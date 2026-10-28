"""Before a visit, and Ask about my health.

- Warning signs are found by code, from a fixed list of patterns, before any model call. A match
  means emergency care; the model is never asked whether something is an emergency.
- Which kind of doctor: a fixed list of kinds in code. The model picks one key and a reason;
  anything not on the list becomes general physician.
- The summary for the doctor is assembled by code from what the patient typed and their confirmed
  record. The model only splits the typed words into symptoms (each with an exact quote that code
  checks), picks the kind of doctor, and suggests up to three questions.
- Ask: the model sorts the question (record, general, both, not health). Record questions are
  answered from the patient's confirmed record: the model only picks item ids that code built and
  writes a short summary that code checks. General questions: the model turns the question into at
  most 8 general search words (code checks them: no digits, no part of the patient's name), Tavily
  searches a fixed list of trusted sites, the model writes points that each carry a source and an
  exact quote, and code keeps a point only if the quote is in that source. Points, summaries and
  follow-up questions that tell the reader to start, stop or skip a medicine, or that name a
  dose, are dropped. Nothing from the record goes to a search. AI summaries are marked as such,
  at most 2 sentences and 300 characters.
- Search results are kept 30 days per query. Each account may ask 20 questions a day.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

from pydantic import BaseModel, Field

from app.ai.client import AIClient, AIError
from app.ai.search import SearchResult, WebSearch
from app.medicine_info import DIRECTIVE, NOT_FOR_PATIENTS
from app.pipeline import prompts
from app.pipeline.quotes import quote_in_source
from app.schemas.assist import (
    AskAnswer,
    AskChart,
    AskPoint,
    AskPrevious,
    AskSource,
    FollowUpQuestion,
    OutOfRangeTest,
    PrepAnswer,
    PrepRecord,
    PrepSymptom,
    QuestionsResponse,
    RecordItem,
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
from app.views import (
    NumberSeries,
    consolidate,
    numbers,
    timeline,
    visit_dates,
    visit_uploaded,
)

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
    since: str | None = None
    since_quote: str | None = None


class _FollowUp(BaseModel):
    text: str
    choices: list[str] = Field(default_factory=list)


class _FollowUps(BaseModel):
    questions: list[_FollowUp] = Field(default_factory=list)


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
    tests_out: list[OutOfRangeTest] = []
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
            if not any(t.name == s.name and t.date == p.on.isoformat() for t in tests_out):
                tests_out.append(OutOfRangeTest(
                    name=s.name, value=p.value, unit=p.unit or "", date=p.on.isoformat(),
                    direction=p.position, range_text=printed))
    return PrepRecord(allergies=_unique(allergies), medicines=_unique(medicines),
                      conditions=_unique(conditions), events=_unique(events),
                      out_of_range=_unique(out_of_range), tests_out_of_range=tests_out)


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


# Wider than INSTRUCTION: advice about a medicine in the AI summaries and follow-up questions.
_MEDICINE_ADVICE = re.compile(
    r"\b(keep|continue|carry\s+on)\s+taking\b|\byou\s+should\s+take\b|"
    r"\btake\s+\w+\s+with\b|"
    r"\b(increase|reduce|lower|raise|decrease)\s+(your|the)\s+\w+|\bswitch\s+to\b|"
    r"\bchange\s+(your|the)\s+(medicines?|medications?|tablets?|pills?)\b|"
    r"\bhave\s+you\s+tried\b", re.IGNORECASE)
_YOU_ORDERS = re.compile(r"\byou\s+(should|must)\b", re.IGNORECASE)
MAX_SUMMARY_CHARS = 300
MAX_SUMMARY_SENTENCES = 2
MAX_FOLLOW_UP_CHARS = 200


def _medicine_names(labels: list[str]) -> list[str]:
    names = []
    for label in labels:
        for word in re.findall(r"[^\W\d_]+", label):
            if len(word) >= 3 and word.casefold() not in ("tablet", "tablets", "capsule", "syrup"):
                names.append(word)
                break
    return names


def _advice_free(text: str, medicines: list[str] | None = None) -> bool:
    if not _clean_sentence(text) or _MEDICINE_ADVICE.search(text):
        return False
    return not any(re.search(rf"\bavoid\b\W+(\w+\W+){{0,3}}?{re.escape(n)}", text, re.IGNORECASE)
                   for n in _medicine_names(medicines or []))


def _summary_ok(text: str, medicines: list[str] | None = None) -> bool:
    """An AI summary: at most 2 sentences and 300 characters, free of advice about medicines."""
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x.strip(" .!?")]
    return (len(text) <= MAX_SUMMARY_CHARS and len(sentences) <= MAX_SUMMARY_SENTENCES
            and not _YOU_ORDERS.search(text) and _advice_free(text, medicines))


_YES = re.compile(r"^\W*(yes|yeah|yep|yup|haan|han|haa|ha|a\s+little|a\s+bit|sometimes|somewhat|"
                  r"slightly|kabhi\s+kabhi)\b", re.IGNORECASE)
_NO = re.compile(r"^\W*(no|nope|nah|never|none|not\s+really|nahi|nahin|nhi)\b", re.IGNORECASE)


def _answers_clean(answers: list[PrepAnswer] | None) -> list[PrepAnswer]:
    return [PrepAnswer(question=a.question.strip(), answer=a.answer.strip())
            for a in (answers or []) if a.answer.strip()][:MAX_QUESTIONS]


def _signs_in(text: str, answers: list[PrepAnswer]) -> list[str]:
    """Warning signs in what was typed AND in each answer, in order, no repeats."""
    # Every way the words can add up: the typed text and the answers joined; a short "yes" to a
    # question that names a sign counts as that sign (the question goes in as a statement, so
    # "stiff neck" plus yes to "Is there a fever?" is both); any other answer is read together
    # with its question. A "no" never adds anything.
    parts = [text, *(a.answer for a in answers)]
    for a in answers:
        if _YES.match(a.answer):
            parts.append(a.question.rstrip(" ?"))
        elif not _NO.match(a.answer):
            parts.append(f"{a.question} {a.answer}")
    out = warning_signs(text)
    for chunk in (". ".join(parts), *parts[1:]):
        out += [x for x in warning_signs(chunk) if x not in out]
    return out


def prepare_visit(patient: Patient, entries: list[Entry], encounters: list[Encounter],
                  text: str, since: str | None, ai: AIClient,
                  today: date | None = None,
                  answers: list[PrepAnswer] | None = None) -> VisitPrep:
    """The saved-to-be summary. The model splits the typed words into symptoms, picks a kind of
    doctor, reads how long from the words and suggests questions; everything else is code. May
    raise AIError."""
    today = today or date.today()
    answers = _answers_clean(answers)
    signs = _signs_in(text, answers)
    record = record_for(entries, encounters, today)
    used = getattr(ai, "used", [])  # which model answered (a backup is marked)
    used.clear()
    since_line = f"Since when: {since}\n" if since else ""
    answers_block = ("\n\nThe patient's answers to follow-up questions:\n"
                     + "\n".join(f"- {a.question}: {a.answer}" for a in answers)) if answers else ""
    answer = ai.reason_json(
        _specialty_prompt(),
        f"{_record_text(patient, record, today)}\n\n{since_line}What the patient typed:\n{text}"
        f"{answers_block}",
        _Prep, max_tokens=4096, thinking=False, temperature=0.0)

    # What the patient said: the typed words and the answers they gave.
    said = "\n".join([text, *(a.answer for a in answers)])
    symptoms: list[PrepSymptom] = []
    for s in answer.symptoms:
        if _clean_sentence(s.text) and quote_in_source(s.quote, said) and not any(
                k.text.casefold() == s.text.strip().casefold() for k in symptoms):
            symptoms.append(PrepSymptom(text=s.text.strip(), quote=s.quote))
    key = answer.specialty.strip() if answer.specialty.strip() in SPECIALTIES \
        else "general_physician"
    reason = answer.reason.strip()
    if not reason or not _clean_sentence(reason):
        reason = FALLBACK_REASON
    questions = _unique([q for q in answer.questions if _clean_sentence(q)])[:MAX_QUESTIONS]
    when = (since or "").strip() or None  # an old client may still send it
    if when is None and answer.since and answer.since_quote:
        candidate = " ".join(answer.since.split())
        if (len(candidate) <= 80 and _clean_sentence(candidate)
                and quote_in_source(answer.since_quote, said)):
            when = candidate
    return VisitPrep(patient_id=patient.id, text=text, since=when, urgent=bool(signs),
                     warning_signs=signs, helpline=HELPLINE if _SELF_HARM in signs else None,
                     symptoms=symptoms, specialty=key,
                     specialty_label=SPECIALTIES[key], reason=reason, questions=questions,
                     answers=answers, record=record, models_used=sorted(set(used)))


MAX_CHOICES = 4
MAX_CHOICE_CHARS = 40


def prepare_questions(text: str, ai: AIClient) -> QuestionsResponse:
    """One round of follow-up questions. Warning signs first, by code: when there are any, no
    model is called. A model failure gives no questions (the patient just goes on)."""
    signs = warning_signs(text)
    if signs:
        return QuestionsResponse(urgent=True, warning_signs=signs,
                                 helpline=HELPLINE if _SELF_HARM in signs else None)
    used = getattr(ai, "used", [])
    used.clear()
    try:
        got = ai.reason_json(prompts.PREPARE_QUESTIONS, f"What the patient typed:\n{text}",
                             _FollowUps, max_tokens=2048, thinking=False, temperature=0.0)
    except AIError:
        return QuestionsResponse()
    out: list[FollowUpQuestion] = []
    for q in got.questions:
        t = q.text.strip()
        if (not t or len(t) > MAX_FOLLOW_UP_CHARS or not _advice_free(t)
                or any(o.text.casefold() == t.casefold() for o in out)):
            continue
        choices = _unique([c for c in q.choices
                           if len(c.strip()) <= MAX_CHOICE_CHARS and _advice_free(c)])
        out.append(FollowUpQuestion(id=f"q{len(out) + 1}", text=t, choices=choices[:MAX_CHOICES]))
        if len(out) == MAX_QUESTIONS:
            break
    return QuestionsResponse(questions=out)


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


QUESTIONS_PER_DAY = 40
QUESTIONS_LIMIT_MESSAGE = (f"You've asked for questions {QUESTIONS_PER_DAY} times today. "
                           "Try again tomorrow.")


def reserve_questions(db: Storage, account_id: str, now: datetime | None = None) -> None:
    """Count one round of follow-up questions (rolling 24 hours, key `prepq:<account id>`)."""
    now = now or datetime.now(UTC)
    key = f"prepq:{account_id}"
    if db.count_asks(key, now - timedelta(days=1)) >= QUESTIONS_PER_DAY:
        raise DailyLimit(QUESTIONS_LIMIT_MESSAGE)
    db.record_ask(key, now)


class _Plan(BaseModel):
    kind: str = "general"  # record | general | both | not_health
    search_words: str = ""
    # The question written out in full when it is a short follow-up ("check again"); else "".
    full_question: str = ""


MAX_FULL_QUESTION_CHARS = 200


def _full_question(text: str, previous: AskPrevious | None) -> str:
    """The model's full wording of a short follow-up, cleaned by code: one line, at most 200
    characters, and only when there is a previous question to lean on. It never reaches the screen."""
    if previous is None or not previous.question.strip():
        return ""
    clean = " ".join(text.split())
    return clean[:MAX_FULL_QUESTION_CHARS].rstrip()


class _RecordAnswer(BaseModel):
    item_ids: list[str] = Field(default_factory=list)
    summary: str = ""


class _Summary(BaseModel):
    summary: str = ""


class _AskPoint(BaseModel):
    text: str
    source: int
    quote: str
    category: str = "good_to_know"


class _Answer(BaseModel):
    points: list[_AskPoint] = Field(default_factory=list)


_LABEL_FILLER = {"type", "high", "low", "with", "from", "tablet", "tablets", "capsule", "capsules",
                 "syrup", "chronic", "acute", "mild", "severe", "disease", "disorder"}


def _record_words(labels: list[str]) -> set[str]:
    """The words of the patient's medicine and condition labels (4 letters or more)."""
    return {t for label in labels for t in re.findall(r"[^\W\d_]+", label.casefold())
            if len(t) >= 4 and t not in _LABEL_FILLER}


