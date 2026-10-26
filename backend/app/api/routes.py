"""HTTP routes. Thin: validate input, call storage and the views, return models.

Every route checks the signed-in account (`own_patient`, `admin_only` below); patient actions
are recorded as done by the signed-in patient.
"""

from __future__ import annotations

import contextlib
import math
from datetime import date
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from app.ai.client import AIClient, AIError
from app.ai.search import SearchError, WebSearch
from app.auth import Me
from app.config import get_settings
from app.media import pdf
from app.medicine_info import medicine_info, prewarm
from app.parameters import is_known, measure_of
from app.pipeline.crosscheck import for_review
from app.pipeline.medicines import from_table, has_hindi, translate_confirmed
from app.pipeline.review import flagged
from app.pipeline.run import Media, consult_date, local_today, process_encounter
from app.schemas.account import Account
from app.schemas.medicine import MedicineInfo
from app.schemas.patient import Patient
from app.schemas.record import (
    EVENT_KINDS,
    DatePrecision,
    Discrepancy,
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    HospitalStay,
    Medication,
    Provenance,
    SourceItem,
    SourceType,
    Speaker,
    TestResult,
    Tier,
    VisitDateSource,
)
from app.storage import Storage
from app.views import (
    EmergencySummary,
    Numbers,
    PatientRecord,
    Removal,
    already_in_record,
    emergency_summary,
    numbers,
    patient_record,
    removal_preview,
)

router = APIRouter()


def storage(request: Request) -> Storage:
    return request.app.state.storage


DB = Annotated[Storage, Depends(storage)]


def ai_client(request: Request) -> AIClient:
    return request.app.state.ai


AI = Annotated[AIClient, Depends(ai_client)]


def web_search(request: Request) -> WebSearch:
    return request.app.state.search


Search = Annotated[WebSearch, Depends(web_search)]


# Upload caps for the public demo.
MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_DOCUMENTS = 6
MAX_PAGES_PER_VISIT = 20  # a photo counts as one page
MAX_NOTES_CHARS = 10_000


def _patient(db: Storage, patient_id: str) -> Patient:
    p = db.get_patient(patient_id)
    if p is None:
        raise HTTPException(404, "Patient not found")
    return p


# Who may do what (in code, every route): a patient account reaches its own record only, and
# anyone else's answers "not found", so it does not even tell that the record exists. Trusted
# people and doctors reach a record only through the emergency routes (api/access.py). Admins
# see how the app runs (Under the hood), not anyone's record.
def own_patient(db: Storage, me: Account, patient_id: str) -> Patient:
    if me.person_id is None or me.person_id != patient_id:
        raise HTTPException(404, "Patient not found")
    return _patient(db, patient_id)


def admin_only(me: Account) -> None:
    if not me.admin:
        raise HTTPException(403, "Admins only")


# ---- patients -------------------------------------------------------------
@router.get("/patients")
def list_patients(db: DB, me: Me, sandbox_id: str | None = None) -> list[Patient]:
    admin_only(me)
    return db.list_patients(sandbox_id)


@router.get("/patients/{patient_id}")
def get_patient(patient_id: str, db: DB, me: Me) -> Patient:
    return own_patient(db, me, patient_id)


# ---- encounters -----------------------------------------------------------
@router.get("/patients/{patient_id}/encounters")
def list_encounters(patient_id: str, db: DB, me: Me) -> list[Encounter]:
    own_patient(db, me, patient_id)
    return db.list_encounters(patient_id)


def _read_upload(f: UploadFile, kind: str, limit: int) -> bytes:
    mime = f.content_type or ""
    if not mime.startswith(f"{kind}/"):
        raise HTTPException(415, f"{f.filename}: expected an {kind} file, got '{mime}'")
    return _read_bytes(f, limit)


