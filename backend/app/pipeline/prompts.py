"""Prompt texts, kept in one place so prompt versions can be tracked in the build log."""

READ_DOCUMENT = (
    "Read all text in this image exactly as written, line by line, in the original language. "
    "Include every part of each line, also text at the right side of the page or in a second "
    "column (for example age, sex, quantities, amounts). "
    "Copy names, numbers, doses and units exactly. Do not translate, correct, summarize or add "
    "anything. If a word is unreadable, write [unclear]."
)

TRANSCRIBE = (
    "Transcribe this doctor consultation exactly as spoken, in the language spoken (Hindi stays "
    "in Devanagari, English in Latin script, mixed speech stays mixed). Do not translate, "
    "summarize or correct. Write each speaker turn on a new line."
)


def transcribe_with_hints(drug_names: list[str]) -> str:
    """Spelling hints only: names, never doses, durations or quantities."""
    if not drug_names:
        return TRANSCRIBE
    return (
        f"{TRANSCRIBE} Medicine names that may come up, for spelling only: "
        f"{', '.join(drug_names)}. Write only what is actually said."
    )


DRUG_NAMES = (
    "List the medicine names (brand or generic) that appear in these documents. Names only: no "
    "doses, strengths, quantities or durations."
)

SORT_DOCUMENTS = """You get the readings of the document photos from one doctor visit. Say what
each document is, and read pharmacy bills line by line.

For each document, by its `source_id`:
- `kind`: "prescription" (a doctor's prescription or consultation note), "bill" (a pharmacy or
  hospital bill, invoice or cash memo listing items sold with quantities or prices), "report"
  (lab, imaging or discharge report) or "other".
- `date`: for a bill, the date printed on it as YYYY-MM-DD (Indian documents write
  day/month/year: 12/10/2026 is 2026-10-12). Null if none, and null for other kinds.
- `lines`: for a bill only, one per item sold, in order. Skip headers, totals, taxes,
  discounts, and the shop's or patient's details. Empty for other kinds.
  - `name`: the item as printed, e.g. "Pantocid 40 Tab".
  - `quote`: the item's whole line from the reading, copied exactly, character for character,
    with nothing left out (keep strengths and pack sizes such as "100 ml").
  - `quantity`: the number in the quantity column; null if there is none.
  - `unit`: what the quantity counts, only if printed next to it (tab, strip, bottle); null
    otherwise. A strength or volume in the name ("650", "100 ml") is not the unit.
  - `units_per_pack`: the pack size only if printed ("strip of 10" is 10); otherwise null.
  - `item_type`: "medicine" for a medicine; "non_drug" for anything else (syringes, needles,
    gloves, cotton, bandages, devices, fees, charges).
Copy only what is printed. Never correct, guess or add anything.
"""

MEDICINE_SUMMARY = """You write a short, general summary of one medicine for a patient, using only
the numbered sources given. The patient already has this medicine in their health record.

Four parts, each a list of points (at most 4 per part, fewer is fine, empty if the sources say
nothing on it):
- `uses`: what the medicine is used for.
- `how_taken`: how it is usually taken (by mouth, with food, at night, as an inhaler...). Never
  give doses, amounts, strengths or how many times a day.
- `side_effects`: common side effects.
- `warnings`: who should be careful, and serious effects that need medical help.

Each point:
- `text`: one short sentence (under 25 words) in plain English, in your own words, stated as a
  fact about the medicine or about people in general, never addressed to "you" ("Diarrhoea is a
  common side effect.", "It is not recommended for people with severe kidney disease."). Never
  tell the reader to start, stop, change or skip a medicine or dose, even when a source does. For
  serious effects write "The sources say to get medical help right away if ...".
- `source`: the number of the source it comes from.
- `quote`: words copied exactly, character for character, from that source, that back the
  point. Points whose quote is not in the source are discarded.

Each point says something new: when several sources say the same thing, use it once.

`matches`: false if the sources are not about this medicine (then leave every part empty).
Use only the sources: no outside knowledge. Do not mention prices or brands to buy.
"""

