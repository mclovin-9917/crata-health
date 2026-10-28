"""Before a visit, and Ask about my health: what the routes take and return.

A `VisitPrep` is saved in the patient's record (its own table). An `AskAnswer` is not stored:
the answers are general, so the screen keeps the last few in the browser only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.views import NumberSeries


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class PrepAnswer(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
    answer: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class PrepRequest(BaseModel):
    symptoms: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                               max_length=2000)]
    since: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    answers: list[PrepAnswer] = Field(default_factory=list, max_length=3)


class QuestionsRequest(BaseModel):
    symptoms: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                               max_length=2000)]


class FollowUpQuestion(BaseModel):
    id: str = Field(description="q1 to q3")
    text: str
    choices: list[str] = Field(default_factory=list, description="0 to 4 short tap answers")


class QuestionsResponse(BaseModel):
    urgent: bool = False
    warning_signs: list[str] = Field(default_factory=list)
    helpline: str | None = None
    questions: list[FollowUpQuestion] = Field(default_factory=list)


class AskPrevious(BaseModel):
    """The question before this one and its points, kept by the screen, so "what about mango?"
    has something to refer to. Context only; nothing is stored."""

    question: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
    points: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=400)]] =         Field(default_factory=list, max_length=5)


class AskRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                               max_length=500)]
    previous: AskPrevious | None = None


class VoiceText(BaseModel):
    text: str
    models_used: list[str] = Field(default_factory=list)


class PrepSymptom(BaseModel):
    text: str = Field(description="One short plain-English line")
    quote: str = Field(description="The patient's own words it comes from")


class OutOfRangeTest(BaseModel):
    name: str
    value: float
    unit: str = ""
    date: str = Field(description="YYYY-MM-DD")
    direction: Literal["high", "low"]
    range_text: str = Field(description="The printed range, e.g. 'up to 5.6'")


class PrepRecord(BaseModel):
    """From the confirmed record, assembled by code."""

    allergies: list[str] = Field(default_factory=list)
    medicines: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    out_of_range: list[str] = Field(default_factory=list)
    tests_out_of_range: list[OutOfRangeTest] = Field(default_factory=list)


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
    answers: list[PrepAnswer] = Field(default_factory=list)
    record: PrepRecord = Field(default_factory=PrepRecord)
    models_used: list[str] = Field(default_factory=list)


AskCategory = Literal["good_to_know", "be_careful", "your_conditions"]


class AskSource(BaseModel):
    n: int = Field(description="1-based, in the order sources are first used")
    site: str
    title: str = ""
    url: str


class AskPoint(BaseModel):
    category: AskCategory = "good_to_know"
    source: int = Field(default=0, description="The `n` of its entry in `sources`")
    text: str = Field(description="One short plain-English sentence")
    quote: str = Field(description="The words in the source that back it")
    site: str = Field(default="", description="Short name shown next to the point, e.g. 'NHS'")
    title: str = ""
    url: str = ""


class RecordItem(BaseModel):
    """One confirmed item of the patient's own record, built by code (never from model text)."""

    id: str
    kind: Literal["medicine", "allergy", "condition", "event", "test"]
    label: str
    detail: str = ""
    # Where the item came from, built by code from the record (never from model text). App routes
    # such as "/encounters/<id>", "/reports/<test id>", "/record"; "" when there is none.
    link: str = ""
    link_label: str = ""
    numbers_link: str = Field(default="", description="Tests: the measure on My health numbers")
    group_key: str = Field(default="", description="Tests: the card key on My health numbers")
    # How the measure has gone (counted by code). Read by the model and the number check only.
    history: str = Field(default="", exclude=True)
    direction: str = Field(default="", exclude=True, description="higher, lower or the same")
    position: str = Field(default="", exclude=True, description="above, below or within the range")


class AskChart(BaseModel):
    """One measure's whole series, built by code from the patient's numbers for a picked test
    value. `series` is what My health numbers draws (several lines when the measures share a card,
    like blood pressure); readings the patient removed are left out."""

    key: str
    name: str
    series: list[NumberSeries]
    report_link: str = Field(default="", description="The newest reading's report")
    numbers_link: str = ""


class AskAnswer(BaseModel):
    kind: Literal["record", "general", "both", "not_health"] = "general"
    record_items: list[RecordItem] = Field(default_factory=list)
    record_summary: str | None = None
    charts: list[AskChart] = Field(default_factory=list)
    summary: str | None = None
    urgent: bool = False
    warning_signs: list[str] = Field(default_factory=list)
    helpline: str | None = None
    health_question: bool = True
    points: list[AskPoint] = Field(default_factory=list)
    sources: list[AskSource] = Field(default_factory=list)
    allergy_notes: list[str] = Field(default_factory=list)
    considered: list[str] = Field(default_factory=list)
    note: str = ""
    models_used: list[str] = Field(default_factory=list)