def _read_bytes(f: UploadFile, limit: int) -> bytes:
    data = f.file.read(limit + 1)  # in memory only, never written to disk
    if len(data) > limit:
        raise HTTPException(413, f"{f.filename}: larger than {limit // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, f"{f.filename}: empty file")
    return data


def _read_document(f: UploadFile) -> tuple[bytes, str, int | None]:
    """A document photo or a PDF: (data, mime, PDF page count or None for a photo)."""
    mime = f.content_type or ""
    if mime == "application/pdf" or (f.filename or "").lower().endswith(".pdf"):
        data = _read_bytes(f, MAX_PDF_BYTES)
        try:
            pages = pdf.page_count(data)
        except pdf.PdfError as exc:
            raise HTTPException(422, f"{f.filename}: {exc}") from exc
        return data, "application/pdf", pages
    if not mime.startswith("image/"):
        raise HTTPException(415, f"{f.filename}: expected a photo or a PDF, got '{mime}'")
    return _read_upload(f, "image", MAX_IMAGE_BYTES), mime, None


def _check_upload(documents: list[UploadFile], notes: str | None) -> None:
    if len(documents) > MAX_DOCUMENTS:
        raise HTTPException(413, f"At most {MAX_DOCUMENTS} documents per visit")
    if notes and len(notes) > MAX_NOTES_CHARS:
        raise HTTPException(413, f"Notes longer than {MAX_NOTES_CHARS} characters")


def _documents_into(enc: Encounter, documents: list[UploadFile]) -> list[Media]:
    """Read the uploaded documents into memory, each with a new source on the visit."""
    media: list[Media] = []
    new: list[SourceItem] = []
    for f in documents:
        data, mime, pages = _read_document(f)
        src = SourceItem(source_type=SourceType.IMAGE, filename=f.filename, pages=pages)
        new.append(src)
        media.append(Media(src.id, "image", data, mime))
    if sum(s.pages or 1 for s in new) > MAX_PAGES_PER_VISIT:
        raise HTTPException(413, f"At most {MAX_PAGES_PER_VISIT} pages per visit")
    enc.sources.extend(new)
    return media


@router.post("/patients/{patient_id}/encounters", status_code=202)
def create_encounter(
    patient_id: str,
    background: BackgroundTasks,
    db: DB,
    ai: AI,
    search: Search,
    me: Me,
    audio: Annotated[UploadFile | None, File(description="Consult recording")] = None,
    documents: Annotated[
        list[UploadFile] | None,
        File(description="Photos, screenshots or PDFs of prescriptions, bills, reports"),
    ] = None,
    notes: Annotated[str | None, Form(description="Typed consult notes")] = None,
    recording_consent: Annotated[
        bool, Form(description="With a recording: the doctor agreed to being recorded")
    ] = False,
) -> Encounter:
    """Start processing. Returns at once; poll GET /encounters/{id} for progress."""
    own_patient(db, me, patient_id)
    return _new_encounter(db, ai, search, background, patient_id, audio=audio,
                          documents=documents or [], notes=notes, consent=recording_consent)


def _new_encounter(
    db: Storage, ai: AIClient, search: WebSearch, background: BackgroundTasks, patient_id: str,
    *, audio: UploadFile | None, documents: list[UploadFile], notes: str | None, consent: bool,
) -> Encounter:
    """Check an upload, save the new visit and start reading it in the background."""
    notes = (notes or "").strip() or None
    if audio is None and not documents and not notes:
        raise HTTPException(422, "Add a recording, a document photo or typed notes")
    if audio is not None and not consent:
        raise HTTPException(422, "A recording needs the doctor's agreement. Tick the box to confirm.")
    _check_upload(documents, notes)

    enc = Encounter(patient_id=patient_id)
    # Until a document shows its own date, the visit is dated by the upload day.
    enc.visit_date, enc.visit_date_source = consult_date(enc), VisitDateSource.UPLOAD
    media = _documents_into(enc, documents)
    if audio is not None:
        data = _read_upload(audio, "audio", MAX_AUDIO_BYTES)
        src = SourceItem(source_type=SourceType.AUDIO, filename=audio.filename,
                         recording_consent=True)
        enc.sources.append(src)
        media.append(Media(src.id, "audio", data, audio.content_type or "audio/wav"))

    db.save_encounter(enc)
    background.add_task(process_encounter, db, ai, enc.id, media, notes, search=search)
    return enc


@router.post("/encounters/{encounter_id}/documents", status_code=202)
def add_documents(
    encounter_id: str,
    background: BackgroundTasks,
    db: DB,
    ai: AI,
    search: Search,
    me: Me,
    documents: Annotated[
        list[UploadFile] | None,
        File(description="Discharge summary, bills, reports: photos, screenshots or PDFs"),
    ] = None,
    notes: Annotated[str | None, Form(description="Typed notes about the stay")] = None,
    test_id: Annotated[
        str | None, Form(description="Add report: the test of this visit the report is for")
    ] = None,
) -> Encounter:
    """Documents for a hospital stay, added once the patient is out, or a report for one of a
    visit's tests (`test_id`: only the values on it are read, filed under the visit's tests).
    Only the new files are read; suggestions join the visit and are reviewed as usual. Poll
    GET /encounters/{id}."""
    enc = _encounter(db, me, encounter_id)
    if enc.stay is None and test_id is None:
        raise HTTPException(409, "Documents can be added later only to a hospital stay or as "
                                 "a report for a test")
    if test_id is not None and not any(
        e.id == test_id and e.kind == EntryKind.TEST and e.status != EntryStatus.REJECTED
        for e in enc.entries
    ):
        raise HTTPException(422, "That test is not part of this visit")
    if enc.status == EncounterStatus.PROCESSING:
        raise HTTPException(409, "This visit is being read. Try again in a minute.")
    documents = documents or []
    notes = (notes or "").strip() or None
    if test_id is not None:
        notes = None  # a report is a document
    if not documents and not notes:
        raise HTTPException(422, "Add a document or typed notes")
    _check_upload(documents, notes)
    media = _documents_into(enc, documents)
    if test_id is not None:
        for src in enc.sources[len(enc.sources) - len(media):]:
            src.report_for = test_id  # a careful re-read of these files is a report read again
    enc.status, enc.step, enc.error = EncounterStatus.PROCESSING, None, None
    enc.step_seconds, enc.models_used = {}, {}  # timings and models of this read only
    db.save_encounter(enc)
    background.add_task(process_encounter, db, ai, enc.id, media, notes, adding=True,
                        report_for=test_id, search=search)
    return enc


class EncounterView(Encounter):
    already_in_record: dict[str, str] = Field(
        default_factory=dict, description="Proposed entry id -> matching label in the record"
    )
    flagged: dict[str, str] = Field(
        default_factory=dict,
        description="Entry id -> why it needs a look of its own (check, clash, unsure): shown "
        "open on Review and never covered by Confirm all",
    )


def _findings(db: Storage, enc: Encounter, elsewhere: list[Entry]) -> list[Discrepancy]:
    """The visit's cross-check findings as they stand after the patient's review."""
    here = {e.id: e for e in enc.entries}
    return for_review(enc, elsewhere, lambda i: here.get(i) or db.get_entry(i),
                      enc.visit_date or date.today())


def _elsewhere(db: Storage, enc: Encounter) -> list[Entry]:
    """The patient's confirmed entries from other visits."""
    confirmed = db.list_entries(enc.patient_id, EntryStatus.CONFIRMED)
    return [e for e in confirmed if e.encounter_id != enc.id]


@router.get("/encounters/{encounter_id}")
def get_encounter(encounter_id: str, db: DB, me: Me) -> EncounterView:
    """Polled by the Processing and Review screens."""
    enc = _encounter(db, me, encounter_id)
    elsewhere = _elsewhere(db, enc)
    proposed = [e for e in enc.entries if e.status == EntryStatus.PROPOSED]
    enc.discrepancies = _findings(db, enc, elsewhere)
    return EncounterView(
        **enc.model_dump(), already_in_record=already_in_record(proposed, elsewhere),
        flagged=flagged(enc.entries, enc.discrepancies),
    )


def _encounter(db: Storage, me: Account, encounter_id: str) -> Encounter:
    """One of the signed-in patient's own visits."""
    enc = db.get_encounter(encounter_id)
    if enc is None or me.person_id is None or enc.patient_id != me.person_id:
        raise HTTPException(404, "Encounter not found")
    return enc


class VisitUpdate(BaseModel):
    visit_date: date | None = None
    title: str | None = None  # the visit's name; 1 to 60 characters after trimming


@router.patch("/encounters/{encounter_id}")
def update_encounter(encounter_id: str, body: VisitUpdate, db: DB, me: Me) -> Encounter:
    """The patient corrects the visit date and/or names the visit."""
    if body.visit_date is None and body.title is None:
        raise HTTPException(422, "Give a visit date or a name")
    if body.visit_date is not None and body.visit_date > date.today():
        raise HTTPException(422, "The visit date cannot be in the future")
    title = " ".join(body.title.split()) if body.title is not None else None
    if title is not None and not 1 <= len(title) <= 60:
        raise HTTPException(422, "The name must be 1 to 60 characters")
    enc = _encounter(db, me, encounter_id)
    if enc.status == EncounterStatus.PROCESSING:  # the read would write over a change made now
        raise HTTPException(409, "This visit is being read. Try again in a minute.")
    if body.visit_date is not None:
        enc.visit_date, enc.visit_date_source = body.visit_date, VisitDateSource.PATIENT
    if title is not None:
        enc.title, enc.title_source = title, "patient"
    db.save_encounter(enc)
    return db.get_encounter(encounter_id)


@router.get("/encounters/{encounter_id}/removal")
def get_removal(encounter_id: str, db: DB, me: Me) -> Removal:
    """What deleting this visit would take out of the record (shown before the patient confirms)."""
    enc = _encounter(db, me, encounter_id)
    return removal_preview(enc, _patient(db, enc.patient_id), db.list_entries(enc.patient_id),
                           db.list_encounters(enc.patient_id))


@router.delete("/encounters/{encounter_id}", status_code=204)
def delete_encounter(encounter_id: str, db: DB, me: Me) -> None:
    """The patient deletes one of their visits for good: its facts, test values and the text read
    from its files. The record is theirs, so it is not kept underneath. (The access log is a
    separate record of who opened the emergency card, and stays.)"""
    enc = _encounter(db, me, encounter_id)
    if enc.status == EncounterStatus.PROCESSING:
        raise HTTPException(409, "This visit is being read. Try again in a minute.")
    db.delete_encounter(enc.id)


@router.post("/encounters/{encounter_id}/reread", status_code=202)
def reread_encounter(
    encounter_id: str,
    background: BackgroundTasks,
    db: DB,
    ai: AI,
    search: Search,
    me: Me,
    files: Annotated[list[UploadFile], File(description="The visit's photos and recording again")],
    source_ids: Annotated[list[str], Form(description="For each file, the source it belongs to")],
    # Text, not bool: newer FastAPI reads a missing bool form field as False, whatever its default.
    careful: Annotated[
        str | None, Form(description="Read documents with thinking on (default true)")
    ] = None,
) -> Encounter:
    """Read a visit again from the browser's copy of its files (the server never kept them).

    A visit that was read re-reads its documents slowly with thinking on (`careful`):
    suggestions not yet reviewed are replaced, confirmed ones stay. A visit that failed is read
    again from the start, the normal way.
    """
    enc = _encounter(db, me, encounter_id)
    if enc.status == EncounterStatus.PROCESSING:
        raise HTTPException(409, "This visit is still being read")
    if len(files) != len(source_ids) or not files:
        raise HTTPException(422, "Send each file with the source it belongs to")
    by_id = {s.id: s for s in enc.sources}
    media: list[Media] = []
    for f, sid in zip(files, source_ids, strict=True):
        src = by_id.get(sid)
        if src is None or src.source_type not in (SourceType.IMAGE, SourceType.AUDIO):
            raise HTTPException(422, f"{f.filename}: not a photo or recording of this visit")
        if src.source_type == SourceType.IMAGE:
            data, mime, _ = _read_document(f)
            media.append(Media(sid, "image", data, mime))
        else:
            data = _read_upload(f, "audio", MAX_AUDIO_BYTES)
            media.append(Media(sid, "audio", data, f.content_type or ""))

    rerun = enc.status != EncounterStatus.FAILED
    if rerun and careful is not None and careful.strip().lower() in ("false", "0", "off", "no"):
        raise HTTPException(409, "This visit was read already")
    if not rerun:
        # A retry starts over from these files; typed notes are already a source.
        notes = next((s.text for s in enc.sources if s.source_type == SourceType.TEXT), None)
        enc.sources = [s for s in enc.sources if s.source_type != SourceType.TEXT]
        enc.entries, enc.quote_failures, enc.bill_lines = [], [], []
    enc.status, enc.step, enc.error = EncounterStatus.PROCESSING, None, None
    enc.step_seconds, enc.models_used = {}, {}  # timings and models of this read only
    db.save_encounter(enc)
    if rerun:
        background.add_task(process_encounter, db, ai, enc.id, media, careful=True, search=search)
    else:
        background.add_task(process_encounter, db, ai, enc.id, media, notes, search=search)
    return enc


# ---- entries: confirm, reject, edit, stop ---------------------------------

class EntryAction(BaseModel):
    action: Literal["confirm", "reject", "edit", "stop"]
    note: str | None = None
    # edit only
    label: str | None = None
    fields: dict[str, str] | None = None
    medication: dict[str, object] | None = None
    result: dict[str, object] | None = None
    # stop only; defaults to today
    stopped_on: date | None = None


@router.patch("/entries/{entry_id}")
def act_on_entry(
    entry_id: str, body: EntryAction, db: DB, ai: AI, search: Search, background: BackgroundTasks,
    me: Me,
) -> Entry:
    e = db.get_entry(entry_id)
    if e is None or e.patient_id is None or e.patient_id != me.person_id:
        raise HTTPException(404, "Entry not found")
    _apply_action(db, ai, search, background, e, body, me.person_id)  # the patient, as in every history
    db.save_entry(e)
    return e


def _apply_action(db: Storage, ai: AIClient, search: WebSearch, background: BackgroundTasks,
                  e: Entry, body: EntryAction, by: str) -> None:
    """Confirm, reject, edit or stop one of the patient's own entries (the caller checks who is
    asking and passes `by` from the session). Keeps the history through set_status and
    apply_edit. The caller saves the entry."""
    enc = db.get_encounter(e.encounter_id) if e.encounter_id else None
    if enc is not None and enc.status == EncounterStatus.PROCESSING:
        # The re-read saves this visit's entries when it ends, which would undo the change.
        raise HTTPException(409, "This visit is being read again. Try again in a minute.")

    if body.action == "confirm":
        if e.status == EntryStatus.CONFIRMED:
            raise HTTPException(409, "Already confirmed")
        _confirm(e, by, body.note, db, ai, search, background)
    elif body.action == "reject":
        if e.status == EntryStatus.REJECTED:
            raise HTTPException(409, "Already rejected")
        _reject(e, by, body.note, db)
    elif body.action == "edit":
        if e.status == EntryStatus.REJECTED:
            raise HTTPException(409, "Rejected entries cannot be edited")
        if e.kind == EntryKind.RESULT and body.result:
            _check_value_edit(body.result)
        try:
            e.apply_edit(by, label=body.label, fields=body.fields, medication=body.medication,
                         result=body.result if e.kind == EntryKind.RESULT else None)
        except ValidationError as exc:
            raise HTTPException(422, f"Invalid details: {exc.errors()}") from exc
    else:  # stop
        if e.kind != EntryKind.MEDICATION or e.status != EntryStatus.CONFIRMED:
            raise HTTPException(409, "Only a confirmed medicine can be marked stopped")
        stopped = (body.stopped_on or date.today()).isoformat()
        e.apply_edit(by, medication={"stopped_on": stopped})


def _confirm(e: Entry, by: str, note: str | None, db: Storage, ai: AIClient, search: WebSearch,
             background: BackgroundTasks) -> None:
    """The patient confirms an entry, with what follows from it. One step for the single and the
    bulk route; the caller saves the entry."""
    e.set_status(EntryStatus.CONFIRMED, by=by, note=note)
    if e.kind == EntryKind.TEST:  # "Add it after all" brings back the values removed with it
        _with_test(db, e, EntryStatus.REJECTED, EntryStatus.CONFIRMED, by)
    if e.kind == EntryKind.MEDICATION:  # its summary is ready by the time it is tapped
        generics = e.medication.generic_names if e.medication else []
        background.add_task(prewarm, e.label, generics, db, ai, search)
    # The record is in English (the quote keeps the original words).
    if has_hindi(e):
        background.add_task(translate_later, e.id, db, ai)


def _reject(e: Entry, by: str, note: str | None, db: Storage) -> None:
    """The patient turns an entry down. The caller saves the entry."""
    e.set_status(EntryStatus.REJECTED, by=by, note=note)
    if e.kind == EntryKind.TEST:  # the values read for this test go with it
        _with_test(db, e, EntryStatus.CONFIRMED, EntryStatus.REJECTED, by)


class ReviewMany(BaseModel):
    action: Literal["confirm", "reject"]
    entry_ids: list[str] = Field(min_length=1, max_length=100)


class Skipped(BaseModel):
    id: str
    reason: str = Field(description="not_waiting | check | clash | unsure")


class ReviewResult(BaseModel):
    entries: list[Entry]
    skipped: list[Skipped]


@router.post("/encounters/{encounter_id}/review")
def review_many(
    encounter_id: str, body: ReviewMany, db: DB, ai: AI, search: Search,
    background: BackgroundTasks, me: Me,
) -> ReviewResult:
    """Confirm or turn down several waiting suggestions of one visit at once (a box on Review).

    Only entries of this visit that are still waiting are touched. Confirm leaves out any entry
    that needs a look of its own (`flagged`), so a warning is never skipped; turning one down is
    always allowed."""
    enc = _encounter(db, me, encounter_id)
    return _review(db, ai, search, background, enc, body, me.person_id)


def _review(db: Storage, ai: AIClient, search: WebSearch, background: BackgroundTasks,
            enc: Encounter, body: ReviewMany, by: str) -> ReviewResult:
    """The bulk confirm or turn-down (the caller has checked who is asking and passes `by` from
    the session)."""
    if enc.status == EncounterStatus.PROCESSING:
        raise HTTPException(409, "This visit is being read again. Try again in a minute.")
    flags = flagged(enc.entries, _findings(db, enc, _elsewhere(db, enc)))
    here = {e.id: e for e in enc.entries}
    done: list[Entry] = []
    skipped: list[Skipped] = []
    for entry_id in dict.fromkeys(body.entry_ids):
        e = here.get(entry_id)
        if e is None or e.status != EntryStatus.PROPOSED:
            skipped.append(Skipped(id=entry_id, reason="not_waiting"))
        elif body.action == "confirm" and entry_id in flags:
            skipped.append(Skipped(id=entry_id, reason=flags[entry_id]))
        else:
            if body.action == "confirm":
                _confirm(e, by, None, db, ai, search, background)
            else:
                _reject(e, by, None, db)
            db.save_entry(e)
            done.append(e)
    return ReviewResult(entries=done, skipped=skipped)


def _check_value_edit(changes: dict[str, object]) -> None:
    """A test value's edit may change only its own fields; a number is a real number, not below
    zero; and its date is not in the future (today where the patient is, not the server's day)."""
    unknown = sorted(set(changes) - set(TestResult.model_fields))
    if unknown:
        raise HTTPException(422, f"Not a field of a test value: {', '.join(unknown)}")
    for name in ("value", "ref_low", "ref_high"):
        v = changes.get(name)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int | float)
                              or not math.isfinite(v) or v < 0):
            raise HTTPException(422, "A number must be zero or more")
    if str(changes.get("measured_on") or "") > local_today().isoformat():
        raise HTTPException(422, "The report date cannot be in the future")


