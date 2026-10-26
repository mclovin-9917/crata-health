"""Accounts, sign-in sessions and one-time codes.

Sign-in is a phone number plus a one-time code (decided 2026-10-12). In the prototype the SMS is
simulated: the code is shown on screen, labelled. For the hackathon, judges can also pick a demo
account from a list, with no phone at all.

A patient account points at a person (`Patient`), whose record it owns; a trusted person is a
patient account too, linked to others through `TrustedLink`. A doctor account carries a name and
a registry number, which the prototype cannot verify. Admin is a flag set from a command, never
from the app.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid.uuid4().hex


def digest(secret: str) -> str:
    """Only digests of codes and session tokens are stored, never the values."""
    return hashlib.sha256(secret.encode()).hexdigest()


class AccountRole(str, Enum):
    PATIENT = "patient"  # includes trusted people: they are patients with links
    DOCTOR = "doctor"


class Account(BaseModel):
    id: str = Field(default_factory=_id)
    role: AccountRole
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, description="E.164, e.g. +919800000000; None for demo")
    person_id: str | None = Field(default=None, description="Patients: their own record")
    registry_id: str | None = Field(default=None, description="Doctors: not verified in prototype")
    admin: bool = False
    demo_key: str | None = Field(default=None, description="Demo accounts: 'asha', 'ravi'...")
    created_at: datetime = Field(default_factory=_now)


# How long a sign-in lasts. Doctors share devices in clinics, so theirs is short.
SESSION_HOURS = {AccountRole.PATIENT: 24 * 30, AccountRole.DOCTOR: 12}


class Session(BaseModel):
    id: str = Field(description="Digest of the token in the cookie; the token itself is not kept")
    account_id: str
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime

    @classmethod
    def start(cls, account: Account, now: datetime | None = None) -> tuple[Session, str]:
        """A new session and the token for the cookie (shown once, then only its digest)."""
        now = now or _now()
        token = secrets.token_urlsafe(32)
        session = cls(id=digest(token), account_id=account.id, created_at=now,
                      expires_at=now + timedelta(hours=SESSION_HOURS[account.role]))
        return session, token

    def valid(self, now: datetime | None = None) -> bool:
        return (now or _now()) < self.expires_at


CODE_MINUTES = 10
CODE_TRIES = 5
CODES_PER_WINDOW = 3  # per phone number
CODE_WINDOW_MINUTES = 15
SIGNUP_MINUTES = 15  # after the code is right, time to fill in name and role


class CodeChallenge(BaseModel):
    """One one-time code sent to one phone number."""

    id: str = Field(default_factory=_id)
    phone: str
    salt: str = Field(default_factory=lambda: secrets.token_hex(8))
    code_digest: str = ""
    created_at: datetime = Field(default_factory=_now)
    tries: int = 0
    verified_at: datetime | None = None
    used: bool = Field(default=False, description="Turned into a session or an account")

    @classmethod
    def issue(cls, phone: str, now: datetime | None = None) -> tuple[CodeChallenge, str]:
        code = f"{secrets.randbelow(10**6):06d}"
        c = cls(phone=phone, created_at=now or _now())
        c.code_digest = digest(c.salt + code)
        return c, code

    def expired(self, now: datetime | None = None) -> bool:
        return (now or _now()) > self.created_at + timedelta(minutes=CODE_MINUTES)

    def matches(self, code: str) -> bool:
        return secrets.compare_digest(self.code_digest, digest(self.salt + code.strip()))

    def signup_open(self, now: datetime | None = None) -> bool:
        return (self.verified_at is not None and not self.used
                and (now or _now()) <= self.verified_at + timedelta(minutes=SIGNUP_MINUTES))
