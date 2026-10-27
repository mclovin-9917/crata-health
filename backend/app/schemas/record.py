"""Core record shapes.

Principles baked in from day one:
1. Every extracted fact carries provenance (which audio second / which image it came from).
2. AI only *proposes*. An entry is not part of the record until the patient confirms it.
3. Status changes are kept as history, never overwritten.
4. Source media is never stored here, only metadata about it and the text the model read or
   heard from it (process, then purge).
5. The emergency view is derived from CONFIRMED entries only.
6. Patient edits never overwrite the model's original value or quote; both are kept.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid.uuid4().hex


class SourceType(str, Enum):
    AUDIO = "audio"
    IMAGE = "image"
    TEXT = "text"
    MANUAL = "manual"


class EntryKind(str, Enum):
    # Plain facts
    MEDICATION = "medication"
    DIAGNOSIS = "diagnosis"
    SYMPTOM = "symptom"  # what the patient describes; never a diagnosis
    TEST = "test"
    RESULT = "result"  # one measured value from a report or a prescription (never on the card)
    ALLERGY = "allergy"
    FOLLOW_UP = "follow_up"
    NOTE = "note"
    # Dated events (shown on the timeline)
    EMERGENCY_VISIT = "emergency_visit"
    ADMISSION = "admission"
    PROCEDURE = "procedure"
    SURGERY = "surgery"
    IMPLANT = "implant"


EVENT_KINDS = {
    EntryKind.EMERGENCY_VISIT,
    EntryKind.ADMISSION,
    EntryKind.PROCEDURE,
    EntryKind.SURGERY,
    EntryKind.IMPLANT,
}

# The patient confirms these before they are in the record (decided 2026-10-19): they feed the
# emergency card and the safety checks. Every other kind is read as it is (`added_as_read`).
ESSENTIAL_KINDS = {EntryKind.MEDICATION, EntryKind.ALLERGY, EntryKind.DIAGNOSIS} | EVENT_KINDS

# Surgeries and implants are pinned on the emergency timeline.
PINNED_KINDS = {EntryKind.SURGERY, EntryKind.IMPLANT}


class EntryStatus(str, Enum):
    PROPOSED = "proposed"  # AI suggestion, not yet in the record
    CONFIRMED = "confirmed"  # patient approved
    REJECTED = "rejected"


class DatePrecision(str, Enum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class Tier(str, Enum):
    """Which protection tier an entry belongs to (see the tiered storage decision)."""

    EMERGENCY_CARD = "emergency_card"  # allergies, current meds, chronic conditions, implants
    CLINICAL_SUMMARY = "clinical_summary"  # admissions, diagnoses, notes
    FULL_RECORD = "full_record"  # everything else


class Speaker(str, Enum):
    """Who stated a fact. Only a doctor or a document can make a diagnosis or a prescription."""

    DOCTOR = "doctor"
    PATIENT = "patient"
    DOCUMENT = "document"


class Provenance(BaseModel):
    source_id: str = Field(description="Which uploaded item this came from")
    source_type: SourceType
    locator: str | None = Field(
        default=None, description="Where in the source, e.g. '00:42-00:51' or 'line 3'"
    )
    quote: str | None = Field(default=None, description="Exact words or text relied on")


class EntryCheck(str, Enum):
    """Something code noticed in a proposal. Shown as 'check this' on the Review screen; the
    wording there is cautious and never tells the patient to start or stop a medicine."""

    MEDICINE_UNNAMED = "medicine_unnamed"  # "anti-malarial tablets": no specific medicine named
    NAME_NOT_IN_QUOTE = "name_not_in_quote"  # the medicine or allergy name isn't in its quote
    NOT_ENGLISH = "not_english"  # details still in Hindi after one translation pass


class MedicationState(str, Enum):
    CURRENT = "current"  # end date not reached, or long-term
    END_UNKNOWN = "end_unknown"  # no duration or start date: shown on the card, labelled
    ENDED = "ended"  # course finished
    STOPPED = "stopped"  # patient marked it stopped


class Medication(BaseModel):
    """Structured details for a medication entry. Date and quantity arithmetic stays in code."""

    dose: str | None = Field(default=None, description="e.g. '500 mg'")
    doses_per_day: float | None = Field(
        default=None, ge=0, description="3 for 'three times a day'; None for 'as needed'"
    )
    duration_days: int | None = Field(default=None, ge=1)
    started_on: date | None = None
    ongoing: bool = Field(default=False, description="Long-term medicine with no planned end")
    stopped_on: date | None = Field(default=None, description="Set when the patient stops it")
    generic_names: list[str] = Field(
        default_factory=list, description="Active ingredients, lowercase, e.g. ['paracetamol']"
    )
    generic_from: Literal["table", "search", "model"] | None = Field(
        default=None,
        description="Curated brand table; a trusted site found by search (read by the model, "
        "checked by code); or the model's own match for an unlisted brand",
    )
    generic_source: str | None = Field(default=None, description="'search': the page's URL")

    def end_date(self) -> date | None:
        """Last day of the course, counting the start day as day 1."""
        if self.ongoing or self.started_on is None or self.duration_days is None:
            return None
        return self.started_on + timedelta(days=self.duration_days - 1)

    def state(self, today: date) -> MedicationState:
        if self.stopped_on is not None and self.stopped_on <= today:
            return MedicationState.STOPPED
        if self.ongoing:
            return MedicationState.CURRENT
        end = self.end_date()
        if end is None:
            return MedicationState.END_UNKNOWN
        return MedicationState.CURRENT if today <= end else MedicationState.ENDED


# Medication states that still belong on the emergency card.
SHOWN_ON_CARD = {MedicationState.CURRENT, MedicationState.END_UNKNOWN}


class TestResult(BaseModel):
    """One measured value, as printed on a report or prescription. Comparing it with the
    printed range is plain code (`position`); nothing here is the model's judgement."""

    __test__ = False  # not a pytest class

    value: float | None = Field(
        default=None, allow_inf_nan=False, description="The number, without its unit"
    )
    value_text: str | None = Field(
        default=None, description="A result in words ('Nil', 'Non-reactive') or '<0.5'"
    )
    unit: str | None = None
    ref_low: float | None = Field(
        default=None, allow_inf_nan=False, description="Lower end of the printed range"
    )
    ref_high: float | None = Field(
        default=None, allow_inf_nan=False, description="Upper end of the printed range"
    )
    ref_text: str | None = Field(default=None, description="The printed range, as written")
    measured_on: date | None = Field(default=None, description="Report date, else the upload day")

    def position(self) -> Literal["low", "high", "within"] | None:
        """Where the number sits against the report's own range; None when it cannot be said."""
        if self.value is None:
            return None
        if self.ref_low is not None and self.value < self.ref_low:
            return "low"
        if self.ref_high is not None and self.value > self.ref_high:
            return "high"
        return "within" if self.ref_low is not None or self.ref_high is not None else None