WITH_TEST = "With its test"


def _with_test(db: Storage, test: Entry, was: EntryStatus, now: EntryStatus, by: str) -> None:
    """A test turned down takes the values read for it along, and "Add it after all" brings back
    the ones it took. Values the patient edited or removed themselves are left alone."""
    for v in db.list_entries(test.patient_id or "", was):
        if v.kind != EntryKind.RESULT or test.id not in v.links or v.edits:
            continue
        last = v.status_history[-1] if v.status_history else None
        if now == EntryStatus.CONFIRMED and (last is None or last.note != WITH_TEST):
            continue
        v.set_status(now, by=by, note=WITH_TEST if now == EntryStatus.REJECTED else None)
        db.save_entry(v)


def translate_later(entry_id: str, db: Storage, ai: AIClient) -> None:
    """Background job after confirming an entry that still has Hindi in it. A failure leaves
    the entry as the patient confirmed it."""
    e = db.get_entry(entry_id)
    if e is None or e.status != EntryStatus.CONFIRMED:
        return
    with contextlib.suppress(Exception):
        if translate_confirmed(e, ai):
            db.update_entry(e)  # not save: if the visit was deleted meanwhile, nothing comes back


# ---- events added by hand -------------------------------------------------
class NewEvent(BaseModel):
    kind: EntryKind
    label: str = Field(min_length=1)
    occurred_on: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2}(-\d{2})?)?$")
    date_precision: DatePrecision | None = None
    unverified: bool = Field(default=True, description="From memory, not from a document")
    fields: dict[str, str] = Field(default_factory=dict)