def check_search_words(words: str, patient_name: str, record_labels: list[str] | None = None,
                       question: str = "") -> bool:
    """The search exception: a few general words may leave the app. At most 8 words, no digits,
    no part of the patient's name, and no word of a medicine or condition in the patient's record
    unless the patient typed that word in this question (so the record never reaches a search
    through the model's guess)."""
    words = words.strip()
    if not words or len(words) > 120 or any(c.isdigit() for c in words):
        return False
    tokens = re.findall(r"[^\W\d_]+", words.casefold())
    if not tokens or len(tokens) > MAX_SEARCH_WORDS:
        return False
    name_parts = {p for p in re.findall(r"[^\W\d_]+", patient_name.casefold())
                  if len(p) >= 2 and p != "synthetic"}
    if name_parts.intersection(tokens):
        return False
    typed = set(re.findall(r"[^\W\d_]+", question.casefold()))
    return not _record_words(record_labels or []).intersection(set(tokens) - typed)


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


def _previous_text(previous: AskPrevious | None) -> str:
    if previous is None or not previous.question.strip():
        return ""
    points = "\n".join(f"- {p}" for p in previous.points if p.strip())
    out = f"Previous question (context only): {previous.question.strip()}\n"
    if points:
        out += f"Its points:\n{points}\n"
    return out + "\n"