class FieldEdit(BaseModel):
    """One patient edit to one field. The model's original value stays visible."""

    field: str = Field(description="'label', 'fields.<name>' or 'medication.<name>'")
    old: str | None
    new: str | None
    at: datetime = Field(default_factory=_now)
    by: str


class StatusChange(BaseModel):
    """One step in an entry's history. Never deleted."""

    status: EntryStatus
    at: datetime = Field(default_factory=_now)
    by: str = Field(default="system", description="user id, or 'system' for model proposals")
    note: str | None = None


class Entry(BaseModel):
    id: str = Field(default_factory=_id)
    patient_id: str | None = None
    encounter_id: str | None = Field(default=None, description="None for events added by hand")
    kind: EntryKind
    label: str = Field(description="Short human label, e.g. 'Amoxicillin 500 mg'")
    fields: dict[str, str] = Field(default_factory=dict)
    provenance: list[Provenance] = Field(default_factory=list)
    status: EntryStatus = EntryStatus.PROPOSED
    status_history: list[StatusChange] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    tier: Tier = Tier.FULL_RECORD
    said_by: Speaker | None = Field(
        default=None,
        description="None for events added by hand; PATIENT for what the patient added to a visit "
        "and for their own readings",
    )

    # Medication entries only
    medication: Medication | None = None

    # Test values only
    result: TestResult | None = None
    added_as_read: bool = Field(
        default=False,
        description="Entered the record without a confirm tap (a value read from a report); the "
        "status history says so. Always editable and removable.",
    )

    # Patient edits, oldest first. Provenance is never edited.
    edits: list[FieldEdit] = Field(default_factory=list)

    # Dated events only
    occurred_on: str | None = Field(
        default=None, description="ISO partial date: '2024', '2024-03' or '2024-03-12'"
    )
    date_precision: DatePrecision | None = None
    unverified: bool = Field(
        default=False, description="True when the date or event came from memory, not a document"
    )

    # Links to other entries (e.g. a surgery to the diagnosis behind it)
    links: list[str] = Field(default_factory=list)

    # Things code noticed that the patient should look at before confirming.
    checks: list[EntryCheck] = Field(default_factory=list)

    @property
    def is_event(self) -> bool:
        return self.kind in EVENT_KINDS

    def set_status(self, status: EntryStatus, by: str, note: str | None = None) -> None:
        """Change status and keep the history. Use this, never assign .status directly."""
        self.status = status
        self.status_history.append(StatusChange(status=status, by=by, note=note))

    @property
    def edited_by_patient(self) -> bool:
        """The patient changed it themselves. A system translation does not count (an entry with
        no patient yet counts any edit)."""
        if self.patient_id is None:
            return bool(self.edits)
        return any(ed.by == self.patient_id for ed in self.edits)

    def apply_edit(
        self,
        by: str,
        *,
        label: str | None = None,
        fields: dict[str, str] | None = None,
        medication: dict[str, object] | None = None,
        result: dict[str, object] | None = None,
    ) -> None:
        """Change values and record each change. Status is untouched: the patient still confirms."""
        changes: list[tuple[str, object, object]] = []
        if label is not None and label != self.label:
            changes.append(("label", self.label, label))
            self.label = label
        for name, value in (fields or {}).items():
            if self.fields.get(name) != value:
                changes.append((f"fields.{name}", self.fields.get(name), value))
                self.fields[name] = value
        if medication:
            current = self.medication or Medication()
            updated = Medication.model_validate({**current.model_dump(), **medication})
            for name in medication:
                old, new = getattr(current, name), getattr(updated, name)
                if old != new:
                    changes.append((f"medication.{name}", old, new))
            self.medication = updated
        if result:
            current = self.result or TestResult()
            updated = TestResult.model_validate({**current.model_dump(), **result})
            for name in result:
                old, new = getattr(current, name), getattr(updated, name)
                if old != new:
                    changes.append((f"result.{name}", old, new))
            self.result = updated
        for name, old, new in changes:
            self.edits.append(FieldEdit(field=name, old=_text(old), new=_text(new), by=by))


