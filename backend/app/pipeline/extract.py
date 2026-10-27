"""Extraction: model output -> proposed entries, with the quote check and tiering in code."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.pipeline.bills import from_imaging
from app.pipeline.quotes import locate, quote_in_source
from app.schemas.record import (
    EVENT_KINDS,
    DatePrecision,
    Entry,
    EntryKind,
    Medication,
    Provenance,
    SourceItem,
    SourceType,
    Speaker,
    Tier,
)


class ExtractedItem(BaseModel):
    """What the model returns per fact. Code decides tier, dates and day counts."""

    kind: EntryKind
    label: str
    source_id: str
    quote: str
    said_by: Literal["doctor", "patient", "document"]
    confidence: float = Field(ge=0, le=1)
    fields: dict[str, str] = Field(default_factory=dict)
    @field_validator("fields", mode="before")
    @classmethod
    def _plain_fields(cls, value: object) -> object:
        """Models sometimes put `null` or a bare number in `fields` (a live run failed on
        `"doses_per_day": null`). Empty values are dropped and numbers become text."""
        if not isinstance(value, dict):
            return value
        return {str(k): str(v) for k, v in value.items() if v is not None and v != ""}

    # diagnosis
    chronic: bool = False
    # events
    occurred_on: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2}(-\d{2})?)?$")
    # medication
    dose: str | None = None
    doses_per_day: float | None = Field(default=None, ge=0)
    duration_value: int | None = Field(default=None, ge=1)
    duration_unit: Literal["day", "week", "month"] | None = None
    ongoing: bool = False
    as_needed: bool = False


class ExtractedResult(BaseModel):
    """A measured value printed on a document. Code checks it against its quote
    (`results.to_results`); the model only copies what is printed."""

    source_id: str
    name: str = Field(description="The parameter as printed, e.g. 'Hemoglobin' or 'BP'")
    quote: str = Field(description="The whole printed row, copied exactly")
    part: Literal["systolic", "diastolic"] | None = Field(
        default=None, description="Blood pressure only: '138/88' is two results, one per part"
    )
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = None
    test: str | None = Field(default=None, description="Label of the test item it belongs to")
    measured_on: str | None = Field(
        default=None, description="The date printed on the same document, as YYYY-MM-DD"
    )
    confidence: float = Field(default=0.9, ge=0, le=1)


class Extraction(BaseModel):
    items: list[ExtractedItem]
    results: list[ExtractedResult] = Field(default_factory=list)
    visit_date: str | None = Field(
        default=None, description="Date printed on a document, as YYYY-MM-DD; null if none"
    )
    # A short neutral name for the visit, and the hospital, clinic or lab as printed (with the
    # words that hold it). Code checks the quote (`facility_of`) and tidies the title.
    visit_title: str | None = None
    facility: str | None = None
    facility_source_id: str | None = None
    facility_quote: str | None = None
    # Hospital stays only (prompts.STAY_EXTRA): the discharge date and the words that hold it.
    discharge_date: str | None = None
    discharge_source_id: str | None = None
    discharge_quote: str | None = None


def document_date(extraction: Extraction, today: date) -> date | None:
    """The visit date the model read off a document, if it is a real, not-future date."""
    return _real_date(extraction.visit_date, date(1950, 1, 1), today)


TITLE_MAX = 60


def _words(text: str, shortest: int) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]+", text.casefold()) if len(w) >= shortest}


def avoided_words(person_name: str | None, medicine_labels: Iterable[str]) -> set[str]:
    """Words an AI visit name must not hold: the patient's first or last name and the names of
    the visit's medicines (policy in code: a name is shown in lists and to a doctor)."""
    words = _words(person_name or "", 3)
    for label in medicine_labels:
        words |= _words(label, 4)
    return words


def clean_title(text: str | None, avoid: set[str] = frozenset()) -> str | None:
    """The model's visit name: trimmed, at most 60 characters, None if empty, and None if it
    holds one of the `avoid` words (the patient's name, a medicine's name)."""
    title = " ".join((text or "").split())[:TITLE_MAX].rstrip()
    if not title or _words(title, 1) & avoid:
        return None
    return title


def facility_of(
    extraction: Extraction, sources: dict[str, SourceItem]
) -> tuple[str, str, str, str | None] | None:
    """The hospital, clinic or lab name: (name, quote, stored source id, page locator), kept only
    if it is on a document or in the patient's typed notes (never a transcript), its quote is in
    that source, and the name is in the quote. The locator is the PDF page when there are several."""
    name = " ".join((extraction.facility or "").split())[:120]
    quote = (extraction.facility_quote or "").strip()
    source = sources.get(extraction.facility_source_id or "")
    if (not name or source is None or source.source_type == SourceType.AUDIO or not quote
            or not quote_in_source(quote, source.text) or not quote_in_source(name, quote)):
        return None
    page = None
    if len(source.page_texts) > 1:
        n = next((i for i, t in enumerate(source.page_texts, 1) if quote_in_source(quote, t)), None)
        page = f"page {n}" if n else None
    return name, quote, source.id, page


def _real_date(text: str | None, earliest: date, latest: date) -> date | None:
    try:
        d = date.fromisoformat(text or "")
    except ValueError:
        return None
    return d if earliest <= d <= latest else None


