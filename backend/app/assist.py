"""Before a visit, and Ask about my health.

- Warning signs are found by code, from a fixed list of patterns, before any model call. A match
  means emergency care; the model is never asked whether something is an emergency.
- Which kind of doctor: a fixed list of kinds in code. The model picks one key and a reason;
  anything not on the list becomes general physician.
- The summary for the doctor is assembled by code from what the patient typed and their confirmed
  record. The model only splits the typed words into symptoms (each with an exact quote that code
  checks), picks the kind of doctor, and suggests up to three questions.
- Ask: the model turns the question into at most 8 general search words (code checks them: no
  digits, no part of the patient's name), Tavily searches a fixed list of trusted sites, the model
  writes points that each carry a source and an exact quote, and code keeps a point only if the
  quote is in that source. Points that tell the reader to start, stop or skip a medicine, or that
  name a dose, are dropped. Nothing from the record goes to a search.
- Search results are kept 30 days per query. Each account may ask 20 questions a day.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, Field

from app.ai.client import AIClient
from app.ai.search import SearchResult, WebSearch
from app.medicine_info import DIRECTIVE, NOT_FOR_PATIENTS
from app.pipeline import prompts
from app.pipeline.quotes import quote_in_source
from app.schemas.assist import (
    AskAnswer,
    AskPoint,
    PrepRecord,
    PrepSymptom,
    VisitPrep,
)
from app.schemas.patient import Patient
from app.schemas.record import (
    SHOWN_ON_CARD,
    Encounter,
    Entry,
    EntryKind,
    EntryStatus,
    MedicationState,
)
from app.storage import Storage
from app.views import consolidate, numbers, timeline, visit_dates, visit_uploaded

# ---- warning signs (code, never the model) ------------------------------------------------
# Plain English labels. Patterns cover English and common Hinglish (Hindi in Latin letters).
_I = re.IGNORECASE
_SIGNS: list[tuple[str, re.Pattern[str]]] = [
    ("Chest pain or pressure", re.compile(
        r"chest\s+(pain|pressure|tightness|discomfort)|"
        r"(pain|pressure|tightness|discomfort)\s+(in|on)\s+(my\s+|the\s+|his\s+|her\s+)?chest|"
        r"(seene|chhati|chaati)\s+(mein|me|main)\s+(dard|jalan|bhaari|bhari|dabav)|"
        r"chest\s+(mein|me|main)\s+(dard|bhaari|bhari)|heart\s+attack|"
        r"chest\s+(feels?\s+|is\s+|has\s+been\s+)?(tight|heavy|hurting|hurts|painful)", _I)),
    ("Trouble breathing", re.compile(
        r"(trouble|difficulty|struggling|hard|hardness)\s+(in\s+|with\s+)?breath|"
        r"short(ness)?\s+of\s+breath|can'?t\s+breathe|cannot\s+breathe|unable\s+to\s+breathe|"
        r"breathless|gasping|not\s+able\s+to\s+breathe|breathing\s+difficulty|"
        r"not\s+breathing|(lips?|face)\s+(is\s+|are\s+)?(turning|going|gone)\s+blue|blue\s+lips|"
        r"(saans|sans|saas)\s+(lene\s+(mein|me|main)\s+)?(takleef|dikkat|phool|nahi\s+aa)|"
        r"(saans|sans)\s+phool|dam\s+ghut", _I)),
    ("Fainting or passing out", re.compile(
        r"\b(fainted|fainting|passed\s+out|passing\s+out|blacked\s+out|lost\s+consciousness|"
        r"unconscious|collapsed)\b|behosh|\b(felt|feel|feels|feeling)\s+(very\s+)?faint\b", _I)),
    ("Face drooping", re.compile(
        r"face\s+(is\s+)?(drooping|drooped|droops|sagging|crooked|lopsided)|drooping\s+face|"
        r"facial\s+droop|(mouth|lip)\s+(is\s+)?(drooping|crooked|twisted)|"
        r"(chehra|chehre|munh|muh)\s+(tedha|tedhi|latak)", _I)),
    ("Slurred speech or trouble speaking", re.compile(
        r"slurred\s+speech|slurring|speech\s+(is\s+)?slurred|can'?t\s+speak|cannot\s+speak|"
        r"trouble\s+speaking|difficulty\s+speaking|"
        r"(zubaan|zuban|jeebh)\s+(ladkhada|latak|atak)|bolne\s+(mein|me|main)\s+(dikkat|takleef)",
        _I)),
    ("Weakness or numbness on one side", re.compile(
        r"(weakness|numbness|paralysis)\s+(on|in|of)\s+(one|the\s+left|the\s+right|my\s+left|"
        r"my\s+right|half)|one[- ]sided\s+(weakness|numbness)|"
        r"(one\s+side|left\s+side|right\s+side)(\s+of\s+(my|the)\s+body)?\s+(is\s+|has\s+)?"
        r"(weak|numb|paralys\w*|gone\s+limp)|paralysis|paralysed|paralyzed|"
        r"(aadha|adha)\s+(sharir|shareer)|ek\s+(taraf|side)\s+(ki\s+|ka\s+)?(kamzori|sunn)|lakwa",
        _I)),
    ("Seizure", re.compile(
        r"\bseizures?\b|\bconvulsions?\b|convulsing|"
        r"\b(having|had|got|getting)\s+(a\s+)?(fit|fits)\b|\bdaura\s+pad|\bmirgi\b", _I)),
    ("Severe bleeding", re.compile(
        r"(severe|heavy|profuse|uncontrolled|non[- ]?stop)\s+bleeding|"
        r"bleeding\s+(a\s+lot|heavily|won'?t\s+stop|will\s+not\s+stop|not\s+stopping)|"
        r"can(?:'?t|\s*not)\s+stop\s+(the\s+)?bleeding|bahut\s+(zyada\s+)?khoon|"
        r"khoon\s+(nahi\s+ruk|band\s+nahi)", _I)),
    ("Coughing or vomiting blood", re.compile(
        r"(coughing|cough|coughed|vomiting|vomit|vomited|throwing|threw|spitting|spit)\s+"
        r"(up\s+)?(some\s+|a\s+lot\s+of\s+)?blood|blood\s+in\s+(my\s+)?(vomit|sputum|cough)|"
        r"(khoon|khun)\s+(ki\s+)?(ulti|khansi)|ulti\s+(mein|me|main)\s+(khoon|khun)|"
        r"khansi\s+(mein|me|main)\s+(khoon|khun)|hematemesis|haematemesis|haemoptysis|"
        r"hemoptysis", _I)),
    ("Swelling of the face or throat", re.compile(
        r"(swelling|swollen)\s+(of\s+|in\s+|on\s+)?(the\s+|my\s+)?(face|lips?|tongue|throat)|"
        r"(face|lips?|tongue|throat)\s+(is\s+|are\s+|has\s+|have\s+)?(swelling|swollen|"
        r"closing)|(gale|gala|chehre|hont|jeebh)\s+(mein\s+|par\s+|pe\s+|ki\s+)?sujan", _I)),
    ("Thoughts of self-harm", re.compile(
        r"suicid|kill\s+myself|killing\s+myself|end\s+my\s+life|want\s+to\s+die|"
        r"(don'?t|do\s+not)\s+want\s+to\s+live|no\s+reason\s+to\s+live|better\s+off\s+dead|"
        r"self[- ]harm|hurt\s+myself|harm\s+myself|take\s+my\s+own\s+life|"
        r"marna\s+chah(ta|ti)|jaan\s+de(na|ne)|khudkushi|aatmahatya", _I)),
    ("Sudden, very bad headache", re.compile(
        r"worst\s+headache|thunderclap\s+headache|"
        r"sudden\s+(severe|terrible|excruciating|very\s+bad)\s+headache", _I)),
    ("Severe head injury", re.compile(
        r"(severe|serious|major|bad)\s+head\s+(injury|trauma)|"
        r"hit\s+(my|his|her|the)\s+head\s+(hard|badly)|head\s+injury\s+(with|and)\s+"
        r"(vomit|faint|drows|confus|bleed)|"
        r"(sar|sir)\s+(par|pe|mein|me)\s+(gehri|badi|zor\s+ki)\s+chot", _I)),
    ("Bleeding in pregnancy", re.compile(
        r"(pregnan|garbh)\w*\W+(\w+\W+){0,6}?(bleed|blood|khoon|khun)|"
        r"(bleed|blood|khoon|khun)\w*\W+(\w+\W+){0,6}?(pregnan|garbh)", _I)),
]
# "High fever with a stiff neck" needs both.
_STIFF_NECK = re.compile(r"stiff\s+neck|neck\s+(is\s+)?stiff|neck\s+stiffness|"
                         r"(gardan|gardn)\s+(akad|jakad|nahi\s+mud)", _I)
_FEVER = re.compile(r"\b(high\s+)?(fever|temperature)\b|bukhar|bukhaar", _I)
_SIGN_STIFF_NECK = "High fever with a stiff neck"

_SELF_HARM = "Thoughts of self-harm"
HELPLINE = "Tele-MANAS: call 14416, free, any time."
_CLAUSE_BREAK = re.compile(r"[.;!?\n,:]|\b(and|aur|but|lekin|magar|however)\b", _I)
_NEG_WORDS = {"no", "not", "nahi", "nahin", "nhi", "zero", "denies", "denied", "don't", "doesn't",
              "didn't", "hasn't", "haven't", "isn't", "aren't", "wasn't", "weren't"}
_NOT_FILLER = {"only", "just", "also", "even", "merely"}
_NAHI_END = re.compile(r"\s*(nahi|nahin|nhi)(\s+(hai|hain|h|he))?\s*", _I)


def _negated(text: str, start: int, end: int) -> bool:
    """A match like "no chest pain" or "chest pain nahi hai". A negation counts only when it sits
    directly in front of the sign (at most one filler word between), or when "nahi hai" closes
    the clause right after it. A miss (a real warning sign called absent) is worse than a false
    alarm, so "never had chest pain", "can't walk without chest pain" and "chest pain that will
    not go away" all count as present."""
    before = text[:start]
    breaks = list(_CLAUSE_BREAK.finditer(before))
    if breaks:
        before = before[breaks[-1].end():]
    words = [w.casefold().replace("\u2019", "'") for w in re.findall(r"[\w'\u2019]+", before)]
    if words and words[-1] in _NEG_WORDS:
        return True
    if len(words) >= 2 and words[-2] in _NEG_WORDS and words[-1] not in _NOT_FILLER:
        return True
    after = text[end:]
    cut = _CLAUSE_BREAK.search(after)
    if cut:
        after = after[:cut.start()]
    return bool(after.strip()) and bool(_NAHI_END.fullmatch(after))