@router.post("/patients/{patient_id}/events", status_code=201)
def add_event(patient_id: str, body: NewEvent, db: DB, me: Me) -> Entry:
    """The patient's own statement, so it enters the record confirmed (not a model proposal)."""
    own_patient(db, me, patient_id)
    if body.kind not in EVENT_KINDS:
        raise HTTPException(422, f"{body.kind.value} is not an event kind")
    e = Entry(
        patient_id=patient_id,
        provenance=[Provenance(source_id="manual", source_type=SourceType.MANUAL)],
        **body.model_dump(),
    )
    e.set_status(EntryStatus.CONFIRMED, by=patient_id, note="added by hand")
    db.save_entry(e)
    return e


# ---- something the patient adds to a visit ----------------------------------
ADDABLE_KINDS = {
    EntryKind.MEDICATION, EntryKind.TEST, EntryKind.NOTE, EntryKind.SYMPTOM, EntryKind.DIAGNOSIS,
    EntryKind.ALLERGY, EntryKind.FOLLOW_UP,
}
ADDED_BY_PATIENT = "added by the patient"  # the status note of an addition (the doctor reads it)
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class NewVisitMedication(BaseModel):
    """What the patient can say about a medicine they add: all optional."""

    dose: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    doses_per_day: float | None = Field(default=None, ge=0, le=24, allow_inf_nan=False)
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    ongoing: bool = False


