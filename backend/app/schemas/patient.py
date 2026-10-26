"""Patient profile: the few facts shown at the top of the record and the emergency card."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


def _id() -> str:
    return uuid.uuid4().hex


class Patient(BaseModel):
    id: str = Field(default_factory=_id)
    name: str = Field(description="Synthetic names only in this repo")
    birth_year: int | None = None
    sex: str | None = None
    blood_group: str | None = None
    sandbox_id: str | None = Field(
        default=None, description="Demo only: which visitor's private copy this belongs to"
    )