def _text(value: object) -> str | None:
    return None if value is None else str(value)


class Discrepancy(BaseModel):
    """Two sources disagree (e.g. doctor said 5 days, bill shows 10 tablets)."""

    id: str = Field(default_factory=_id)
    check: str = Field(description="allergy_clash | billed_not_prescribed | quantity_mismatch")
    message: str
    entry_ids: list[str] = Field(default_factory=list)
    bill_line_ids: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    severity: str = Field(default="review", description="info | review | urgent")
    matched_by: Literal["table", "model"] = Field(
        default="table", description="'model' when the match relied on the model's answer"
    )


class DocumentKind(str, Enum):
    """What a document photo is. Bills are read into bill lines, never into record entries."""

    PRESCRIPTION = "prescription"
    BILL = "bill"
    REPORT = "report"  # lab report or discharge summary
    IMAGING = "imaging"  # X-ray, CT, MRI, ultrasound, echo or other scan report
    OTHER = "other"


class BillLine(BaseModel):
    """One line of a pharmacy bill. Not a record entry: what was sold, not what was prescribed.
    The cross-checks compare these with the prescription and the consult."""

    id: str = Field(default_factory=_id)
    name: str = Field(description="As printed, e.g. 'Pantocid 40 Tab'")
    quantity: float | None = Field(default=None, ge=0, description="The number in the Qty column")
    unit: str | None = Field(default=None, description="As printed, e.g. 'tab', 'strip', 'bottle'")
    units_per_pack: int | None = Field(
        default=None, ge=1, description="Printed pack size, e.g. 10 for '1 strip of 10'"
    )
    is_drug: bool = Field(description="False for syringes, consumables, fees (decided in code)")
    generic_names: list[str] = Field(default_factory=list)
    generic_from: Literal["table", "search", "model"] | None = None
    generic_source: str | None = None
    provenance: Provenance