class NewVisitEntry(BaseModel):
    kind: EntryKind
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    fields: dict[Annotated[str, StringConstraints(max_length=40)], ShortText] = Field(
        default_factory=dict, max_length=10)
    medication: NewVisitMedication | None = None


def _added_tier(kind: EntryKind) -> Tier:
    """Policy, in code: as for what the pipeline reads (`extract.tier_for`). A diagnosis the
    patient adds is not a chronic condition on the card."""
    if kind in (EntryKind.MEDICATION, EntryKind.ALLERGY):
        return Tier.EMERGENCY_CARD
    return Tier.CLINICAL_SUMMARY if kind == EntryKind.DIAGNOSIS else Tier.FULL_RECORD


@router.post("/encounters/{encounter_id}/entries", status_code=201)
def add_to_visit(
    encounter_id: str, body: NewVisitEntry, db: DB, ai: AI, search: Search,
    background: BackgroundTasks, me: Me,
) -> Entry:
    """The patient adds something the doctor (or the reading) missed, to one of their visits.
    Their own statement, so it is theirs and confirmed at once, marked as added by them; they
    can edit or remove it as any entry of theirs."""
    enc = _encounter(db, me, encounter_id)
    if enc.status == EncounterStatus.PROCESSING:
        raise HTTPException(409, "This visit is being read. Try again in a minute.")
    if enc.status != EncounterStatus.READY:  # only a visit that was read takes additions
        raise HTTPException(409, "This visit could not be read, so nothing can be added to it.")
    if body.kind not in ADDABLE_KINDS:
        raise HTTPException(422, f"{body.kind.value} cannot be added to a visit")
    if body.medication is not None and body.kind != EntryKind.MEDICATION:
        raise HTTPException(422, "Medicine details belong to a medicine")
    medication = None
    if body.kind == EntryKind.MEDICATION:
        details = body.medication or NewVisitMedication()
        medication = Medication(
            dose=details.dose or None, doses_per_day=details.doses_per_day,
            duration_days=None if details.ongoing else details.duration_days,
            ongoing=details.ongoing,
            # A course counts from the visit's day, as for what the pipeline reads.
            started_on=(enc.visit_date or enc.created_at.date()) if details.duration_days else None,
        )
    e = Entry(
        patient_id=enc.patient_id, encounter_id=enc.id, kind=body.kind, label=body.label,
        fields={k: v for k, v in body.fields.items() if v}, medication=medication,
        provenance=[Provenance(source_id="manual", source_type=SourceType.MANUAL)],
        tier=_added_tier(body.kind), said_by=Speaker.PATIENT,
    )
    if medication is not None:
        # What a confirm does for a medicine: its generic names (so the allergy cross-check sees
        # it; the curated table only, no model call here) and its summary, ready by the first tap.
        generics = from_table(body.label)
        if generics:
            medication.generic_names, medication.generic_from = list(generics), "table"
    e.set_status(EntryStatus.CONFIRMED, by=me.person_id or "", note=ADDED_BY_PATIENT)
    db.save_entry(e)
    if medication is not None:
        background.add_task(prewarm, e.label, medication.generic_names, db, ai, search)
    return e