_CATEGORIES = ("good_to_know", "be_careful", "your_conditions")


MAX_RECORD_EVENTS = 10
MAX_RECORD_TESTS = 20
MAX_CHARTS = 3
RECORD_NOTE = "From your confirmed record. Ask your doctor before changing your medicines."
RECORD_NONE_NOTE = ("This hasn't been added to your record yet. Add the visit or report it comes "
                    "from, and it will show up here.")
_PER_DAY = {1.0: "once a day", 2.0: "twice a day", 3.0: "three times a day"}


def _tokens(text: str) -> list[str]:
    """Numbers and words, so "40mg" and "40 mg" read the same."""
    return re.findall(r"\d+(?:\.\d+)?|[^\W\d_]+", text.casefold())


def _without_repeats(label: str, parts: list[str]) -> str:
    """The detail line minus what the label already says ("Amlodipine 5 mg" + "5 mg, once a day"
    leaves "once a day"). Whole tokens in a row only: "25mcg" is not in "Thyroxine 125mcg"."""
    own = _tokens(label)

    def said(part: str) -> bool:
        mine = _tokens(part)
        return bool(mine) and any(own[i:i + len(mine)] == mine
                                  for i in range(len(own) - len(mine) + 1))

    return ", ".join(p for p in parts if p and not said(p))


