"""The pipeline for one encounter: read documents, transcribe, extract, quote check.

Runs as a background task. Media arrives in memory, is sent to the model, and is dropped when
this function returns: it is never written to disk. Only the text the model read or heard is
stored (as each source's `text`, plus timestamped `segments` for audio). The patient's browser
keeps its own copy until the visit is reviewed, so it can ask for a careful re-read.

Bills are sorted out after reading (`bills.py`): their lines are stored as bill lines, not
record entries. Last, the cross-checks (`crosscheck.py`) compare the visit with the record.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from app.ai.client import AIClient, audio_part, image_part
from app.ai.search import WebSearch
from app.config import get_settings
from app.media import audio as audio_prep
from app.media import pdf
from app.pipeline import attach, prompts
from app.pipeline.bills import (
    apply_kinds,
    bill_date,
    bill_only,
    bill_suggestions,
    extraction_text,
    parts_of,
    sort_documents,
    to_bill_lines,
)
from app.pipeline.brands import from_search
from app.pipeline.crosscheck import allergy_clashes, billed_not_prescribed
from app.pipeline.extract import (
    DrugNames,
    Extraction,
    avoided_words,
    clean_title,
    discharge_date,
    document_date,
    facility_of,
    to_entries,
)
from app.pipeline.medicines import check_entries
from app.pipeline.quotes import quote_in_source
from app.pipeline.results import to_results
from app.pipeline.review import read_as_is
from app.schemas.record import (
    AttachSuggestion,
    BillLine,
    Discrepancy,
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    SourceItem,
    SourceType,
    TranscriptSegment,
    VisitDateSource,
)
from app.storage import Storage
from app.views import fact_key

T = TypeVar("T")

REPLACED_NOTE = "Replaced by a careful re-read"
PDF_TEXT = "pdf text layer (no model)"  # in models_used when a PDF page gave its own text


@dataclass
class Media:
    """One uploaded file, held in memory only. `source_id` links it to its SourceItem.
    An "image" is any document: a photo, a screenshot or a PDF (`mime` application/pdf)."""

    source_id: str
    kind: Literal["image", "audio"]
    data: bytes
    mime: str


def process_encounter(
    db: Storage,
    ai: AIClient,
    encounter_id: str,
    media: list[Media],
    notes: str | None = None,
    *,
    careful: bool = False,
    adding: bool = False,
    report_for: str | None = None,
    search: WebSearch | None = None,
) -> None:
    """Read a visit. `careful` is a re-read of the same files with thinking on for documents.
    `adding` reads files added later to a visit (a hospital stay's documents): only the new
    files are read, and their suggestions join the ones already there. `report_for` is the test
    entry a report was added to ("Add report"): only the values on it are kept, and they are
    dated by the report, not the visit. `search` looks up brand names the table does not know on
    trusted sites (`brands.py`); without it, the model's own match is used."""
    enc = db.get_encounter(encounter_id)
    if enc is None:
        return
    before = enc.model_copy(deep=True)
    if adding:  # the route already attached the new files; a failed read takes them off again
        new = {m.source_id for m in media}
        before.sources = [s for s in before.sources if s.id not in new]
    if hasattr(ai, "used"):
        ai.used.clear()  # per worker thread; only this visit's calls from here on
    try:
        if careful:
            _reread(db, ai, enc, media, search)
        elif adding:
            _run(db, ai, enc, media, notes, use={m.source_id for m in media}, search=search,
                 report_for=report_for, name_visit=False)
        else:
            _run(db, ai, enc, media, notes, search=search)
    except Exception as exc:  # noqa: BLE001 - any failure must mark the encounter failed
        if careful or adding:
            # A failed re-read or addition leaves the visit as it was, so nothing is lost.
            what = "Careful re-read" if careful else "Reading the added documents"
            before.status, before.step = EncounterStatus.READY, None
            before.error = f"{what} failed at {enc.step or 'starting'}: {exc}"
            db.save_encounter(before)
        else:
            enc.status = EncounterStatus.FAILED
            enc.error = f"{enc.step or 'starting'}: {exc}"
            db.save_encounter(enc)
    finally:
        media.clear()  # drop the media; nothing else holds it


INTERRUPTED = "The read was interrupted when the server restarted"


def recover_interrupted_reads(db: Storage) -> int:
    """Run on a real start, when no job can be running: a visit still marked as being read was cut
    off (a restart, a sleeping host), and would stay "processing" for ever, unable even to be
    deleted. A visit with entries keeps them (a re-read or added documents was interrupted, so it is
    as it was, minus files never read); one without goes to "failed", for a retry from the files the
    patient's browser kept. Returns how many were put right."""
    fixed = 0
    for stale in db.processing_encounters():
        enc = db.get_encounter(stale.id)
        if enc is None:
            continue
        if enc.entries:
            enc.sources = [s for s in enc.sources if s.text or s.source_type == SourceType.TEXT]
            enc.status, enc.error = EncounterStatus.READY, INTERRUPTED
        else:
            enc.status, enc.error = EncounterStatus.FAILED, f"{INTERRUPTED}. Try again."
        enc.step = None
        db.save_encounter(enc)
        fixed += 1
    return fixed


def _reread(db: Storage, ai: AIClient, enc: Encounter, media: list[Media],
            search: WebSearch | None = None) -> None:
    """A careful re-read. Each file gets a new source, so confirmed entries keep the text their
    quotes came from. Suggestions not yet reviewed are replaced; confirmed and rejected entries
    stay, and new suggestions that repeat one of them are dropped."""
    def root(source_id: str) -> str:  # the file's first source, however often it was re-read
        src = next((s for s in enc.sources if s.id == source_id), None)
        return (src.reread_of or src.id) if src else source_id

    reread: set[str] = set()  # the files being read again, by their first source
    attached: set[str] = set()  # of those, the files smart attach moved here (see below)
    groups: dict[str | None, list[Media]] = {}  # reports added to a test are read as reports
    live = [t.id for t in _tests(enc.entries)]
    for m in media:
        old = next(s for s in enc.sources if s.id == m.source_id)
        # A file smart attach moved here is read like a report added to a test: its values only,
        # filed under the visit's tests, the visit's date and name untouched. The test it was
        # filed under first, else any test of the visit; with none, the file is left as it was.
        key = old.report_for
        if key is None and old.attached_to:
            key = old.attached_to if old.attached_to in live else next(iter(live), None)
            if key is None:
                continue
        new = SourceItem(source_type=old.source_type, filename=old.filename,
                         reread_of=old.reread_of or old.id, careful=True, report_for=old.report_for,
                         attached_to=old.attached_to)
        enc.sources.append(new)
        reread.add(root(old.id))
        if old.attached_to and old.report_for is None:
            attached.add(root(old.id))
        m.source_id = new.id
        groups.setdefault(key, []).append(m)
    # Bill lines are not reviewed, so the new reading of a bill simply replaces the old one.
    enc.bill_lines = [b for b in enc.bill_lines if root(b.provenance.source_id) not in reread]
    typed = {s.id for s in enc.sources if s.source_type == SourceType.TEXT}
    earlier = list(enc.entries)
    new_entries: list[Entry] = []
    failures: list[str] = []
    for report_for in sorted(groups, key=lambda k: k is not None):  # the visit's own files first
        files = groups[report_for]
        use = {m.source_id for m in files} | (typed if report_for is None else set())
        new_entries += _run(db, ai, enc, files, None, use=use, careful=True, finish=False,
                            search=search, report_for=report_for)
        failures += enc.quote_failures
    enc.quote_failures = list(dict.fromkeys(failures))  # a report's run repeats the earlier ones

    for e in earlier:
        if e.kind != EntryKind.RESULT and any(root(p.source_id) in attached for p in e.provenance):
            continue  # only the values of an attached file are read again; its other entries stay
        if e.status == EntryStatus.PROPOSED:
            e.set_status(EntryStatus.REJECTED, by="system", note=REPLACED_NOTE)
        elif (e.added_as_read and not e.edits and e.status == EntryStatus.CONFIRMED
              and any(root(p.source_id) in reread or p.source_id in typed for p in e.provenance)):
            # A fact read as it is from a re-read file (or the typed notes, which are read again
            # with them) and never touched: the new reading replaces it.
            e.set_status(EntryStatus.REJECTED, by="system", note=REPLACED_NOTE)
    handled = [e for e in earlier
               if (e.added_as_read and e.edits)
               or (e.status_history and e.status_history[-1].by != "system")]
    reviewed = {k for e in handled for k in _keys(e)}
    fresh = [e for e in new_entries if fact_key(e) not in reviewed]
    # Values point at tests that exist: one that repeats an earlier entry the patient handled
    # (not one they turned down) points at that entry, and a test replaced by this read gives way
    # to its new version, for values read earlier as well as now.
    kept = {k: e.id for e in handled if e.status != EntryStatus.REJECTED for k in _keys(e)}
    dropped = {e.id for e in new_entries if fact_key(e) in reviewed}
    moved = {e.id: kept[fact_key(e)] for e in new_entries if e.id in dropped and fact_key(e) in kept}
    live = {fact_key(e): e.id for e in fresh if e.kind == EntryKind.TEST}
    swapped = {e.id: live[fact_key(e)] for e in earlier
               if e.kind == EntryKind.TEST and e.status == EntryStatus.REJECTED
               and e.status_history[-1].by == "system" and fact_key(e) in live}

    def relinked(links: list[str]) -> list[str]:
        out = [moved.get(i, swapped.get(i, i)) for i in links if i not in dropped or i in moved]
        return list(dict.fromkeys(out))

    for e in [*earlier, *fresh]:
        if e.kind == EntryKind.RESULT:
            e.links = relinked(e.links)
    enc.entries = earlier + fresh
    enc.discrepancies = _step(db, ai, enc, "cross_checking", lambda: _cross_check(db, ai, enc))
    enc.step = None
    enc.status = EncounterStatus.READY
    db.save_encounter(enc)


def _run(
    db: Storage,
    ai: AIClient,
    enc: Encounter,
    media: list[Media],
    notes: str | None,
    *,
    use: set[str] | None = None,
    careful: bool = False,
    finish: bool = True,
    search: WebSearch | None = None,
    report_for: str | None = None,
    name_visit: bool = True,
) -> list[Entry]:
    """Read, transcribe and extract. `use` limits extraction to these sources (a re-read
    leaves the earlier readings out). `name_visit` is False for files added to a visit later
    (they are only part of it, so they must not rename it). Returns the new entries, which it
    appends to the visit."""
    by_id = {s.id: s for s in enc.sources}

    def step(name: str, fn: Callable[[], T]) -> T:
        return _step(db, ai, enc, name, fn)

    images = [m for m in media if m.kind == "image"]
    audio = [m for m in media if m.kind == "audio"]

    # 0. Decode and chunk audio first: a bad file fails in a second, before any model call.
    chunks = {m.source_id: step("preparing_audio", lambda m=m: audio_prep.prepare(m.data))
              for m in audio}

    # 1. Read documents. A PDF page with a text layer is used as it is; other pages are read.
    def read(data: bytes, mime: str) -> str:
        return ai.perceive([image_part(data, mime)], prompts.READ_DOCUMENT,
                           thinking=careful or None).strip()

    for m in images:
        if m.mime == "application/pdf":
            pages = step("reading_documents", lambda m=m: pdf.pages(m.data))
            texts = [p.text or step("reading_documents", lambda p=p: read(p.jpeg, "image/jpeg"))
                     for p in pages]
            if any(p.text for p in pages):
                used = enc.models_used.setdefault("reading_documents", [])
                if PDF_TEXT not in used:
                    used.append(PDF_TEXT)
            by_id[m.source_id].page_texts = texts
            by_id[m.source_id].text = "\n\n".join(texts)
        else:
            by_id[m.source_id].text = step("reading_documents", lambda m=m: read(m.data, m.mime))
    doc_text = "\n\n".join(by_id[m.source_id].text or "" for m in images)

    # 1b. Sort the documents and read bills into bill lines (never record entries).
    bill_lines: list[BillLine] = []
    failed: list[str] = []
    billed_on: date | None = None
    docs = parts_of([by_id[m.source_id] for m in images])
    if docs:
        def sort_and_read() -> tuple[list[BillLine], list[str], date | None]:
            sort = sort_documents(docs, ai)
            apply_kinds(sort, docs)
            return (*to_bill_lines(sort, docs, ai), bill_date(sort, consult_date(enc)))

        bill_lines, failed, billed_on = step("sorting_documents", sort_and_read)
        if report_for is not None:  # a report for a test brings values, never bill lines
            bill_lines, failed = [], []

    # 2. Transcribe, with drug names from the documents as spelling hints only.
    if audio:
        names: list[str] = []
        if doc_text:
            names = step(
                "finding_drug_names",
                lambda: ai.reason_json(prompts.DRUG_NAMES, doc_text, DrugNames).names,
            )
        instruction = prompts.transcribe_with_hints(names)
        for source_id, parts in chunks.items():
            source = by_id[source_id]
            for c in parts:
                text = step(
                    "transcribing",
                    lambda c=c: ai.perceive([audio_part(c.wav, "audio/wav")], instruction),
                )
                source.segments.append(
                    TranscriptSegment(start_s=c.start_s, end_s=c.end_s, text=text.strip())
                )
            source.text = "\n".join(s.text for s in source.segments)

    # Typed notes are a source too (the fallback when there is no audio).
    if notes and notes.strip():
        typed = SourceItem(source_type=SourceType.TEXT, text=notes.strip())
        enc.sources.append(typed)
        if use is not None:
            use.add(typed.id)

    # 3. Extract, then keep only proposals whose quote is really in their source. Bills (and
    # the bill pages of a PDF) are left out: what was sold is not what was prescribed.
    readable = []
    for s in enc.sources:
        if use is None or s.id in use:
            text = extraction_text(s)
            readable.append(s if text == s.text else s.model_copy(update={"text": text}))
    short = _short_ids(readable)
    if not short and not bill_lines:
        enc.step = "extracting"
        raise ValueError("No text could be read from the uploads")
    extraction = Extraction(items=[])
    instructions = prompts.EXTRACT + (prompts.STAY_EXTRA if enc.stay else "")
    report_test = next((e for e in enc.entries if e.id == report_for), None) if report_for else None
    if report_test is not None:  # "Add report": the values on it, filed under this visit's tests
        instructions += prompts.report_extra([t.label for t in _tests(enc.entries)],
                                             report_test.label)
    if short:
        extraction = step(
            "extracting",
            lambda: ai.reason_json(
                instructions, _sources_block(short), Extraction, max_tokens=8192
            ),
        )
    # The visit date: printed on a document if there is one, else the upload day. A stay keeps
    # the start date the patient gave.
    printed = document_date(extraction, consult_date(enc)) or billed_on
    if report_test is not None:
        pass  # a report added later never moves the visit's date; its values carry their own
    elif printed and enc.visit_date_source != VisitDateSource.PATIENT:
        enc.visit_date, enc.visit_date_source = printed, VisitDateSource.DOCUMENT
    elif enc.visit_date is None:
        enc.visit_date, enc.visit_date_source = consult_date(enc), VisitDateSource.UPLOAD
    starts = enc.visit_date
    if enc.stay and report_test is None:
        starts = _stay_dates(enc, extraction, short)
    entries, dropped = to_entries(extraction, short, starts)
    if report_test is not None:
        entries, dropped = [], []  # a report added to a test gives its values only
    if enc.stay:
        entries = _for_stay(enc, entries)
    if report_for is None:  # a report added to a test never names the visit
        _name_visit(db, enc, extraction, short, entries, full=name_visit)
    # Test values: read as they are, after the checks in results.py. They file under a test of
    # this reading or an earlier one by its label.
    values, value_failures = to_results(
        extraction, short,
        document_date(extraction, local_today()) or local_today(),
        [*_tests(enc.entries), *(e for e in entries if e.kind == EntryKind.TEST)],
        report_test,
        today=local_today(),
    )
    dropped = [*dropped, *value_failures]
    for e in [*entries, *values]:  # which page of a PDF each quote is on
        for prov in e.provenance:
            pages = by_id[prov.source_id].page_texts if prov.source_id in by_id else []
            if len(pages) > 1 and prov.locator is None:
                n = next((i for i, t in enumerate(pages, 1) if quote_in_source(prov.quote, t)), None)
                prov.locator = f"page {n}" if n else None
    # 4. Generic names, unnamed medicines, names missing from quotes, Hindi left in details.
    def check_medicines() -> list[Entry]:
        checked = check_entries(entries, ai)
        searches = search.calls if search else 0
        from_search(checked, bill_lines, ai, search, db)
        if search and search.calls > searches:  # evidence: Tavily answered in this step
            used = enc.models_used.setdefault("checking_medicines", [])
            if "tavily:search" not in used:
                used.append("tavily:search")
        return checked

    entries = step("checking_medicines", check_medicines)
    if bill_lines and bill_only(enc.sources) and enc.stay is None:
        # Nothing to compare the bill with: its medicines are suggestions instead, because a
        # bill can hold medicines bought for someone else.
        entries = [*entries, *bill_suggestions(bill_lines, enc.visit_date)]
    entries = [*entries, *values]
    read_as_is(entries)  # what the patient need not confirm goes in as read (review.py)
    enc.entries = [*enc.entries, *entries]
    enc.bill_lines = [*enc.bill_lines, *bill_lines]
    # A report added to a test keeps what the visit's own reading left out, and adds its own.
    earlier_failures = enc.quote_failures if report_test is not None else []
    enc.quote_failures = [*earlier_failures, *dropped, *failed]
    if finish:
        # 5. Cross-checks between the sources and the record. A report added to a test brought
        # values only, so there is nothing new to check.
        if report_test is None:
            enc.discrepancies = step("cross_checking", lambda: _cross_check(db, ai, enc))
        if use is None:  # a new visit (not a re-read, not files added later): smart attach
            enc.attach_suggestion = _attach_suggestion(db, ai, enc)
        enc.step = None
        enc.status = EncounterStatus.READY
        db.save_encounter(enc)
    return entries


def _attach_suggestion(db: Storage, ai: AIClient, enc: Encounter) -> AttachSuggestion | None:
    """A new visit of reports only may be the report for a test waiting on an earlier visit
    (`attach.py`). The model is asked only when code finds candidates; a failed or useless answer
    leaves the visit as its own, and never fails the read."""
    found = attach.candidates(db, enc)
    if not found:
        return None
    try:
        return _step(db, ai, enc, "matching_report", lambda: attach.ask(ai, enc, found))
    except Exception:  # noqa: BLE001 - the suggestion is optional
        return None


def _name_visit(db: Storage, enc: Encounter, extraction: Extraction,
                short: dict[str, SourceItem], entries: list[Entry], *, full: bool) -> None:
    """The visit's suggested name and its hospital, clinic or lab.

    `full` is a read of the whole visit (the first read or a careful re-read): only then is an AI
    name set, and only for an ordinary visit (a stay is named by its kind and place, so it gets
    none). An AI name never replaces the patient's own, and is dropped if it holds the patient's
    name or a medicine's (`clean_title`). A stay's place is the one the patient gave; otherwise
    it is what a document or the typed notes print (its quote checked in code)."""
    if full and enc.stay is None and enc.title_source != "patient":
        patient = db.get_patient(enc.patient_id)
        medicines = [e.label for e in [*enc.entries, *entries] if e.kind == EntryKind.MEDICATION]
        title = clean_title(extraction.visit_title,
                            avoided_words(patient.name if patient else None, medicines))
        if title:
            enc.title, enc.title_source = title, "ai"
    given = (enc.stay.place or "").strip() if enc.stay is not None else ""
    if given:
        enc.facility, enc.facility_quote, enc.facility_source_id = given or None, None, None
        enc.facility_locator = None
    elif full:
        found = facility_of(extraction, short)
        if found:  # a re-read that finds none leaves what was found before
            (enc.facility, enc.facility_quote, enc.facility_source_id,
             enc.facility_locator) = found


def _keys(e: Entry) -> set[str]:
    """What an entry the patient handled is called when a re-read finds it again: its current
    wording, and the wording it was read with (an edit changes the first, not the second)."""
    keys = {fact_key(e)}
    first = next((ed.old for ed in e.edits if ed.field == "label" and ed.old), None)
    if first:
        keys.add(fact_key(e.model_copy(update={"label": first})))
    return keys


def _tests(entries: list[Entry]) -> list[Entry]:
    """The tests a value can belong to: not the ones the patient turned down."""
    return [e for e in entries if e.kind == EntryKind.TEST and e.status != EntryStatus.REJECTED]


def _stay_dates(enc: Encounter, extraction: Extraction, short: dict[str, SourceItem]) -> date:
    """A stay's discharge date read off its documents is kept as a suggestion (the patient
    closes the stay). Medicines from a stay's documents are for after it, so they start on the
    day the patient went home: as they said, else as the documents say, else today."""
    stay = enc.stay
    assert stay is not None
    today = local_today()
    found = discharge_date(extraction, short, stay.started_on, today)
    if found:
        stay.suggested_end, stay.suggested_end_quote, stay.suggested_end_source_id = found
    return stay.ended_on or stay.suggested_end or today


def _for_stay(enc: Encounter, entries: list[Entry]) -> list[Entry]:
    """The stay is the patient's own entry, so the documents' account of the admission itself
    is dropped (policy in code, whatever the model returns); everything else is linked to it."""
    stay = enc.stay
    assert stay is not None
    kept = [e for e in entries if e.kind not in (EntryKind.EMERGENCY_VISIT, EntryKind.ADMISSION)]
    for e in kept:
        e.links.append(stay.entry_id)
    return kept


def _step(db: Storage, ai: AIClient, enc: Encounter, name: str, fn: Callable[[], T]) -> T:
    """Run one pipeline step: shown to the polling app, timed, and its models recorded."""
    enc.step = name
    db.save_encounter(enc)  # the app polls this to show progress
    start = time.perf_counter()
    used = getattr(ai, "used", [])  # which models answered (a backup is marked)
    before = len(used)
    result = fn()
    elapsed = time.perf_counter() - start
    enc.step_seconds[name] = round(enc.step_seconds.get(name, 0.0) + elapsed, 2)
    if len(used) > before:
        enc.models_used[name] = sorted({*enc.models_used.get(name, []), *used[before:]})
    return result


def _cross_check(db: Storage, ai: AIClient, enc: Encounter) -> list[Discrepancy]:
    """The visit's medicines and bill against allergies in the record and in this visit, and
    the bill against what the visit and the record give."""
    record = [e for e in db.list_entries(enc.patient_id, EntryStatus.CONFIRMED)
              if e.encounter_id != enc.id]
    allergies = [e for e in record if e.kind == EntryKind.ALLERGY]
    found = allergy_clashes(enc.entries, enc.bill_lines, allergies, ai)
    if enc.stay is None:  # a hospital bill is what was used there, not a prescription
        found += billed_not_prescribed(enc.entries, enc.bill_lines, enc.sources, record,
                                       enc.visit_date or consult_date(enc))
    return found


def consult_date(enc: Encounter) -> date:
    """The consult's calendar day where the patient is (created_at is stored in UTC)."""
    return enc.created_at.astimezone(ZoneInfo(get_settings().timezone)).date()


def local_today() -> date:
    """Today where the patient is."""
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def _short_ids(sources: list[SourceItem]) -> dict[str, SourceItem]:
    """Short, readable ids for the model: doc1, doc2, audio1, notes1."""
    prefix = {SourceType.IMAGE: "doc", SourceType.AUDIO: "audio", SourceType.TEXT: "notes"}
    counts: dict[str, int] = {}
    out: dict[str, SourceItem] = {}
    for s in sources:
        if not s.text:
            continue
        p = prefix.get(s.source_type, "source")
        counts[p] = counts.get(p, 0) + 1
        out[f"{p}{counts[p]}"] = s
    return out


def _sources_block(short: dict[str, SourceItem]) -> str:
    kinds = {SourceType.IMAGE: "document", SourceType.AUDIO: "consult audio transcript",
             SourceType.TEXT: "typed consult notes"}
    return "\n\n".join(
        f"=== source_id: {sid} ({kinds.get(s.source_type, 'source')}) ===\n{s.text}"
        for sid, s in short.items()
    )
