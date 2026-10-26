"""Sign-in routes: phone + one-time code, sign-up, demo accounts, sign out.

Flow: POST /auth/code (text a code) -> POST /auth/verify (right code: signed in, or "needs
profile" for a new number) -> POST /auth/signup (name and role; creates the account). For the
hackathon, POST /auth/demo signs in as a demo account picked from a list (DEMO_LOGINS).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth import (
    AuthError,
    Me,
    check_code,
    mask_phone,
    normalize_phone,
    send_code,
    sign_in,
    sign_out,
)
from app.config import get_settings
from app.schemas.account import Account, AccountRole
from app.schemas.patient import Patient
from app.seed import DEMO_PEOPLE
from app.sms import SmsSender
from app.storage import Storage

router = APIRouter(prefix="/auth")


def _storage(request: Request) -> Storage:
    return request.app.state.storage


def _sms(request: Request) -> SmsSender:
    return request.app.state.sms


DB = Annotated[Storage, Depends(_storage)]
SMS = Annotated[SmsSender, Depends(_sms)]


class MeView(BaseModel):
    """What the app may know about the signed-in account (the phone number masked)."""

    id: str
    role: AccountRole
    name: str
    phone: str | None
    person_id: str | None
    registry_id: str | None
    admin: bool
    demo: bool

    @classmethod
    def of(cls, a: Account) -> MeView:
        return cls(id=a.id, role=a.role, name=a.name, phone=mask_phone(a.phone),
                   person_id=a.person_id, registry_id=a.registry_id, admin=a.admin,
                   demo=a.demo_key is not None)


def _fail(exc: AuthError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("/me")
def me(account: Me) -> MeView:
    return MeView.of(account)


class CodeRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)


class CodeSent(BaseModel):
    challenge_id: str
    phone: str = Field(description="Masked")
    simulated_code: str | None = Field(
        default=None, description="Prototype only: there is no SMS provider, so the code is shown"
    )


@router.post("/code")
def request_code(body: CodeRequest, db: DB, sms: SMS) -> CodeSent:
    try:
        challenge, code = send_code(db, sms, body.phone)
    except AuthError as exc:
        raise _fail(exc) from exc
    return CodeSent(challenge_id=challenge.id, phone=mask_phone(challenge.phone) or "",
                    simulated_code=code if sms.simulated else None)


class VerifyRequest(BaseModel):
    challenge_id: str
    code: str = Field(min_length=4, max_length=10)


class Verified(BaseModel):
    status: Literal["signed_in", "needs_profile"]
    me: MeView | None = None


@router.post("/verify")
def verify(body: VerifyRequest, db: DB, request: Request, response: Response) -> Verified:
    try:
        challenge = check_code(db, body.challenge_id, body.code)
    except AuthError as exc:
        raise _fail(exc) from exc
    account = db.account_by_phone(challenge.phone)
    if account is None:
        return Verified(status="needs_profile")  # the same challenge now allows sign-up
    challenge.used = True
    db.save_challenge(challenge)
    sign_in(db, account, request, response)
    return Verified(status="signed_in", me=MeView.of(account))


class SignUp(BaseModel):
    challenge_id: str
    role: AccountRole
    name: str = Field(min_length=1, max_length=120)
    registry_id: str | None = Field(default=None, max_length=60, description="Doctors")
    birth_year: int | None = Field(default=None, ge=1900, le=2100, description="Patients")
    sex: str | None = Field(default=None, max_length=20)
    blood_group: str | None = Field(default=None, max_length=10)


@router.post("/signup")
def sign_up(body: SignUp, db: DB, request: Request, response: Response) -> MeView:
    challenge = db.get_challenge(body.challenge_id)
    if challenge is None or not challenge.signup_open():
        raise HTTPException(400, "Confirm your phone number again: ask for a new code")
    if db.account_by_phone(challenge.phone) is not None:
        raise HTTPException(409, "This number already has an account. Sign in instead.")
    name = body.name.strip()
    if body.role == AccountRole.DOCTOR:
        if not (body.registry_id or "").strip():
            raise HTTPException(422, "Doctors need a medical registry number")
        account = Account(role=body.role, name=name, phone=challenge.phone,
                          registry_id=body.registry_id.strip())
    else:
        person = Patient(name=name, birth_year=body.birth_year, sex=body.sex,
                         blood_group=body.blood_group)
        db.save_patient(person)
        account = Account(role=body.role, name=name, phone=challenge.phone, person_id=person.id)
    db.save_account(account)
    challenge.used = True
    db.save_challenge(challenge)
    sign_in(db, account, request, response)
    return MeView.of(account)


class DemoAccount(BaseModel):
    key: str
    name: str
    role: AccountRole
    about: str


@router.get("/demo-accounts")
def demo_accounts(db: DB) -> list[DemoAccount]:
    """For the hackathon: accounts a visitor can try without a phone. Empty when switched off."""
    if not get_settings().demo_logins:
        return []
    about = {p.key: p.about for p in DEMO_PEOPLE}
    order = {p.key: i for i, p in enumerate(DEMO_PEOPLE)}
    accounts = sorted(db.demo_accounts(), key=lambda a: order.get(a.demo_key or "", len(order)))
    return [DemoAccount(key=a.demo_key or "", name=a.name, role=a.role,
                        about=about.get(a.demo_key or "", ""))
            for a in accounts]


class DemoRequest(BaseModel):
    key: str


@router.post("/demo")
def demo_sign_in(body: DemoRequest, db: DB, request: Request, response: Response) -> MeView:
    if not get_settings().demo_logins:
        raise HTTPException(404, "Demo accounts are switched off")
    account = db.account_by_demo_key(body.key)
    if account is None:
        raise HTTPException(404, "No such demo account")
    sign_in(db, account, request, response)
    return MeView.of(account)


@router.post("/logout", status_code=204)
def logout(db: DB, request: Request, response: Response) -> None:
    sign_out(db, request, response)


def seed_demo_accounts(db: Storage) -> None:
    """Accounts for the demo people (`seed.DEMO_PEOPLE`). Runs on every start after the demo
    seed: adds what is missing, keeps existing accounts (and their sessions), and gives each
    its testing-guide phone number unless someone else has registered that number."""
    for p in DEMO_PEOPLE:
        if p.person_id and db.get_patient(p.person_id) is None:
            continue  # the demo person is gone (database reset by hand)
        account = db.account_by_demo_key(p.key) or Account(
            role=p.role, name=p.name, person_id=p.person_id, registry_id=p.registry_id,
            demo_key=p.key, created_at=datetime.now(UTC))
        if p.phone and account.phone is None:
            phone = normalize_phone(p.phone)
            if db.account_by_phone(phone) is None:
                account.phone = phone
        db.save_account(account)