def discharge_date(
    extraction: Extraction, sources: dict[str, SourceItem], started: date, today: date
) -> tuple[date, str, str] | None:
    """The discharge date read off a stay's documents: (date, quote, stored source id), kept
    only if it is between the start of the stay and today and its quote is in its source."""
    d = _real_date(extraction.discharge_date, started, today)
    source = sources.get(extraction.discharge_source_id or "")
    quote = (extraction.discharge_quote or "").strip()
    if d is None or source is None or not quote or not quote_in_source(quote, source.text):
        return None
    return d, quote, source.id


class DrugNames(BaseModel):
    names: list[str]


_DAYS_PER = {"day": 1, "week": 7, "month": 30}
_STRUCTURED = ("dose", "doses_per_day", "duration_value", "duration_unit", "duration", "ongoing",
               "as_needed", "said_by", "chronic", "occurred_on", "confidence")
_PRECISION = {4: DatePrecision.YEAR, 7: DatePrecision.MONTH, 10: DatePrecision.DAY}
_CARD_KINDS = {EntryKind.ALLERGY, EntryKind.MEDICATION, EntryKind.IMPLANT}


def tier_for(item: ExtractedItem) -> Tier:
    """Protection tier is policy, so code decides it, not the model."""
    if item.kind in _CARD_KINDS or (item.kind == EntryKind.DIAGNOSIS and item.chronic):
        return Tier.EMERGENCY_CARD
    if item.kind in EVENT_KINDS or item.kind == EntryKind.DIAGNOSIS:
        return Tier.CLINICAL_SUMMARY
    return Tier.FULL_RECORD


def apply_speaker_rules(item: ExtractedItem, source: SourceItem) -> tuple[EntryKind, str, Speaker]:
    """Who said it decides what it can be (code, not the model, enforces this).

    A document speaks for itself. A patient's words can describe symptoms and their history,
    but cannot create a diagnosis or a new prescription: a non-chronic "diagnosis" from the
    patient becomes a symptom, and a medicine the patient mentions (other than one they already
    take long-term) becomes a note.

    A scan report (X-ray, CT, MRI, ultrasound) never makes a diagnosis: a "diagnosis" quoted from
    one becomes a note labelled "Scan finding: ...", so it needs no confirmation and can never
    reach the emergency card, whatever `chronic` says (policy in code, not the model).
    """
    if source.source_type == SourceType.IMAGE:
        if item.kind == EntryKind.DIAGNOSIS and from_imaging(source, item.quote):
            return EntryKind.NOTE, f"Scan finding: {item.label}", Speaker.DOCUMENT
        return item.kind, item.label, Speaker.DOCUMENT
    speaker = Speaker(item.said_by) if item.said_by != "document" else Speaker.DOCTOR
    if speaker == Speaker.PATIENT:
        if item.kind == EntryKind.DIAGNOSIS and not item.chronic:
            return EntryKind.SYMPTOM, item.label, speaker
        if item.kind == EntryKind.MEDICATION and not item.ongoing:
            return EntryKind.NOTE, f"Patient mentions {item.label}", speaker
    return item.kind, item.label, speaker


def duration_days(item: ExtractedItem) -> int | None:
    if item.duration_value is None or item.duration_unit is None:
        return None
    return item.duration_value * _DAYS_PER[item.duration_unit]


def to_entries(
    extraction: Extraction,
    sources: dict[str, SourceItem],
    consult_date: date,
) -> tuple[list[Entry], list[str]]:
    """Proposed entries that pass the quote check, and labels of the ones that failed.

    `sources` maps the short ids shown to the model ("doc1", "audio") to stored sources.
    """
    kept: list[Entry] = []
    failed: list[str] = []
    for item in extraction.items:
        source = sources.get(item.source_id)
        if source is None or not quote_in_source(item.quote, source.text):
            failed.append(item.label)
            continue
        kind, label, speaker = apply_speaker_rules(item, source)
        item = item.model_copy(update={"kind": kind, "label": label})
        fields = {k: v.strip() for k, v in item.fields.items() if v and v.strip()}
        for k in _STRUCTURED:  # the model sometimes repeats structured values as free text
            fields.pop(k, None)
        if item.as_needed and item.kind == EntryKind.MEDICATION:
            fields.setdefault("frequency", "as needed")
        medication = None
        if item.kind == EntryKind.MEDICATION:
            medication = Medication(
                dose=item.dose,
                doses_per_day=None if item.as_needed else item.doses_per_day,
                duration_days=duration_days(item),
                started_on=consult_date,
                ongoing=item.ongoing,
            )
        is_event = item.kind in EVENT_KINDS
        kept.append(
            Entry(
                kind=item.kind,
                label=item.label,
                fields=fields,
                provenance=[
                    Provenance(
                        source_id=source.id,
                        source_type=source.source_type,
                        locator=locate(item.quote, source.segments),
                        quote=item.quote,
                    )
                ],
                confidence=item.confidence,
                tier=tier_for(item),
                said_by=speaker,
                medication=medication,
                occurred_on=item.occurred_on if is_event else None,
                date_precision=(
                    _PRECISION[len(item.occurred_on)] if is_event and item.occurred_on else None
                ),
                # An event someone said (consult, typed notes) is from memory, not a document.
                unverified=is_event and source.source_type != SourceType.IMAGE,
            )
        )
    return kept, failed