PREPARE_VISIT = """A patient typed what they feel, to get ready for a doctor visit. You also get a
few facts from their health record, and sometimes the patient's answers to a few follow-up
questions (context: use them to understand the symptoms; do not repeat them as symptoms unless
they describe one). You do not diagnose, and you never tell the patient to start,
stop, skip or change a medicine or dose.

Return:
- `symptoms`: what the patient describes, one item per symptom. `text`: a short plain-English
  phrase (under 12 words). `quote`: words copied exactly, character for character, from what
  the patient typed, that back it. Items whose quote is not in their text are discarded. Use the
  patient's words only: do not add symptoms they did not mention.
- `since`: how long the patient says it has been going on, in their own short words ("3 days",
  "since last week"); null if they did not say. `since_quote`: words copied exactly from what the
  patient typed (or answered) that say it; null if `since` is null. A `since` whose quote is not
  in their words is discarded.
- `specialty`: the one key below for the kind of doctor who would usually see this first. When
  unsure, "general_physician".
- `reason`: one short sentence in plain English on why that kind of doctor ("Ear, nose and throat
  doctors see problems like a blocked ear."). Never a diagnosis, never about a medicine.
- `questions`: up to 3 short questions the patient may want to ask the doctor, in the first
  person, about what they described ("How long should I expect this to last?"). No doses, and
  nothing that tells them to start, stop or skip a medicine. Fewer is fine.

Keys for `specialty`:
"""  # PREPARE_VISIT v2 (2026-10-27: since from the text, follow-up answers)

PREPARE_QUESTIONS = """A patient typed what they feel, to get ready for a doctor visit. You ask up to
3 short, plain follow-up questions whose answers would help decide which kind of doctor to see, for
example where exactly it is, since when (only if they did not say), or whether there is a fever.
You do not diagnose and never name a diagnosis or illness. Never ask about medicines to take and
never tell them to start, stop, skip or change a medicine or dose. Do not repeat what they already
said. Never ask about emergency warning signs (chest pain, trouble breathing, fainting, bleeding,
stroke signs, self-harm): code checks those itself. Never ask "have you tried" a remedy or medicine.
Fewer questions are fine; none is fine.

Return `questions`, each:
- `text`: one short question in plain English, addressed to the patient ("Where exactly is the
  pain?").
- `choices`: up to 4 very short tap answers when the answer is natural to pick ("Left side",
  "Right side", "Both", "Not sure"); an empty list when it needs free words.
"""  # PREPARE_QUESTIONS v2 (2026-10-27: no emergency-sign questions)

ASK_SEARCH = """A patient asks a question about their health. You do not answer it: you decide what
kind of question it is and, if general information is needed, write words to search trusted
health sites with.

Return:
- `kind`:
  "record": it is about the patient's own record: their medicines, allergies, conditions, test
    values, past visits or events ("what tablets am I taking for BP?", "what was my last sugar
    result?").
  "general": it asks for general health information: food, diet, sleep, exercise, habits, a
    condition or symptom in general ("can I eat rice with diabetes?").
  "both": it needs the patient's own record and general information together ("can I eat papaya
    with my BP tablets?").
  "not_health": anything else (sports scores, coding, prices, chit-chat).
- `search_words`: for "general" and "both" only: at most 8 plain English words naming the food,
  habit or condition the question is about, in general terms ("diabetes breakfast", "turmeric milk
  cold"). No digits. No names of people. Nothing personal: not an age, a place or a medicine dose.
  Empty for "record" and "not_health".

You may also get the patient's previous question. If the new question leans on it ("what about
mango?", "and at night?"), judge and write words for the new question together with the same food,
habit or condition from the previous one. If it stands on its own, ignore the previous question.
"""  # ASK_SEARCH v3 (2026-10-27: record / general / both / not_health)

ASK_RECORD = """A patient asks a question about their own health record. You get the question and a
numbered list of the patient's confirmed record items, each with an id.

Return:
- `item_ids`: the ids of the items that answer the question (only ids from the list; empty if
  none do).
- `summary`: at most 2 short plain sentences saying what those items show, using only those
  items. Do not diagnose or advise. Never tell the reader to start, stop, skip or change a
  medicine or dose, and give no dose or amount. If the items do not answer the question, say so
  in one sentence.
"""  # ASK_RECORD v1 (2026-10-27)