def _numbers_link(group_key: str) -> str:
    return f"/numbers/{quote(group_key, safe='')}"


class _Links:
    """Where each item of the record came from, as app routes. Built from the record by code."""

    def __init__(self, entries: list[Entry], encounters: list[Encounter]) -> None:
        self.by_id = {e.id: e for e in entries}
        self.visible = set(self.by_id)

    def visit(self, entry: Entry | None) -> tuple[str, str]:
        if entry is not None and entry.encounter_id:
            return f"/encounters/{entry.encounter_id}", "Open visit"
        return "/record", "Open My record"

    def report(self, entry_id: str) -> str:
        """`/reports/<test id>` for the report a value was read from; "" when it has none."""
        value = self.by_id.get(entry_id)
        for test_id in (value.links if value is not None else []):
            test = self.by_id.get(test_id)
            if (test is not None and test.kind == EntryKind.TEST and test.id in self.visible
                    and test.status == EntryStatus.CONFIRMED):
                return f"/reports/{test.id}"
        return ""


def _frequency(per_day: float | None) -> str:
    if not per_day:
        return ""
    return _PER_DAY.get(float(per_day), f"{_g(per_day)} times a day")


def record_items_for(entries: list[Entry], encounters: list[Encounter],
                     today: date | None = None) -> list[RecordItem]:
    """The patient's CONFIRMED record as numbered items (r1, r2, ...), built by code: current
    medicines (dose and how often when known), allergies, conditions, recent events and the
    latest value of each test with its range and date. The ids are what the model may point at."""
    today = today or date.today()
    links = _Links(entries, encounters)
    rows: list[dict[str, str]] = []

    def row(kind: str, label: str, detail: str = "", source: Entry | None = None, **more: str):
        link, link_label = links.visit(source)
        rows.append({"kind": kind, "label": label, "detail": detail, "link": link,
                     "link_label": link_label, **more})

    for e in _latest(entries, encounters, EntryKind.MEDICATION):
        m = e.medication
        state = m.state(today) if m else MedicationState.END_UNKNOWN
        if state in SHOWN_ON_CARD:
            detail = _without_repeats(e.label, [(m.dose or "") if m else "",
                                                _frequency(m.doses_per_day) if m else ""])
            row("medicine", e.label, detail, e)
    for e in _latest(entries, encounters, EntryKind.ALLERGY):
        row("allergy", e.label, "", e)
    for e in _latest(entries, encounters, EntryKind.DIAGNOSIS):
        row("condition", e.label, "", e)
    confirmed = [e for e in entries
                 if e.status == EntryStatus.CONFIRMED]
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
    events = [e for e in timeline(confirmed)
              if e.occurred_on and e.occurred_on >= cutoff[:len(e.occurred_on)]]
    for e in events[:MAX_RECORD_EVENTS]:
        row("event", e.label, _without_repeats(e.label, [e.occurred_on or ""]), e)
    tests = 0
    for group in numbers(entries, encounters).groups:
        for series in group.series:
            p = series.latest
            if p is None or tests >= MAX_RECORD_TESTS:
                continue
            unit = f" {p.unit}" if p.unit else ""
            detail = f"{_g(p.value)}{unit} on {p.on.isoformat()}"
            if p.range is not None and (p.range.low is not None or p.range.high is not None):
                low, high = p.range.low, p.range.high
                printed = (f"{_g(low)} to {_g(high)}" if low is not None and high is not None
                           else f"up to {_g(high)}" if high is not None else f"from {_g(low)}")
                detail += f" (printed range {printed}"
                detail += f", {p.position})" if p.position in ("low", "high") else ")"
            report = links.report(p.entry_id)
            source = links.by_id.get(p.entry_id)
            history, direction, position = _facts(series)
            row("test", series.name, detail, source, numbers_link=_numbers_link(group.key),
                group_key=group.key, history=history, direction=direction, position=position)
            if report:  # the report it was read from beats the visit
                rows[-1].update(link=report, link_label="See report")
            elif source is None or not source.encounter_id:
                rows[-1].update(link="", link_label="")  # added by the patient: only the numbers
            tests += 1
    seen: set[tuple[str, str, str]] = set()
    items: list[RecordItem] = []
    for r in rows:
        key = (r["kind"], r["label"].casefold(), r["detail"])
        if not r["label"].strip() or key in seen:
            continue
        seen.add(key)
        items.append(RecordItem(id=f"r{len(items) + 1}", **{**r, "label": r["label"].strip()}))
    return items