# ---- my health numbers -----------------------------------------------------
@router.get("/patients/{patient_id}/numbers")
def get_numbers(patient_id: str, db: DB, me: Me) -> Numbers:
    """Every number in the record by measure and unit, over time, with the range each report
    printed. Worked out by code from the confirmed values; nothing is converted or interpreted."""
    own_patient(db, me, patient_id)
    return numbers(db.list_entries(patient_id), db.list_encounters(patient_id))


class ReadingIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    value: float = Field(ge=0, allow_inf_nan=False)
    # As the screen sent it (a preset fills in its usual unit, which the patient can change).
    # Never filled in here: a unit nobody gave is not known, and a number in another unit must not
    # be taken for the usual one.
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None


class NewReadings(BaseModel):
    readings: list[ReadingIn] = Field(min_length=1, max_length=10)  # a blood pressure is two
    on: date | None = Field(default=None, description="The day it was taken; today if left out")


@router.post("/patients/{patient_id}/readings", status_code=201)
def add_readings(patient_id: str, body: NewReadings, db: DB, me: Me) -> list[Entry]:
    """Readings the patient took themselves (a home blood pressure, their weight). Their own
    statement, so they enter the record confirmed, marked as added by hand; a name the app knows
    is stored under its usual name, so the reading lands on the same chart as the reports'."""
    own_patient(db, me, patient_id)
    today = local_today()  # where the patient is, not the server's day
    on = body.on or today
    if on > today:
        raise HTTPException(422, "The date cannot be in the future")
    saved = []
    for r in body.readings:
        known = measure_of(r.name)
        e = Entry(
            patient_id=patient_id,
            kind=EntryKind.RESULT,
            label=known.name if is_known(r.name) else " ".join(r.name.split()),
            provenance=[Provenance(source_id="manual", source_type=SourceType.MANUAL)],
            tier=Tier.FULL_RECORD,
            said_by=Speaker.PATIENT,
            result=TestResult(value=r.value, unit=r.unit or None, measured_on=on),
        )
        e.set_status(EntryStatus.CONFIRMED, by=patient_id, note="added by hand")
        db.save_entry(e)
        saved.append(e)
    return saved