ASK_SUMMARY = """You get a patient's question and a few short points that trusted health sites
support. Write a summary of at most 2 short plain sentences that answers the question using only
those points. General information, not advice for this patient: never address the reader as
"you", never diagnose, never tell anyone to start, stop, skip or change a medicine or dose, and
give no dose or amount.

Return `summary`.
"""  # ASK_SUMMARY v1 (2026-10-27)

ASK_ANSWER = """A patient asked a general question about food or daily life and health. You get the
question, a few facts from their record, and numbered sources from trusted health sites. Answer
with short points that the sources back, using only the sources. This is general information, not
advice for this patient, and you do not diagnose.

Return `points`, at most 5, fewer is fine, empty if the sources do not answer the question. Each:
- `text`: one short sentence (under 25 words) in plain English, in your own words, stated as a
  fact about the food or habit or about people in general, never addressed to "you". Where the
  record facts make a source point matter (a condition, an allergy), say so about people with
  that condition in general, not about this patient. Never tell the reader to start, stop, skip
  or change a medicine or a dose, even when a source does, and never give a dose or an amount of
  a medicine. For anything serious write "The sources say to get medical help if ...".
- `source`: the number of the source it comes from.
- `quote`: words copied exactly, character for character, from that source, that back the point.
  Points whose quote is not in the source are discarded.
- `category`: "good_to_know" for a general fact; "be_careful" for a risk, a limit or a warning;
  "your_conditions" for a point that matters for people with one of the conditions listed in the
  record facts. When unsure, "good_to_know".

Each point says something new. Use only the sources: no outside knowledge. You may also get the
patient's previous question and its points, as context for a follow-up question ("what about
mango?"): answer the new question only, and still only from the sources.
"""  # ASK_ANSWER v2 (2026-10-27: category, previous question)

VOICE_INPUT = (
    "Write down exactly what the speaker says, in the language spoken. Hindi stays in Latin "
    "letters (Hinglish), English stays English, mixed speech stays mixed. Do not translate, "
    "correct, summarize or explain. No speaker labels, no notes, nothing added. If nothing was "
    "said, answer with an empty reply."
)  # VOICE_INPUT v1 (2026-10-27)


GENERIC_NAMES = """For each medicine name below (one per line), say what it is.

- `name`: the line exactly as given.
- `status`: "specific" if it names a particular medicine (brand or generic); "vague" if it only
  gives a purpose or class and no particular medicine (for example "anti-malarial tablets",
  "BP tablets", "painkiller", "antibiotic course", "vitamins"); "unknown" if you do not
  recognise it. Never guess: when unsure, use "unknown".
- `generics`: for "specific", its active ingredients in lowercase English generic names
  (e.g. ["amoxicillin", "clavulanic acid"]). Empty for "vague" and "unknown".
"""

BRAND_INGREDIENTS = """For each medicine below you get search results from trusted medicine sites,
numbered across the whole list. Say each medicine's active ingredients, using only the results
listed under that medicine (v2, 2026-10-17).

- `name`: the medicine exactly as given.
- `found`: true only if a result is about this medicine (the same brand name; for a generic
  name, that generic). Never guess from a similar name: if unsure, false.
- `generics`: its active ingredients in lowercase English generic names, e.g. ["ondansetron"].
  Empty if not found.
- `source`: the number of the result you used.
- `quote`: words copied exactly, character for character, from that result, that name the
  medicine's ingredients (e.g. "Ondem 4 Tablet is composed of Ondansetron"). Matches whose quote
  is not in the result are discarded.
"""