_POSITION_WORD = {"high": "above", "low": "below", "within": "within"}


def _facts(series: NumberSeries) -> tuple[str, str, str]:
    """How a measure has gone, for the model and the checks on its summary (never shown): the
    count of readings, the first and the one before the latest, whether the latest is higher,
    lower or the same as the one before, and where it sits against the printed range. All of it
    is decided here; the model only words it. Returns (text, direction, position)."""
    live = [p for p in series.points if not p.removed]
    if not live:
        return "", "", ""
    latest = live[-1]
    parts: list[str] = []
    direction = ""
    if len(live) >= 2:
        first, before = live[0], live[-2]
        direction = ("higher" if latest.value > before.value
                     else "lower" if latest.value < before.value else "the same")
        parts += [f"{len(live)} readings since {first.on.isoformat()}",
                  f"first {_g(first.value)}, the one before the latest {_g(before.value)}",
                  f"the latest is {direction} than the one before" if direction != "the same"
                  else "the latest is the same as the one before"]
    position = _POSITION_WORD.get(latest.position or "", "")
    if position:
        parts.append(f"the latest is {position} the printed range"
                     if position != "within" else "the latest is within the printed range")
    return "; ".join(parts), direction, position


def charts_for(kept: list[RecordItem], entries: list[Entry],
               encounters: list[Encounter]) -> list[AskChart]:
    """The full series of every measure the picked test items belong to (at most 3), straight
    from the patient's numbers. Only picked test items bring a chart; the model never writes one."""
    keys: list[str] = []
    for item in kept:
        if item.kind == "test" and item.group_key and item.group_key not in keys:
            keys.append(item.group_key)
    if not keys:
        return []
    groups = {g.key: g for g in numbers(entries, encounters).groups}
    links = _Links(entries, encounters)
    charts: list[AskChart] = []
    for key in keys:
        group = groups.get(key)
        if group is None:
            continue
        series = []
        for s in group.series:
            live = [p for p in s.points if not p.removed]
            if live:
                series.append(s.model_copy(update={"points": live}))
        if not series:
            continue
        newest = max((p for s in series for p in s.points), key=lambda p: p.on)
        charts.append(AskChart(key=key, name=group.name, series=series,
                               report_link=links.report(newest.entry_id),
                               numbers_link=_numbers_link(key)))
        if len(charts) == MAX_CHARTS:
            break
    return charts


