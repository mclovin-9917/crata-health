"""Access model: who may open whose emergency summary, and the tamper-evident log of it.

Prototype stubs (labelled, replace later): login, doctor verification, in-app-only alerts.
Built properly: the policy function, the hash-chained log, alert records.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.record import Tier


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid.uuid4().hex


class Role(str, Enum):
    PATIENT = "patient"
    TRUSTED = "trusted"
    DOCTOR = "doctor"


class AccessReason(str, Enum):
    OWN = "own"
    EMERGENCY = "emergency"


class TrustedLink(BaseModel):
    """`trusted_id` is a registered trusted person of `patient_id`."""

    patient_id: str
    trusted_id: str


INVITE_DAYS = 14  # an invitation nobody answers lapses
MAX_TRUSTED = 5  # trusted people plus open invitations, per patient


class TrustedInvite(BaseModel):
    """A patient asks the owner of a phone number to be their trusted person (decided
    2026-10-15). Nobody becomes anyone's trusted person without saying yes: the link is made only
    when the person signed in with that number accepts."""

    id: str = Field(default_factory=_id)
    patient_id: str
    phone: str = Field(description="E.164, as normalized at sign-in")
    created_at: datetime = Field(default_factory=_now)
    status: str = Field(default="pending", description="pending | accepted | declined | cancelled")
    answered_at: datetime | None = None

    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(days=INVITE_DAYS)

    def is_open(self, now: datetime | None = None) -> bool:
        return self.status == "pending" and (now or _now()) < self.expires_at


def tiers_for(role: Role, reason: AccessReason) -> set[Tier]:
    """Policy: which tiers a role may read in a given situation.

    In an emergency a doctor gets the emergency card and the clinical summary instantly, with
    alerts. A trusted person gets no tier: only the basics (`TRUSTED_BASICS`) and a short-lived
    code for a doctor (decided 2026-10-12). The full record is patient-only.
    """
    if role == Role.PATIENT:
        return {Tier.EMERGENCY_CARD, Tier.CLINICAL_SUMMARY, Tier.FULL_RECORD}
    if reason == AccessReason.EMERGENCY and role == Role.DOCTOR:
        return {Tier.EMERGENCY_CARD, Tier.CLINICAL_SUMMARY}
    return set()


# What a trusted person sees when they start an emergency: enough to tell paramedics, nothing
# about medicines or conditions.
TRUSTED_BASICS = ("name", "blood_group", "allergies")

EMERGENCY_CODE_MINUTES = 30


class EmergencyCode(BaseModel):
    """A short-lived, single-use code a trusted person shows a doctor as a QR. The doctor still
    declares the emergency; the code only proves a trusted person started it."""

    code: str = Field(default_factory=lambda: secrets.token_urlsafe(16))
    patient_id: str
    issued_by: str
    issued_by_label: str
    issued_at: datetime = Field(default_factory=_now)
    expires_at: datetime = Field(
        default_factory=lambda: _now() + timedelta(minutes=EMERGENCY_CODE_MINUTES)
    )
    used_at: datetime | None = None
    used_by: str | None = None

    def usable(self, now: datetime | None = None) -> bool:
        return self.used_at is None and (now or _now()) < self.expires_at


class AccessLogEntry(BaseModel):
    id: str = Field(default_factory=_id)
    patient_id: str
    actor_id: str
    actor_role: Role
    actor_label: str = Field(description="Display name, e.g. 'Dr. A. Sharma'")
    actor_registry_id: str | None = Field(default=None, description="Stub in the prototype")
    verified: bool = Field(default=False, description="Always False in the prototype")
    place: str | None = None
    reason: AccessReason
    tiers: list[Tier]
    at: datetime = Field(default_factory=_now)
    via: str | None = Field(default=None, description="Trusted person whose code the doctor used")
    prev_hash: str = ""
    hash: str = ""

    def compute_hash(self) -> str:
        # `via` came later: leave it out when empty, so entries written before it existed still
        # verify.
        skip = {"hash"}
        if self.via is None:
            skip.add("via")
        body = self.model_dump(mode="json", exclude=skip)
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def append_log(log: list[AccessLogEntry], entry: AccessLogEntry) -> AccessLogEntry:
    """Chain `entry` to the end of `log` (a patient's log, oldest first)."""
    entry.prev_hash = log[-1].hash if log else ""
    entry.hash = entry.compute_hash()
    log.append(entry)
    return entry


def verify_chain(log: list[AccessLogEntry]) -> bool:
    """True if no entry was edited, removed or reordered."""
    prev = ""
    for e in log:
        if e.prev_hash != prev or e.hash != e.compute_hash():
            return False
        prev = e.hash
    return True


class Alert(BaseModel):
    """In-app alert shown to the patient and their trusted people."""

    id: str = Field(default_factory=_id)
    recipient_id: str
    patient_id: str
    log_entry_id: str | None = Field(
        default=None, description="The access log entry; None for alerts about trusted people"
    )
    message: str
    at: datetime = Field(default_factory=_now)
    read: bool = False
    reported: bool = Field(default=False, description="Recipient tapped 'Not expected? Report'")