def _found(pattern: re.Pattern[str], text: str) -> bool:
    return any(not _negated(text, m.start(), m.end()) for m in pattern.finditer(text))


def warning_signs(text: str) -> list[str]:
    """Plain-English labels of the warning signs the text mentions (empty: none). Any hit means
    emergency care; this is decided here, before any model sees the text."""
    out = [label for label, pattern in _SIGNS if _found(pattern, text)]
    if _found(_STIFF_NECK, text) and _FEVER.search(text):
        out.append(_SIGN_STIFF_NECK)
    return out


# ---- which doctor ---------------------------------------------------------------------------
SPECIALTIES = {
    "general_physician": "General physician",
    "ent": "ENT (ear, nose, throat)",
    "skin": "Skin specialist (dermatologist)",
    "heart": "Heart specialist (cardiologist)",
    "lungs": "Chest and lungs (pulmonologist)",
    "stomach": "Stomach and digestion (gastroenterologist)",
    "bones": "Bones and joints (orthopaedic)",
    "nerves": "Brain and nerves (neurologist)",
    "eyes": "Eye specialist (ophthalmologist)",
    "women": "Women's health (gynaecologist)",
    "children": "Children's doctor (paediatrician)",
    "mind": "Mental health (psychiatrist)",
    "urinary": "Kidney and urinary (urologist)",
    "hormones": "Diabetes and hormones (endocrinologist)",
    "dentist": "Dentist",
}
FALLBACK_REASON = "This kind of doctor can look into what you describe."
URGENT_NOTE = "Call 112 or go to the nearest emergency department."
MAX_QUESTIONS = 3
RECENT_DAYS = 365