def _record_list(items: list[RecordItem]) -> str:
    return "\n".join(
        f"{i.id}. [{i.kind}] {i.label}{': ' + i.detail if i.detail else ''}"
        f"{' | history: ' + i.history if i.history else ''}" for i in items)  # facts from code


def _record_part(question: str, items: list[RecordItem], ai: AIClient,
                 context: str) -> tuple[list[RecordItem], str | None]:
    """Which record items answer the question, and a short summary from them. Ids the model
    invents are dropped; the items come from the record, never from model text."""
    if not items:
        return [], None
    got = ai.reason_json(
        prompts.ASK_RECORD,
        f"{context}Question: {question}\n\nThe patient's confirmed record:\n{_record_list(items)}",
        _RecordAnswer, max_tokens=2048, thinking=False, temperature=0.0)
    by_id = {i.id: i for i in items}
    kept: list[RecordItem] = []
    for item_id in got.item_ids:
        item = by_id.get(item_id.strip())
        if item is not None and item not in kept:
            kept.append(item)
    if not kept:  # nothing from the record answers it: no summary either
        return [], None
    summary = " ".join(got.summary.split())
    names = [i.label for i in items if i.kind == "medicine"]
    return kept, (summary if summary and _record_summary_ok(summary, kept, names) else None)


_IMPERATIVE = re.compile(r"\W*(please\s+)?(take|use|apply|eat|drink|try|avoid|stop|start|continue|keep|"
                         r"increase|reduce|lower|raise|switch|change|skip|double|halve)\b", re.IGNORECASE)


# Words that judge a value. The report's range is the "printed range"; whether a number is good,
# normal or controlled is for the patient's doctor, never for this summary.
_JUDGING = re.compile(
    r"\b(better|worse|good|bad|improv\w*|worsen\w*|normal|abnormal|controlled|uncontrolled|"
    r"healthy|concerning)\b", re.IGNORECASE)
_CLAUSE = re.compile(r"[;,:]|\b(?:and|but|while|whereas|though|although)\b", re.IGNORECASE)
_DIRECTION_WORD = re.compile(r"\b(higher|lower|the same)\b", re.IGNORECASE)
_POSITION_WORDS = re.compile(r"\b(above|below|within)\b", re.IGNORECASE)
_STOP = {"and", "the", "for", "of", "in", "with", "test", "level", "levels"}


def _names(text: str, item: RecordItem) -> bool:
    """The text names the measure: every word of its name is there ("Blood sugar (fasting)" is
    named by "fasting blood sugar")."""
    wanted = [t for t in _tokens(item.label) if t not in _STOP and not t.isdigit()]
    have = set(_tokens(text))
    return bool(wanted) and all(t in have for t in wanted)


def _words_match_facts(sentences: list[str], kept: list[RecordItem]) -> bool:
    """A direction or range word ("higher", "lower", "above", "below", "within") in a summary must
    be the one the code worked out for the measure it is about. A clause that names a measure is
    about it; one that names none (the "both remain above ..." after an "and") is about the
    measures its sentence names. A word with no code fact behind it is not allowed."""
    tests = [i for i in kept if i.kind == "test"]
    for sentence in sentences:
        in_sentence = [i for i in tests if _names(sentence, i)]
        for clause in _CLAUSE.split(sentence):
            if not clause:
                continue
            named = [i for i in tests if _names(clause, i)] or in_sentence
            for words, fact in ((_DIRECTION_WORD, "direction"), (_POSITION_WORDS, "position")):
                for word in words.findall(clause):
                    if not named:
                        continue  # about no measure of the record: nothing to compare with
                    if not any(getattr(i, fact) == word.casefold() for i in named):
                        return False
    return True