# ---- emergency and hospital stays, logged by the patient ------------------
STAY_LABEL = {"emergency_visit": "Emergency visit", "admission": "Hospital admission"}


class NewStay(BaseModel):
    kind: Literal["emergency_visit", "admission"]
    place: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=200)
    started_on: date
    ended_on: date | None = None


class StayUpdate(BaseModel):
    """Only the fields sent are changed. `ended_on` closes the stay (the day they went home)."""

    place: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=200)
    ended_on: date | None = None


def _check_stay_dates(started: date, ended: date | None) -> None:
    today = date.today()
    if started > today or (ended and ended > today):
        raise HTTPException(422, "A stay cannot be in the future")
    if ended and ended < started:
        raise HTTPException(422, "The day you went home is before the day it started")


def _clean(text: str | None) -> str | None:
    return " ".join(text.split()) or None if text else None


@router.post("/patients/{patient_id}/stays", status_code=201)
def log_stay(patient_id: str, body: NewStay, db: DB, me: Me) -> Encounter:
    """The patient logs an emergency visit or a hospital admission, during it or after. Their own
    statement, so its record entry is confirmed and marked unverified (no document yet).
    Nothing else starts a stay: not a doctor opening the card, not a trusted person."""
    own_patient(db, me, patient_id)
    _check_stay_dates(body.started_on, body.ended_on)
    place, reason = _clean(body.place), _clean(body.reason)
    entry = Entry(
        kind=EntryKind(body.kind),
        label=reason or STAY_LABEL[body.kind],
        fields={k: v for k, v in {"place": place,
                                  "until": body.ended_on.isoformat() if body.ended_on else None
                                  }.items() if v},
        provenance=[Provenance(source_id="manual", source_type=SourceType.MANUAL)],
        occurred_on=body.started_on.isoformat(),
        date_precision=DatePrecision.DAY,
        unverified=True,
        tier=Tier.CLINICAL_SUMMARY,
    )
    entry.set_status(EntryStatus.CONFIRMED, by=patient_id, note="logged by the patient")
    enc = Encounter(
        patient_id=patient_id,
        status=EncounterStatus.READY,
        visit_date=body.started_on,
        visit_date_source=VisitDateSource.PATIENT,
        entries=[entry],
        stay=HospitalStay(kind=body.kind, place=place, reason=reason, started_on=body.started_on,
                          ended_on=body.ended_on, entry_id=entry.id),
    )
    db.save_encounter(enc)
    return db.get_encounter(enc.id)