DRUG_FAMILIES = """For an allergy check, say what each name below is (one per line). A name is an
allergy as a patient or doctor gave it, a medicine name, or an active ingredient.

- `name`: the line exactly as given.
- `kind`: "medicine" for a particular medicine or active ingredient; "group" for a group of
  medicines ("sulfa drugs", "antibiotics"); "not_medicine" for anything else (foods, pollen,
  dust, latex, animals, insect stings); "unknown" if you do not recognise it. Never guess: when
  unsure, use "unknown".
- `ingredients`: for "medicine", its active ingredients in lowercase English generic names
  (e.g. ["cefuroxime"]). Empty otherwise.
- `families`: the keys of the families below that it belongs to. Only keys from this list;
  empty if none fits. A broad group ("antibiotics", "painkillers") fits no single family.

Families (key: name, with examples):
"""

TRANSLATE_FIELDS = """Each line below is a value from an English health record that was left
partly in Hindi or Hinglish (romanized). Rewrite each line as short, plain English, the way a
health record would say it ("raat ko ek tablet" -> "one tablet at night", "zaroorat pade toh do
puff" -> "two puffs if needed"). Keep medicine names, numbers and units exactly. Do not add
anything that is not in the line. Return `english` with one entry per line, in the same order.
"""

EXTRACT = """You turn a doctor consultation and its documents into health record entries.

Rules:
- Only facts actually stated in the sources. Never infer, guess or add medical knowledge.
- Each item needs `source_id` (one of the given source ids) and `quote`: words copied exactly,
  character for character, from that source, in its original language. The quote must contain
  the fact. Items whose quote is not found in the source are discarded.
- `said_by`: who states the fact. "document" for document photos. In a consult transcript or
  typed notes, decide from the content: the doctor asks, examines, diagnoses and prescribes;
  the patient describes complaints, history and allergies. Transcripts have no speaker labels.
- Complaints the patient describes (fever, cough, pain) are `symptom` items, never `diagnosis`.
  A `diagnosis` is only what the doctor or a document concludes, or a long-term condition the
  patient already has (then `chronic` true).
- `label`: short, in English, e.g. "Azithromycin 500 mg" or "Penicillin allergy".
- Transcripts may be romanized, phonetically spelled Hindi or Hinglish. Medicine names can be
  misspelled ("parasetamal" is paracetamol) and numbers can be Hindi words ("chaiso pachas" is
  650, "teen din" or "tindin" is 3 days, "paanch din" or "pansdin" is 5 days, "das din" is 10
  days). Common phrases: "din mein ek baar" = once a day (doses_per_day 1), "din mein do baar"
  = twice a day, "subah shaam" = morning and evening (2), "raat ko" = at night, "khane ke baad"
  = after food, "zaroorat pade toh" = if needed. "... din mein theek na ho to phir aaiye" is a
  follow_up ("come back if not better in ... days"). Fill dose, doses_per_day and duration from
  these phrases just as for English. Quote the transcript exactly as written; write the label
  and every field in English ("raat ko ek tablet" becomes frequency "one tablet at night"). Lower `confidence` when you had to
  interpret garbled words.
- A medicine the doctor says NOT to take, or stops, is not a medication item. Mention it in a
  `note` item instead.
- If the doctor names only a purpose and no particular medicine ("some tablets for malaria",
  "BP tablets"), still make the medication item, label it with the words used, and lower
  `confidence`. Never fill in a medicine name that was not said or written.
- Allergies: the substance as the label; the reaction in fields.reaction if stated.
- Medication: dose as written; `doses_per_day` (3 for "three times a day", 1 for "once a day",
  leave empty if only "as needed"); `duration_value` and `duration_unit` exactly as stated;
  `ongoing` true only for long-term medicines; `as_needed` true for "if needed" or "SOS".
  Put the frequency wording in fields.frequency and any condition in fields.instructions.
- Diagnosis: `chronic` true only for long-term conditions (diabetes, hypertension, asthma...).
- Events (emergency_visit, admission, procedure, surgery, implant): `occurred_on` as
  YYYY, YYYY-MM or YYYY-MM-DD only if a date is stated.
- follow_up for return visits; test for investigations ordered or reported (one item per test or
  panel, no numbers: those go in `results`, see below).
- `confidence` from 0 to 1: how clearly the source states this fact.
- No duplicates: one item per fact, using the clearest source.
- `visit_date`: the date printed on a prescription, bill or report, as YYYY-MM-DD (Indian
  documents write day/month/year: 12/10/2026 is 2026-10-12). Null if no document shows a date.
  On a lab report use the date the sample was collected, else the report date.

Test values (v4, 2026-10-19). Besides `items`, return `results`: the measured values printed on
the documents (lab reports, and numbers a doctor wrote on a prescription: blood pressure, pulse,
temperature, weight, height, oxygen level, blood sugar). Documents only: never a value that was
only spoken or typed.
- A test or panel (CBC, lipid profile, HbA1c, X-ray) is one `test` item. Its numbers go in
  `results`, never as separate `test` items. Vitals written on a prescription (BP, pulse,
  temperature, weight, oxygen level) are `results` only: never a `test` item.
- One result per printed value: `source_id`, `name` (the parameter exactly as printed, e.g.
  "Hemoglobin", "BP"), `quote` (the whole printed row, copied exactly, character for character,
  with the value and its name in it).
- `value`: the number only, as printed, without its unit ("13.2", "14200" for "14,200"). If the
  result is in words ("Nil", "Non-reactive", "Normal study") or has a sign ("<0.5"), leave
  `value` empty and put it in `value_text`, copied as printed. An imaging report's impression is
  a result in words.
- `unit` only if printed next to it. `ref_low` and `ref_high`: the normal range printed for it
  ("13.0 - 17.0"); one of them for "up to 200"; `ref_text`: the range as printed.
- Blood pressure "138/88" is two results with the same quote: `part` "systolic" (138) and
  `part` "diastolic" (88), `name` as printed ("BP").
- `test`: the `label` of the `test` item this value belongs to, exactly as you wrote it there;
  null if it belongs to none (for example numbers written on a prescription).
- `measured_on`: the date printed on the same document as the value (on a lab report, the date
  the sample was collected, else the report date), as YYYY-MM-DD; null if that document shows
  none. A report and a prescription in the same visit can have different dates.
- Never convert units, calculate, or say whether a value is normal, high or low.

Visit name and place (v1, 2026-10-22). Also return:
- `visit_title`: a short, neutral name for the visit, 2 to 5 words, in English, from what the
  visit is about: the main diagnosis or complaint, or the tests for a lab report ("Fever and
  cough", "Blood sugar and lipids"). No patient name, no advice, no medicine names, no date.
  Null if nothing says what the visit is about.
- `facility`: the hospital, clinic or lab name exactly as printed on a document (a letterhead or
  stamp), never a doctor's name. `facility_source_id` and `facility_quote`: the source and the
  exact words that hold it, copied character for character. All three null if no document
  prints one.
"""