def _record_summary_ok(summary: str, kept: list[RecordItem], medicines: list[str]) -> bool:
    """Reading back the record ("Amlodipine 5 mg once a day") is not advice, so the dose check is
    replaced by this: every number in the summary must be in the kept items. Everything else is
    as for any AI summary (length, sentences, no orders, no instructions about medicines)."""
    record_text = " ".join(f"{i.label} {i.detail} {i.history}" for i in kept)
    record_numbers = set(re.findall(r"\d+(?:\.\d+)?", record_text))
    if any(n not in record_numbers for n in re.findall(r"\d+(?:\.\d+)?", summary)):
        return False
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", summary.strip()) if x.strip(" .!?")]
    if any(_IMPERATIVE.match(s) for s in sentences):  # "Take 500 mg daily." is an order
        return False
    if _JUDGING.search(summary) or not _words_match_facts(sentences, kept):
        return False
    return (len(summary) <= MAX_SUMMARY_CHARS and len(sentences) <= MAX_SUMMARY_SENTENCES
            and not _YOU_ORDERS.search(summary) and not DIRECTIVE.search(summary)
            and not INSTRUCTION.search(summary) and not _MEDICINE_ADVICE.search(summary)
            and not any(re.search(rf"\bavoid\b\W+(\w+\W+){{0,3}}?{re.escape(n)}", summary, re.IGNORECASE)
                        for n in _medicine_names(medicines)))


def _summary_of_points(question: str, points: list[AskPoint], ai: AIClient,
                       medicines: list[str] | None = None) -> str | None:
    """A 2-sentence summary written from the kept points' text only (not the sources)."""
    if not points:
        return None
    try:
        got = ai.reason_json(
            prompts.ASK_SUMMARY,
            f"Question: {question}\n\nPoints:\n" + "\n".join(f"- {p.text}" for p in points),
            _Summary, max_tokens=1024, thinking=False, temperature=0.0)
    except AIError:
        return None
    text = " ".join(got.summary.split())
    return text if text and _summary_ok(text, medicines) else None


# Messages that only ask for the last question again (whole message, any case, punctuation and
# "please" ignored). Decided in code, not by the model: a model sometimes reads "check again" as a
# new question. Includes common Hinglish.
RE_ASK_PHRASES = frozenset({
    "check again", "try again", "again", "repeat", "ask again", "one more time", "same question",
    "refresh", "redo", "phir se", "dobara", "fir se check karo", "phir se check karo",
})


def is_re_ask(text: str) -> bool:
    words = re.findall(r"[\w']+", text.casefold())
    if words and words[0] == "please":
        words = words[1:]
    elif words and words[-1] == "please":
        words = words[:-1]
    return " ".join(words) in RE_ASK_PHRASES