# A dose or an amount of a medicine ("500 mg", "twice daily"): never in an answer or a question.
DOSE = re.compile(
    r"\b\d+(\.\d+)?\s*(mg|mcg|µg|ml|iu|g|gm|grams?|units?|tablets?|tabs?|capsules?|caps?|pills?|"
    r"drops?|puffs?)\b|"
    r"\b(once|twice|thrice)\s+(a\s+day|daily|per\s+day)\b|\b\d+\s+times\s+(a\s+day|daily)\b|"
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(tablets?|capsules?|pills?)\s+"
    r"((a|per|each|every)\s+day|daily)\b|\bgrams?\b",
    re.IGNORECASE,
)
# Instructions about a medicine: never in an answer, a reason, a question or a symptom line.
INSTRUCTION = re.compile(
    r"\b(stop|start|skip|discontinue|quit|stopping|starting|skipping|discontinuing)\b|"
    r"\b(do\s+not|don'?t|should\s+not|shouldn'?t|must\s+not|never)\s+take\b|"
    r"\bavoid\s+taking\b|"
    r"\b(increase|reduce|lower|raise|decrease|double|halve|change)\s+"
    r"(your\s+|the\s+|his\s+|her\s+)?dos(e|age)\b|\bdos(e|es|age)\b",
    re.IGNORECASE,
)