class TranscriptSegment(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    text: str


class SourceItem(BaseModel):
    """Metadata about an uploaded item. The raw media itself is NOT stored here.

    `text` is what the model read or heard (document reading or transcript). Quotes are checked
    against it, and the proof screen highlights them in it.
    """

    id: str = Field(default_factory=_id)
    source_type: SourceType
    filename: str | None = None
    added_at: datetime = Field(default_factory=_now)
    text: str | None = None
    segments: list[TranscriptSegment] = Field(
        default_factory=list, description="Audio only: timestamped transcript pieces"
    )
    # A careful re-read adds a new source for the same file, so entries already confirmed keep
    # the text their quotes came from.
    reread_of: str | None = Field(default=None, description="The source this re-reads")
    careful: bool = Field(default=False, description="Documents read with thinking on")
    document_kind: DocumentKind | None = Field(
        default=None, description="Documents only: prescription, bill, report, imaging or other"
    )
    pages: int | None = Field(default=None, description="PDFs only: number of pages")
    page_texts: list[str] = Field(
        default_factory=list, description="PDFs only: each page's text (text layer or reading)"
    )
    page_kinds: list[DocumentKind] = Field(
        default_factory=list, description="Multi-page PDFs only: what each page is"
    )
    recording_consent: bool | None = Field(
        default=None, description="Audio only: the patient noted that the doctor agreed to it"
    )
    report_for: str | None = Field(
        default=None,
        description="Documents added with 'Add report': the test entry they are the report for. "
        "A careful re-read treats them the same way (values only, the visit's date untouched).",
    )


class EncounterStatus(str, Enum):
    PROCESSING = "processing"
    READY = "ready"  # waiting for the patient's review
    FAILED = "failed"


class VisitDateSource(str, Enum):
    DOCUMENT = "document"
    UPLOAD = "upload"
    PATIENT = "patient"


class HospitalStay(BaseModel):
    """An emergency visit or a hospital admission the patient logged themselves. It is the
    patient's own statement, so it enters the record as theirs (`entry_id`, confirmed and
    unverified); nothing else can start one. Open until the patient adds the day they left."""

    kind: Literal["emergency_visit", "admission"]
    place: str | None = Field(default=None, description="Hospital or clinic, as the patient wrote it")
    reason: str | None = Field(default=None, description="What happened, in the patient's words")
    started_on: date
    ended_on: date | None = Field(default=None, description="The day they went home; None while open")
    entry_id: str = Field(description="The patient's own event entry for this stay")
    # Read off a discharge summary; only a suggestion until the patient uses it.
    suggested_end: date | None = None
    suggested_end_quote: str | None = None
    suggested_end_source_id: str | None = None

    @property
    def open(self) -> bool:
        return self.ended_on is None


class Encounter(BaseModel):
    """One doctor visit or one record event, with everything extracted from it."""

    id: str = Field(default_factory=_id)
    patient_id: str
    created_at: datetime = Field(default_factory=_now)
    # The day of the visit: printed on a document if there is one, else the upload day (local
    # time), and the patient can correct it. `created_at` stays the upload moment.
    visit_date: date | None = None
    visit_date_source: VisitDateSource | None = None
    # The visit's name ("Fever and cough"): the model suggests one when it reads the visit
    # (`title_source` "ai"), the patient can change it ("patient", never replaced by a re-read).
    title: str | None = None
    title_source: Literal["ai", "patient"] | None = None
    # The hospital, clinic or lab: as printed on a document (kept only when its quote is in the
    # source), or the place of a logged stay.
    facility: str | None = None
    facility_quote: str | None = None
    facility_source_id: str | None = None
    facility_locator: str | None = Field(default=None, description="PDF page of the quote")
    status: EncounterStatus = EncounterStatus.PROCESSING
    step: str | None = Field(default=None, description="Current pipeline step, for polling")
    error: str | None = None
    sources: list[SourceItem] = Field(default_factory=list)
    entries: list[Entry] = Field(default_factory=list)
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    bill_lines: list[BillLine] = Field(default_factory=list)
    stay: HospitalStay | None = Field(
        default=None, description="Set when this is an emergency or hospital stay, not a consult"
    )
    summary: str | None = None
    # Evidence for the eval and the "Under the hood" panel.
    step_seconds: dict[str, float] = Field(default_factory=dict)
    models_used: dict[str, list[str]] = Field(
        default_factory=dict, description="Step -> models that answered; '(backup)' marks a backup"
    )
    quote_failures: list[str] = Field(
        default_factory=list, description="Labels of model proposals dropped: quote not in source"
    )


def emergency_entries(entries: list[Entry], today: date | None = None) -> list[Entry]:
    """Entries allowed in the emergency view: confirmed only. Never proposed or rejected.

    Medicines must also still be current. One with no known end stays on the card (the UI
    labels it "duration not known") until the patient marks it stopped.
    """
    today = today or _now().date()
    return [
        e
        for e in entries
        if e.status == EntryStatus.CONFIRMED
        and e.kind != EntryKind.RESULT  # report values never go on the emergency card
        and not (
            e.kind == EntryKind.MEDICATION
            and e.medication is not None
            and e.medication.state(today) not in SHOWN_ON_CARD
        )
    ]