def ask(patient: Patient, entries: list[Entry], encounters: list[Encounter], question: str,
        account_id: str, db: Storage, ai: AIClient, search: WebSearch,
        now: datetime | None = None, previous: AskPrevious | None = None) -> AskAnswer:
    """The steps of plan section 2. May raise DailyLimit, AIError or SearchError."""
    now = now or datetime.now(UTC)
    if previous is not None and previous.question.strip() and is_re_ask(question):
        question, previous = previous.question.strip(), None  # repeat the last question as it was

    def urgent(signs: list[str]) -> AskAnswer:
        return AskAnswer(urgent=True, warning_signs=signs, note=URGENT_NOTE,
                         helpline=HELPLINE if _SELF_HARM in signs else None)

    # A follow-up is also read with the question it leans on ("high fever" then "my neck is stiff").
    prior = previous.question if previous and previous.question.strip() else ""
    signs = warning_signs(question) or (warning_signs(f"{prior} {question}") if prior else [])
    if signs:  # no limit used, no model, no search
        return urgent(signs)
    if db.count_asks(account_id, now - timedelta(days=1)) >= ASKS_PER_DAY:
        raise DailyLimit(LIMIT_MESSAGE)
    db.record_ask(account_id, now)

    used = getattr(ai, "used", [])
    used.clear()
    context = _previous_text(previous)
    plan = ai.reason_json(prompts.ASK_SEARCH, f"{context}Question: {question}", _Plan,
                          max_tokens=1024, thinking=False, temperature=0.0)
    kind = plan.kind.strip().casefold()
    if kind not in ("record", "general", "both", "not_health"):
        kind = "general"
    # A short follow-up ("check again") is read as the question it leans on.
    effective = _full_question(plan.full_question, previous) or question
    if effective != question and (signs := warning_signs(effective)):
        return urgent(signs)  # the model's wording is checked too, by code
    # The typed words and the model's wording both count for an allergen the patient named.
    said = f"{question} {effective}" if effective != question else question
    if kind == "not_health":
        return AskAnswer(kind="not_health", health_question=False, note=NOT_HEALTH_NOTE,
                         models_used=sorted(set(used)))

    out = AskAnswer(kind=kind, note=ASK_NOTE)  # type: ignore[arg-type]
    today = now.date()
    record = record_for(entries, encounters, today)
    searched = False

    def models() -> list[str]:
        return [*sorted(set(used)), *(["tavily:search"] if searched else [])]

    if kind in ("record", "both"):
        try:
            out.record_items, out.record_summary = _record_part(
                effective, record_items_for(entries, encounters, today), ai, context)
            out.charts = charts_for(out.record_items, entries, encounters)
        except AIError:
            if kind == "record":
                raise
        if kind == "record":
            out.note = RECORD_NOTE if out.record_items else RECORD_NONE_NOTE
            out.allergy_notes = allergy_notes(record.allergies, said)
            out.models_used = models()
            return out

    words = " ".join(plan.search_words.split())
    labels = [e.label for kind_ in (EntryKind.MEDICATION, EntryKind.DIAGNOSIS)
              for e in _latest(entries, encounters, kind_)]
    # Words the patient typed (this question, or the one it follows up) may name their own condition.
    typed = f"{question} {previous.question if previous else ''}"
    if not check_search_words(words, patient.name, labels, typed):
        if kind == "both" and out.record_items:  # the record part alone is still an answer
            out.note = RECORD_NOTE
            out.allergy_notes = allergy_notes(record.allergies, said)
            out.models_used = models()
            return out
        out.note = GENERAL_WORDS_NOTE
        out.allergy_notes = allergy_notes(record.allergies, said)
        out.models_used = models()
        return out

    results, searched = cached_search(words + SEARCH_SUFFIX, db, search, now)
    results = [r for r in results if not NOT_FOR_PATIENTS.search(r.url)]
    out.allergy_notes = allergy_notes(record.allergies, said)
    out.considered = considered_conditions(record.conditions, words)
    if not results:
        out.note = ((RECORD_NOTE if kind == "both" else ASK_NOTE) if out.record_items
                    else NO_ANSWER_NOTE)
        out.models_used = models()
        return out

    facts = "\n".join(f"{label}: {'; '.join(items) or 'none in the record'}" for label, items in (
        ("Conditions", record.conditions), ("Allergies", record.allergies),
        ("Current medicines", record.medicines)))
    answer = ai.reason_json(
        prompts.ASK_ANSWER,
        f"{context}Question: {effective}\n\nFrom the patient's record:\n{facts}\n\n"
        f"Sources:\n{_sources_block(results)}", _Answer, max_tokens=4096, thinking=False,
        temperature=0.0)
    for p in answer.points:
        text = p.text.strip()
        if not (1 <= p.source <= len(results)) or not _clean_sentence(text):
            continue
        r = results[p.source - 1]
        if not quote_in_source(p.quote, r.content):
            continue
        if any(k.text.casefold() == text.casefold() for k in out.points):
            continue
        if len(out.points) < MAX_ASK_POINTS:
            n = next((s.n for s in out.sources if s.url == r.url), 0)
            if not n:
                n = len(out.sources) + 1
                out.sources.append(AskSource(n=n, site=_site_name(r.url), title=r.title,
                                             url=r.url))
            category = p.category.strip().casefold()
            # "With your conditions" only when the point itself names one of the patient's
            # confirmed conditions (same names and everyday words as the search check).
            if category not in _CATEGORIES or (
                    category == "your_conditions"
                    and not considered_conditions(record.conditions, f"{text} {p.quote}")):
                category = "good_to_know"
            out.points.append(AskPoint(category=category, source=n, text=text, quote=p.quote,
                                       site=_site_name(r.url), title=r.title, url=r.url))
    if not out.points:
        out.note = (NO_ANSWER_NOTE if not out.record_items
                    else RECORD_NOTE if kind == "both" else ASK_NOTE)
    else:
        out.summary = _summary_of_points(effective, out.points, ai, record.medicines)
    out.models_used = models()
    return out