class _Symptom(BaseModel):
    text: str
    quote: str


class _Prep(BaseModel):
    symptoms: list[_Symptom] = Field(default_factory=list)
    specialty: str = "general_physician"
    reason: str = ""
    questions: list[str] = Field(default_factory=list)


def _specialty_prompt() -> str:
    return prompts.PREPARE_VISIT + "\n".join(f"- {k}: {v}" for k, v in SPECIALTIES.items())


def _latest(entries: list[Entry], encounters: list[Encounter], kind: EntryKind) -> list[Entry]:
    confirmed = [e for e in entries
                 if e.status == EntryStatus.CONFIRMED]
    facts = consolidate(confirmed, visit_dates(encounters), visit_uploaded(encounters))
    return [f.latest for f in facts if f.kind == kind]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        if item.strip() and item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item.strip())
    return out


def _g(value: float) -> str:
    return f"{value:g}"


def record_for(entries: list[Entry], encounters: list[Encounter],
               today: date | None = None) -> PrepRecord:
    """The confirmed record, as short lines for the summary. Code only."""
    today = today or date.today()
    allergies = [e.label for e in _latest(entries, encounters, EntryKind.ALLERGY)]
    medicines = []
    for e in _latest(entries, encounters, EntryKind.MEDICATION):
        state = e.medication.state(today) if e.medication else MedicationState.END_UNKNOWN
        if state in SHOWN_ON_CARD:
            medicines.append(e.label)
    conditions = [e.label for e in _latest(entries, encounters, EntryKind.DIAGNOSIS)]
    confirmed = [e for e in entries
                 if e.status == EntryStatus.CONFIRMED]
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
    events = [f"{e.label} ({e.occurred_on})" for e in timeline(confirmed)
              if e.occurred_on and e.occurred_on >= cutoff[:len(e.occurred_on)]]
    out_of_range = []
    for group in numbers(entries, encounters).groups:
        for s in group.series:
            p = s.latest
            if p is None or p.position not in ("low", "high") or p.range is None:
                continue
            low, high = p.range.low, p.range.high
            printed = (f"{_g(low)} to {_g(high)}" if low is not None and high is not None
                       else f"up to {_g(high)}" if high is not None else f"from {_g(low or 0)}")
            unit = f" {p.unit}" if p.unit else ""
            out_of_range.append(f"{s.name} {_g(p.value)}{unit} on {p.on.isoformat()}: "
                                f"{p.position} (printed range {printed})")
    return PrepRecord(allergies=_unique(allergies), medicines=_unique(medicines),
                      conditions=_unique(conditions), events=_unique(events),
                      out_of_range=_unique(out_of_range))