def report_extra(tests: list[str], chosen: str) -> str:
    """Added to EXTRACT for a report the patient added to a test of an earlier visit (v2,
    2026-10-19: names the test the patient chose)."""
    ordered = "\n".join(f"- {t}" for t in tests) or "- (none)"
    return (
        "\nThese sources are a report the patient added later to the visit's test "
        f"\"{chosen}\". Return `results` only (with `measured_on`), and leave `items` empty. "
        "Set each result's `test` to the label of the listed test it belongs to, exactly as "
        f"written; if it belongs to none of the others, use \"{chosen}\".\n"
        f"Tests of this visit:\n{ordered}\n"
    )


# Added to EXTRACT for documents from a hospital stay the patient logged (v1, 2026-10-15).
STAY_EXTRA = """
These sources are from a hospital stay (an emergency visit or an admission) that the patient has
already logged in their record.
- Do not make an item for the stay itself: no emergency_visit or admission items.
- Medicines to take after leaving hospital (discharge medicines, "advised", "continue at home")
  are medication items.
- Medicines given only in hospital (IV, injections, drips, nebulisation, "given",
  "administered", "treatment received") are NOT medication items. Make one note item per
  medicine, labelled "Given in hospital: <medicine>", quoting the words that name it.
- Diagnoses, procedures, surgeries, implants and tests from the stay are items as usual.
- `discharge_date`: the date of discharge printed on a discharge summary, as YYYY-MM-DD.
  `discharge_source_id` and `discharge_quote`: the source and the exact words that hold that
  date, copied character for character. All three null if no discharge date is printed.
"""
