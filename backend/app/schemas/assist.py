"""Before a visit, and Ask about my health: what the routes take and return.

A `VisitPrep` is saved in the patient's record (its own table). An `AskAnswer` is not stored:
the answers are general, so the screen keeps the last few in the browser only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class PrepRequest(BaseModel):
    symptoms: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                               max_length=2000)]
    since: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None


class AskRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                               max_length=500)]


class PrepSymptom(BaseModel):
    text: str = Field(description="One short plain-English line")
    quote: str = Field(description="The patient's own words it comes from")


class PrepRecord(BaseModel):
    """From the confirmed record, assembled by code."""

    allergies: list[str] = Field(default_factory=list)
    medicines: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    out_of_range: list[str] = Field(default_factory=list)


class VisitPrep(BaseModel):
    id: str = Field(default_factory=_id)
    patient_id: str
    created_at: datetime = Field(default_factory=_now)
    text: str = Field(description="What the patient typed, in full")
    since: str | None = None
    urgent: bool = False
    warning_signs: list[str] = Field(default_factory=list)
    helpline: str | None = None
    symptoms: list[PrepSymptom] = Field(default_factory=list)
    specialty: str = "general_physician"
    specialty_label: str = "General physician"
    reason: str = ""
    questions: list[str] = Field(default_factory=list)
    record: PrepRecord = Field(default_factory=PrepRecord)
    models_used: list[str] = Field(default_factory=list)


class AskPoint(BaseModel):
    text: str = Field(description="One short plain-English sentence")
    quote: str = Field(description="The words in the source that back it")
    site: str = Field(default="", description="Short name shown next to the point, e.g. 'NHS'")
    title: str = ""
    url: str = ""


class AskAnswer(BaseModel):
    urgent: bool = False
    warning_signs: list[str] = Field(default_factory=list)
    helpline: str | None = None
    health_question: bool = True
    points: list[AskPoint] = Field(default_factory=list)
    allergy_notes: list[str] = Field(default_factory=list)
    considered: list[str] = Field(default_factory=list)
    note: str = ""
    models_used: list[str] = Field(default_factory=list)