def _record_text(patient: Patient, record: PrepRecord, today: date) -> str:
    who = []
    if patient.birth_year:
        who.append(f"age about {today.year - patient.birth_year}")
    if patient.sex:
        who.append(f"sex {patient.sex}")
    lines = [f"Patient: {', '.join(who) or 'age and sex not known'}"]
    for label, items in (("Allergies", record.allergies), ("Current medicines", record.medicines),
                         ("Conditions", record.conditions),
                         ("Recent events", record.events)):
        lines.append(f"{label}: {'; '.join(items) or 'none in the record'}")
    return "\n".join(lines)


def _clean_sentence(text: str) -> bool:
    return (bool(text.strip()) and not DIRECTIVE.search(text) and not INSTRUCTION.search(text)
            and not DOSE.search(text))


def prepare_visit(patient: Patient, entries: list[Entry], encounters: list[Encounter],
                  text: str, since: str | None, ai: AIClient,
                  today: date | None = None) -> VisitPrep:
    """The saved-to-be summary. The model splits the typed words into symptoms, picks a kind of
    doctor and suggests questions; everything else is code. May raise AIError."""
    today = today or date.today()
    signs = warning_signs(text)
    record = record_for(entries, encounters, today)
    used = getattr(ai, "used", [])  # which model answered (a backup is marked)
    used.clear()
    since_line = f"Since when: {since}\n" if since else ""
    answer = ai.reason_json(
        _specialty_prompt(),
        f"{_record_text(patient, record, today)}\n\n{since_line}What the patient typed:\n{text}",
        _Prep, max_tokens=4096, thinking=False, temperature=0.0)

    symptoms: list[PrepSymptom] = []
    for s in answer.symptoms:
        if _clean_sentence(s.text) and quote_in_source(s.quote, text) and not any(
                k.text.casefold() == s.text.strip().casefold() for k in symptoms):
            symptoms.append(PrepSymptom(text=s.text.strip(), quote=s.quote))
    key = answer.specialty.strip() if answer.specialty.strip() in SPECIALTIES \
        else "general_physician"
    reason = answer.reason.strip()
    if not reason or not _clean_sentence(reason):
        reason = FALLBACK_REASON
    questions = _unique([q for q in answer.questions if _clean_sentence(q)])[:MAX_QUESTIONS]
    return VisitPrep(patient_id=patient.id, text=text, since=since or None, urgent=bool(signs),
                     warning_signs=signs, helpline=HELPLINE if _SELF_HARM in signs else None,
                     symptoms=symptoms, specialty=key,
                     specialty_label=SPECIALTIES[key], reason=reason, questions=questions,
                     record=record, models_used=sorted(set(used)))


# ---- Ask about my health --------------------------------------------------------------------
HEALTH_SITES = {"medlineplus.gov": "MedlinePlus", "nhs.uk": "NHS",
                "mayoclinic.org": "Mayo Clinic", "who.int": "WHO", "cdc.gov": "CDC"}
HEALTH_DOMAINS = list(HEALTH_SITES)
ASKS_PER_DAY = 20
SEARCH_CACHE_DAYS = 30
MAX_SEARCH_WORDS = 8
MAX_ASK_POINTS = 5
SEARCH_SUFFIX = " diet health"
LIMIT_MESSAGE = f"You've asked {ASKS_PER_DAY} questions today. Try again tomorrow."
NOT_HEALTH_NOTE = "That doesn't look like a health question. Ask about food, diet or daily habits and your health."
GENERAL_WORDS_NOTE = "Try asking in more general words."
NO_ANSWER_NOTE = ("The trusted health sites did not give a clear answer to this. Try asking in "
                  "other words, or ask your doctor.")
