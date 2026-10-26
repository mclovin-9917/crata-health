"""Who is asking: phone numbers, one-time codes and sign-in sessions.

Rules (all in code):
- A code is 6 digits, valid 10 minutes, 5 tries; at most 3 codes per number per 15 minutes.
- Only digests of codes and session tokens are stored.
- The session token lives in an httpOnly cookie (scripts in the page cannot read it), SameSite
  Lax (not sent on cross-site form posts), Secure over HTTPS.
- Patients stay signed in for 30 days, doctors for 12 hours.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response

from app.schemas.account import (
    CODE_TRIES,
    CODE_WINDOW_MINUTES,
    CODES_PER_WINDOW,
    Account,
    CodeChallenge,
    Session,
    digest,
)
from app.sms import SmsSender
from app.storage import Storage

COOKIE = "crata_session"


class AuthError(Exception):
    """A sign-in step that cannot go on; the message is safe to show."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalize_phone(raw: str) -> str:
    """'+91 98000 00000', '098000 00000' or '9800000000' -> '+919800000000'. A 10-digit number
    is taken as Indian; other countries need their + code."""
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("+"):
        phone = "+" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        phone = "+91" + digits[1:]
    elif len(digits) == 10:
        phone = "+91" + digits
    elif len(digits) == 12 and digits.startswith("91"):
        phone = "+" + digits
    else:
        raise AuthError("Enter a 10-digit mobile number, or the number with its + country code")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise AuthError("That doesn't look like a phone number")
    if phone.startswith("+91") and not re.fullmatch(r"\+91[6-9]\d{9}", phone):
        raise AuthError("Indian mobile numbers have 10 digits and start with 6, 7, 8 or 9")
    return phone


def mask_phone(phone: str | None) -> str | None:
    return None if not phone else phone[:3] + " ••••• " + phone[-3:]


def send_code(db: Storage, sms: SmsSender, raw_phone: str,
              now: datetime | None = None) -> tuple[CodeChallenge, str]:
    """Issue a code for this number and text it. Returns the challenge and the code (the caller
    shows it only when the SMS is simulated)."""
    now = now or datetime.now(UTC)
    phone = normalize_phone(raw_phone)
    if db.count_challenges(phone, now - timedelta(minutes=CODE_WINDOW_MINUTES)) >= CODES_PER_WINDOW:
        raise AuthError("Too many codes for this number. Wait 15 minutes and try again.", 429)
    challenge, code = CodeChallenge.issue(phone, now)
    db.save_challenge(challenge)
    sms.send(phone, f"{code} is your Crata Health sign-in code. It works for 10 minutes. "
                    "Never share it.")
    return challenge, code


def check_code(db: Storage, challenge_id: str, code: str,
               now: datetime | None = None) -> CodeChallenge:
    """The challenge, marked verified, if the code is right. Every wrong try counts."""
    now = now or datetime.now(UTC)
    c = db.get_challenge(challenge_id)
    if c is None or c.used or c.verified_at is not None:
        raise AuthError("This code was already used or does not exist. Ask for a new one.")
    if c.expired(now):
        raise AuthError("This code has expired. Ask for a new one.")
    if c.tries >= CODE_TRIES:
        raise AuthError("Too many wrong tries. Ask for a new code.", 429)
    if not c.matches(code):
        c.tries += 1
        db.save_challenge(c)
        left = CODE_TRIES - c.tries
        raise AuthError(f"That code is not right. {left} tr{'y' if left == 1 else 'ies'} left."
                        if left else "Too many wrong tries. Ask for a new code.")
    c.verified_at = now
    db.save_challenge(c)
    return c


def sign_in(db: Storage, account: Account, request: Request, response: Response) -> Session:
    session, token = Session.start(account)
    db.save_session(session)
    response.set_cookie(
        COOKIE, token, max_age=int((session.expires_at - session.created_at).total_seconds()),
        httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/",
    )
    return session


def sign_out(db: Storage, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.delete_session(digest(token))
    response.delete_cookie(COOKIE, path="/")


def account_for(db: Storage, token: str | None) -> Account | None:
    if not token:
        return None
    session = db.get_session(digest(token))
    if session is None or not session.valid():
        return None
    return db.get_account(session.account_id)


# ---- FastAPI dependencies -------------------------------------------------------------------
def current_account(request: Request) -> Account | None:
    """The signed-in account, or None."""
    return account_for(request.app.state.storage, request.cookies.get(COOKIE))


def signed_in(account: Annotated[Account | None, Depends(current_account)]) -> Account:
    if account is None:
        raise HTTPException(401, "Please sign in")
    return account


MaybeAccount = Annotated[Account | None, Depends(current_account)]
Me = Annotated[Account, Depends(signed_in)]