@router.patch("/encounters/{encounter_id}/stay")
def update_stay(encounter_id: str, body: StayUpdate, db: DB, me: Me) -> Encounter:
    """The patient changes the place or reason, or closes the stay with the day they went home.
    The record entry changes through its edit history, as any patient edit does."""
    enc = _encounter(db, me, encounter_id)
    if enc.stay is None:
        raise HTTPException(409, "This visit is not a hospital stay")
    if enc.status == EncounterStatus.PROCESSING:
        # The read saves the stay when it ends, which would undo the change.
        raise HTTPException(409, "This stay's documents are being read. Try again in a minute.")
    stay, sent = enc.stay, body.model_fields_set
    if "ended_on" in sent:
        _check_stay_dates(stay.started_on, body.ended_on)
        stay.ended_on = body.ended_on
    if "place" in sent:
        stay.place = _clean(body.place)
    if "reason" in sent:
        stay.reason = _clean(body.reason)

    entry = next((e for e in enc.entries if e.id == stay.entry_id), None)
    if entry is not None:
        fields = {"place": stay.place or "",
                  "until": stay.ended_on.isoformat() if stay.ended_on else ""}
        entry.apply_edit(me.person_id, label=stay.reason or STAY_LABEL[stay.kind],
                         fields={k: v for k, v in fields.items() if v or k in entry.fields})
        entry.fields = {k: v for k, v in entry.fields.items() if v}
    db.save_encounter(enc)
    return db.get_encounter(encounter_id)


# ---- record and emergency summary -----------------------------------------
@router.get("/patients/{patient_id}/record")
def get_record(patient_id: str, db: DB, me: Me) -> PatientRecord:
    patient = own_patient(db, me, patient_id)
    return patient_record(patient, db.list_entries(patient_id), db.list_encounters(patient_id))


@router.get("/patients/{patient_id}/emergency")
def get_emergency(patient_id: str, db: DB, me: Me) -> EmergencySummary:
    """The patient's own card. Doctors get it through emergency access, which is logged."""
    patient = own_patient(db, me, patient_id)
    return emergency_summary(patient, db.list_entries(patient_id), db.list_encounters(patient_id))


# ---- under the hood -------------------------------------------------------
@router.get("/meta")
def meta(me: Me) -> dict[str, object]:
    admin_only(me)
    s = get_settings()
    return {
        "perceive": {"provider": s.perceive.provider, "model": s.perceive.model},
        "reason": {"provider": s.reason.provider, "model": s.reason.model},
    }


# ---- medicine summaries -----------------------------------------------------
@router.get("/medicine-info")
def get_medicine_info(
    db: DB,
    ai: AI,
    search: Search,
    me: Me,  # signed in: lookups cost Tavily credits and model calls
    name: Annotated[str, Query(min_length=1, max_length=80, description="As in the record")],
    generics: Annotated[list[str] | None, Query(description="Active ingredients")] = None,
) -> MedicineInfo:
    """General information about a medicine from trusted sites, kept for 30 days. Only the
    medicine's name and active ingredients are sent out, nothing about the patient."""
    if not search.available:
        raise HTTPException(503, "Medicine summaries are not set up on this server")
    generics = [g.strip() for g in generics or [] if g.strip()][:5]
    try:
        return medicine_info(name.strip(), generics, db, ai, search)
    except (SearchError, AIError) as exc:
        raise HTTPException(502, "Could not look this up right now. Try again in a minute.") from exc