ASK_NOTE = ("General information from trusted health sites, not advice for you. Ask your doctor "
            "before changing your diet or medicines.")


class DailyLimit(Exception):
    """The account has used its questions for today."""


PREPS_PER_DAY = 20
PREP_LIMIT_MESSAGE = f"You've made {PREPS_PER_DAY} summaries today. Try again tomorrow."


def reserve_prep(db: Storage, account_id: str, now: datetime | None = None) -> None:
    """Count one summary against the account's rolling 24 hours (kept in ask_log under its own
    key). Raises DailyLimit when the 20 are used."""
    now = now or datetime.now(UTC)
    key = f"prep:{account_id}"
    if db.count_asks(key, now - timedelta(days=1)) >= PREPS_PER_DAY:
        raise DailyLimit(PREP_LIMIT_MESSAGE)
    db.record_ask(key, now)


class _Plan(BaseModel):
    health_question: bool
    search_words: str = ""


class _AskPoint(BaseModel):
    text: str
    source: int
    quote: str


class _Answer(BaseModel):
    points: list[_AskPoint] = Field(default_factory=list)


def check_search_words(words: str, patient_name: str) -> bool:
    """The search exception: a few general words may leave the app. At most
    8 words, no digits, no part of the patient's name."""
    words = words.strip()
    if not words or len(words) > 120 or any(c.isdigit() for c in words):
        return False
    tokens = re.findall(r"[^\W\d_]+", words.casefold())
    if not tokens or len(tokens) > MAX_SEARCH_WORDS:
        return False
    name_parts = {p for p in re.findall(r"[^\W\d_]+", patient_name.casefold())
                  if len(p) >= 2 and p != "synthetic"}
    return not name_parts.intersection(tokens)


