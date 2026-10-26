"""General information about a medicine, shown when the patient taps its name.

Not part of anyone's record: it is about the medicine, shared by everyone, and kept for a while
so the same medicine is not looked up again on every tap.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class InfoSource(BaseModel):
    title: str
    url: str
    site: str = Field(default="", description="Short name shown next to each point, e.g. 'NHS'")


class InfoPoint(BaseModel):
    text: str = Field(description="One short plain-English sentence")
    source: int = Field(description="Index into MedicineInfo.sources")
    quote: str = Field(description="The words in that source that back the sentence")


class MedicineInfo(BaseModel):
    key: str = Field(description="Active ingredients, or 'name:<brand>' when they are unknown")
    name: str = Field(description="What the patient tapped, e.g. 'Augmentin 625 mg'")
    generic_names: list[str] = Field(default_factory=list)
    found: bool = Field(description="False when the trusted sources had nothing usable")
    uses: list[InfoPoint] = Field(default_factory=list)
    how_taken: list[InfoPoint] = Field(default_factory=list)
    side_effects: list[InfoPoint] = Field(default_factory=list)
    warnings: list[InfoPoint] = Field(default_factory=list)
    sources: list[InfoSource] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    models_used: list[str] = Field(default_factory=list)
    dropped: int = Field(default=0, description="Points dropped: quote not found in their source")


class BrandMatch(BaseModel):
    """A brand name's active ingredients, read off a trusted site (Tavily search plus the model,
    checked by code). Shared by everyone, like MedicineInfo; `found` False is a miss."""

    key: str = Field(description="The name's words without strength or form, e.g. 'ondem'")
    name: str = Field(description="As first looked up, e.g. 'Ondem 4 Tab'")
    found: bool
    generic_names: list[str] = Field(default_factory=list)
    url: str | None = Field(default=None, description="The page the ingredients were read from")
    site: str | None = Field(default=None, description="Short name, e.g. '1mg'")
    quote: str | None = Field(default=None, description="The words on that page naming them")
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