def _site_name(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/")[0].casefold()
    return next((name for domain, name in HEALTH_SITES.items()
                 if host == domain or host.endswith("." + domain)), host)


def _cache_key(query: str, domains: list[str]) -> str:
    return f"{' '.join(query.casefold().split())}|{','.join(sorted(domains))}"


def cached_search(query: str, db: Storage, search: WebSearch,
                  now: datetime) -> tuple[list[SearchResult], bool]:
    """The results, and whether a real search was made (a saved one costs no credit)."""
    key = _cache_key(query, HEALTH_DOMAINS)
    saved = db.get_search_cache(key, now - timedelta(days=SEARCH_CACHE_DAYS))
    if saved is not None:
        return saved, False
    results = search.search(query, HEALTH_DOMAINS, max_results=6)
    if results:  # an empty answer should not stick for 30 days
        db.save_search_cache(key, results, now)
    return results, True


def _sources_block(results: list[SearchResult]) -> str:
    return "\n\n".join(f"[{i}] {r.title} ({r.url})\n{r.content}" for i, r in enumerate(results, 1))


def _name_in(text: str, name: str) -> bool:
    return bool(re.search(rf"\b{re.escape(name.casefold())}s?\b", text.casefold()))


_ALLERGY_FILLER = re.compile(r"\b(allerg\w*|to|drugs?|medicines?)\b", re.IGNORECASE)


def allergy_notes(allergies: list[str], question: str) -> list[str]:
    notes = []
    for label in allergies:
        name = " ".join(_ALLERGY_FILLER.sub(" ", label).split())
        if len(name) >= 3 and _name_in(question, name):
            notes.append(f"Your record lists an allergy to {name}.")
    return _unique(notes)


# Everyday words for a condition, so "salt high blood pressure" finds "Hypertension".
CONDITION_WORDS = {
    "hypertension": ["blood pressure"],
    "diabet": ["blood sugar", "glucose"],
    "thyroid": ["thyroid"],
    "lipid": ["cholesterol"],
    "cholesterol": ["cholesterol"],
    "gerd": ["acidity", "reflux"],
    "reflux": ["acidity", "reflux"],
    "asthma": ["asthma"],
}


def considered_conditions(conditions: list[str], words: str) -> list[str]:
    """Which of the patient's conditions the search words mention (whole name, or one of its
    longer words, so "diabetes" finds "Type 2 diabetes")."""
    wanted = set(re.findall(r"[^\W\d_]+", words.casefold()))
    out = []
    for c in conditions:
        parts = [p for p in re.findall(r"[^\W\d_]+", c.casefold()) if len(p) >= 5]
        low = c.casefold()
        aliases = [a for key, names in CONDITION_WORDS.items() if key in low for a in names]
        if (_name_in(words, c) or wanted.intersection(parts)
                or any(_name_in(words, a) for a in aliases)):
            out.append(c)
    return _unique(out)


def ask(patient: Patient, entries: list[Entry], encounters: list[Encounter], question: str,
        account_id: str, db: Storage, ai: AIClient, search: WebSearch,
        now: datetime | None = None) -> AskAnswer:
    """The steps of plan section 2. May raise DailyLimit, AIError or SearchError."""
    now = now or datetime.now(UTC)
    signs = warning_signs(question)
    if signs:  # no limit used, no model, no search
        return AskAnswer(urgent=True, warning_signs=signs, note=URGENT_NOTE,
                         helpline=HELPLINE if _SELF_HARM in signs else None)
    if db.count_asks(account_id, now - timedelta(days=1)) >= ASKS_PER_DAY:
        raise DailyLimit(LIMIT_MESSAGE)
    db.record_ask(account_id, now)

    used = getattr(ai, "used", [])
    used.clear()
    plan = ai.reason_json(prompts.ASK_SEARCH, f"Question: {question}", _Plan, max_tokens=1024,
                          thinking=False, temperature=0.0)
    if not plan.health_question:
        return AskAnswer(health_question=False, note=NOT_HEALTH_NOTE,
                         models_used=sorted(set(used)))
    words = " ".join(plan.search_words.split())
    if not check_search_words(words, patient.name):
        return AskAnswer(note=GENERAL_WORDS_NOTE, models_used=sorted(set(used)))

    record = record_for(entries, encounters, now.date())
    results, searched = cached_search(words + SEARCH_SUFFIX, db, search, now)
    results = [r for r in results if not NOT_FOR_PATIENTS.search(r.url)]

    def models() -> list[str]:
        return [*sorted(set(used)), *(["tavily:search"] if searched else [])]

    base = AskAnswer(allergy_notes=allergy_notes(record.allergies, question),
                     considered=considered_conditions(record.conditions, words), note=ASK_NOTE)
    if not results:
        base.note = NO_ANSWER_NOTE
        base.models_used = models()
        return base

    facts = "\n".join(f"{label}: {'; '.join(items) or 'none in the record'}" for label, items in (
        ("Conditions", record.conditions), ("Allergies", record.allergies),
        ("Current medicines", record.medicines)))
    answer = ai.reason_json(
        prompts.ASK_ANSWER,
        f"Question: {question}\n\nFrom the patient's record:\n{facts}\n\n"
        f"Sources:\n{_sources_block(results)}", _Answer, max_tokens=4096, thinking=False,
        temperature=0.0)
    for p in answer.points:
        text = p.text.strip()
        if not (1 <= p.source <= len(results)) or not _clean_sentence(text):
            continue
        r = results[p.source - 1]
        if not quote_in_source(p.quote, r.content):
            continue
        if any(k.text.casefold() == text.casefold() for k in base.points):
            continue
        if len(base.points) < MAX_ASK_POINTS:
            base.points.append(AskPoint(text=text, quote=p.quote, site=_site_name(r.url),
                                        title=r.title, url=r.url))
    if not base.points:
        base.note = NO_ANSWER_NOTE
    base.models_used = models()
    return base
