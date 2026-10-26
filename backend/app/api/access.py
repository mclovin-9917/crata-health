"""Emergency access (changed 2026-10-12).

A doctor scans a QR and declares an emergency: either the patient's own card QR, or a short-lived
code a trusted person shows them. The doctor gets the emergency summary. A trusted person never
sees the summary: starting an emergency gives them only the basics (name, blood group, allergies)
and a single-use code for the doctor. Every step is written to the patient's hash-chained log,
and the patient and their trusted people get an alert.

Who is acting comes from the session, never from the request body (2026-10-13). A signed-in
doctor opens the card under their account. A doctor without an account can still open it in an
emergency by typing a name and registry ID; the log and the alerts say "no account". A trusted
person acts as their signed-in account. Prototype shortcuts (labelled in the UI and README):
registry IDs are never verified; alerts are in-app only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.routes import DB, _patient, own_patient
from app.auth import AuthError, MaybeAccount, Me, mask_phone, normalize_phone
from app.schemas.access import (
    MAX_TRUSTED,
    AccessLogEntry,
    AccessReason,
    Alert,
    EmergencyCode,
    Role,
    TrustedInvite,
    TrustedLink,
    tiers_for,
    verify_chain,
)
from app.schemas.account import Account, AccountRole
from app.schemas.patient import Patient
from app.schemas.record import Tier
from app.storage import Storage
from app.views import EmergencySummary, emergency_summary

router = APIRouter()


NO_ACCOUNT = "(no account)"


class EmergencyRequest(BaseModel):
    role: Literal["doctor", "trusted"] = "doctor"
    actor_label: str | None = Field(
        default=None, max_length=120, description="Doctors without an account: their name"
    )
    registry_id: str | None = Field(
        default=None, max_length=60, description="Doctors without an account: registry ID"
    )
    place: str | None = Field(default=None, max_length=120)
    code: str | None = Field(default=None, max_length=64, description="A trusted person's code")


class EmergencyAccess(BaseModel):
    summary: EmergencySummary
    log_entry: AccessLogEntry
    alerted: list[str]


@router.post("/patients/{patient_id}/access/emergency", status_code=201)
def emergency_access(
    patient_id: str, body: EmergencyRequest, db: DB, account: MaybeAccount
) -> EmergencyAccess:
    patient = _patient(db, patient_id)
    role = Role(body.role)
    trusted = db.trusted_people(patient_id)
    if role == Role.TRUSTED:
        # Policy: a trusted person never opens the summary; they get a code for a doctor.
        raise HTTPException(403, "Trusted people get a short-lived code to show a doctor instead")
    if account is not None and account.role != AccountRole.DOCTOR:
        raise HTTPException(403, "This is for doctors. You are signed in with a patient account: "
                                 "sign out to open the card as a doctor without an account.")
    if account is not None:
        actor_id, label, registry = account.id, account.name, account.registry_id
    else:
        label, registry = (body.actor_label or "").strip(), (body.registry_id or "").strip()
        if not label or not registry:
            raise HTTPException(422, "A doctor without an account must give a name and a registry "
                                     "ID (not verified in prototype)")
        actor_id, label = f"no-account:{uuid.uuid4().hex}", f"{label} {NO_ACCOUNT}"
    via = None
    if body.code:
        used = db.use_code(body.code, patient_id, used_by=actor_id)
        if used is None:
            raise HTTPException(403, "This emergency code has expired or was already used. "
                                     "Ask for a new one.")
        via = used.issued_by_label

    tiers = tiers_for(role, AccessReason.EMERGENCY)
    entry = db.append_access(
        AccessLogEntry(
            patient_id=patient_id,
            actor_id=actor_id,
            actor_role=role,
            actor_label=label,
            actor_registry_id=registry,
            verified=False,
            place=body.place,
            reason=AccessReason.EMERGENCY,
            tiers=sorted(tiers, key=lambda t: t.value),
            via=via,
        )
    )

    recipients = [patient_id, *trusted]
    message = _alert_message(patient, entry)
    for r in recipients:
        db.save_alert(Alert(recipient_id=r, patient_id=patient_id, log_entry_id=entry.id,
                            message=message))

    summary = emergency_summary(
        patient, db.list_entries(patient_id), db.list_encounters(patient_id)
    )
    if Tier.CLINICAL_SUMMARY not in tiers:
        summary.timeline = []
    return EmergencyAccess(summary=summary, log_entry=entry, alerted=recipients)


def _alert_message(patient: Patient, e: AccessLogEntry) -> str:
    if e.actor_role == Role.TRUSTED:
        return (
            f"{e.actor_label} started an emergency for {patient.name} and has a code to show a "
            "doctor. They saw only the name, blood group and allergies. "
            "If this was not expected, tap Report."
        )
    who = f"{e.actor_label} (registry ID {e.actor_registry_id}, not verified)"
    where = f" at {e.place}" if e.place else ""
    via = f", using an emergency code from {e.via}" if e.via else ""
    return (
        f"{who} opened {patient.name}'s emergency summary{where}{via}, declaring an emergency. "
        "If this was not expected, tap Report."
    )


# ---- a trusted person starts an emergency -----------------------------------
class AllergyBasics(BaseModel):
    label: str
    reaction: str | None


class EmergencyBasics(BaseModel):
    """Enough to tell paramedics. Medicines, conditions and history stay for the doctor."""

    name: str
    blood_group: str | None
    allergies: list[AllergyBasics]


class TrustedEmergency(BaseModel):
    code: str
    expires_at: datetime
    basics: EmergencyBasics
    log_entry: AccessLogEntry
    alerted: list[str]


@router.post("/patients/{patient_id}/access/trusted-emergency", status_code=201)
def trusted_emergency(patient_id: str, db: DB, me: Me) -> TrustedEmergency:
    """The signed-in account must be one of this patient's trusted people."""
    trusted = db.trusted_people(patient_id)
    if me.person_id is None or me.person_id not in trusted:
        raise HTTPException(404, "Patient not found")
    patient = _patient(db, patient_id)
    actor_id, actor_label = me.person_id, me.name

    entry = db.append_access(
        AccessLogEntry(
            patient_id=patient_id,
            actor_id=actor_id,
            actor_role=Role.TRUSTED,
            actor_label=actor_label,
            reason=AccessReason.EMERGENCY,
            tiers=sorted(tiers_for(Role.TRUSTED, AccessReason.EMERGENCY), key=lambda t: t.value),
        )
    )
    recipients = [r for r in [patient_id, *trusted] if r != actor_id]
    message = _alert_message(patient, entry)
    for r in recipients:
        db.save_alert(Alert(recipient_id=r, patient_id=patient_id, log_entry_id=entry.id,
                            message=message))

    code = EmergencyCode(patient_id=patient_id, issued_by=actor_id, issued_by_label=actor_label)
    db.save_code(code)
    summary = emergency_summary(patient, db.list_entries(patient_id), db.list_encounters(patient_id))
    basics = EmergencyBasics(
        name=patient.name,
        blood_group=patient.blood_group,
        allergies=[AllergyBasics(label=e.label, reaction=e.fields.get("reaction"))
                   for e in summary.allergies],
    )
    return TrustedEmergency(code=code.code, expires_at=code.expires_at, basics=basics,
                            log_entry=entry, alerted=recipients)


class AccessLog(BaseModel):
    entries: list[AccessLogEntry]  # oldest first
    chain_intact: bool


@router.get("/patients/{patient_id}/access-log")
def access_log(patient_id: str, db: DB, me: Me) -> AccessLog:
    """The patient's own log only."""
    own_patient(db, me, patient_id)
    entries = db.access_log(patient_id)
    return AccessLog(entries=entries, chain_intact=verify_chain(entries))


def alert_recipient(me: Account) -> str | None:
    """Whose alerts the signed-in account reads: they are kept under the patient's person id."""
    return me.person_id


@router.get("/alerts")
def list_alerts(db: DB, me: Me) -> list[Alert]:
    """The signed-in person's own alerts."""
    recipient = alert_recipient(me)
    if not recipient:
        return []
    return db.list_alerts(recipient)


class AlertUpdate(BaseModel):
    read: bool | None = None
    reported: bool | None = Field(default=None, description="'Not expected? Report' was tapped")


@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: str, body: AlertUpdate, db: DB, me: Me) -> Alert:
    """Mark read or reported. The misuse-report workflow itself is a prototype stub."""
    a = db.get_alert(alert_id)
    recipient = alert_recipient(me)
    if a is None or recipient is None or a.recipient_id != recipient:
        raise HTTPException(404, "Alert not found")
    if body.read is not None:
        a.read = body.read
    if body.reported:
        a.reported = True  # a report cannot be taken back
        a.read = True
    db.save_alert(a)
    return a


# ---- trusted people ---------------------------------------------------------
#
# A patient invites someone by phone number; the person signed in with that number accepts or
# declines (decided 2026-10-15). Either side can end the link later; the other side is alerted.
# Whether a number has an account is never revealed to the patient: an invitation to a number
# with no patient account simply waits, then lapses after INVITE_DAYS.
class TrustedPerson(BaseModel):
    id: str
    name: str


def _people(db: Storage, ids: list[str]) -> list[TrustedPerson]:
    return [TrustedPerson(id=p.id, name=p.name) for i in ids if (p := db.get_patient(i))]


def _tell(db: Storage, recipient_id: str, patient_id: str, message: str) -> None:
    db.save_alert(Alert(recipient_id=recipient_id, patient_id=patient_id, message=message))


def _name(db: Storage, person_id: str) -> str:
    p = db.get_patient(person_id)
    return p.name if p else "Someone"


@router.get("/patients/{patient_id}/trusted")
def trusted_people(patient_id: str, db: DB, me: Me) -> list[TrustedPerson]:
    own_patient(db, me, patient_id)
    return _people(db, db.trusted_people(patient_id))


@router.delete("/patients/{patient_id}/trusted/{trusted_id}")
def remove_trusted(patient_id: str, trusted_id: str, db: DB, me: Me) -> list[TrustedPerson]:
    """The patient ends a trust link. The person removed is told."""
    patient = own_patient(db, me, patient_id)
    if not db.remove_trusted(patient_id, trusted_id):
        raise HTTPException(404, "Not one of your trusted people")
    _tell(db, trusted_id, patient_id,
          f"{patient.name} removed you as a trusted person. You can no longer start an emergency for them.")
    return _people(db, db.trusted_people(patient_id))


class InviteRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)


class SentInvite(BaseModel):
    """An open invitation, as the patient who sent it sees it (the number they typed)."""

    id: str
    phone: str
    created_at: datetime
    expires_at: datetime


def _sent(inv: TrustedInvite) -> SentInvite:
    return SentInvite(id=inv.id, phone=inv.phone, created_at=inv.created_at,
                      expires_at=inv.expires_at)


@router.get("/patients/{patient_id}/trusted/invites")
def sent_invites(patient_id: str, db: DB, me: Me) -> list[SentInvite]:
    own_patient(db, me, patient_id)
    return [_sent(i) for i in db.invites_from(patient_id) if i.is_open()]


@router.post("/patients/{patient_id}/trusted/invites", status_code=201)
def invite_trusted(patient_id: str, body: InviteRequest, db: DB, me: Me) -> SentInvite:
    """Invite the owner of a phone number to be a trusted person. Nothing is linked until they
    accept."""
    own_patient(db, me, patient_id)
    try:
        phone = normalize_phone(body.phone)
    except AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    if phone == me.phone:
        raise HTTPException(422, "That is your own number")
    trusted = db.trusted_people(patient_id)
    other = db.account_by_phone(phone)
    if other is not None and other.person_id in trusted:
        raise HTTPException(409, "They are already one of your trusted people")
    open_invites = [i for i in db.invites_from(patient_id) if i.is_open()]
    if any(i.phone == phone for i in open_invites):
        raise HTTPException(409, "You have already invited this number")
    if len(trusted) + len(open_invites) >= MAX_TRUSTED:
        raise HTTPException(409, f"At most {MAX_TRUSTED} trusted people, invitations included")
    inv = TrustedInvite(patient_id=patient_id, phone=phone)
    db.save_invite(inv)
    return _sent(inv)


@router.delete("/patients/{patient_id}/trusted/invites/{invite_id}", status_code=204)
def cancel_invite(patient_id: str, invite_id: str, db: DB, me: Me) -> None:
    own_patient(db, me, patient_id)
    inv = db.get_invite(invite_id)
    if inv is None or inv.patient_id != patient_id or not inv.is_open():
        raise HTTPException(404, "Invitation not found")
    inv.status, inv.answered_at = "cancelled", datetime.now(UTC)
    db.save_invite(inv)


class ReceivedInvite(BaseModel):
    """An open invitation, as the person invited sees it."""

    id: str
    from_name: str
    created_at: datetime
    expires_at: datetime


@router.get("/invites")
def my_invites(db: DB, me: Me) -> list[ReceivedInvite]:
    """Invitations to the signed-in person's own number. Only patient accounts have a record
    to be trusted from (a doctor account has none)."""
    if me.person_id is None or me.phone is None:
        return []
    return [ReceivedInvite(id=i.id, from_name=_name(db, i.patient_id), created_at=i.created_at,
                           expires_at=i.expires_at)
            for i in db.invites_to(me.phone) if i.is_open() and i.patient_id != me.person_id]


def _my_invite(db: Storage, me: Account, invite_id: str) -> TrustedInvite:
    inv = db.get_invite(invite_id)
    if (inv is None or me.person_id is None or inv.phone != me.phone or not inv.is_open()
            or inv.patient_id == me.person_id):
        raise HTTPException(404, "Invitation not found")
    return inv


@router.post("/invites/{invite_id}/accept")
def accept_invite(invite_id: str, db: DB, me: Me) -> list[TrustedPerson]:
    """Become the inviting patient's trusted person. Returns 'people who trust me'."""
    inv = _my_invite(db, me, invite_id)
    assert me.person_id is not None
    db.add_trusted(TrustedLink(patient_id=inv.patient_id, trusted_id=me.person_id))
    inv.status, inv.answered_at = "accepted", datetime.now(UTC)
    db.save_invite(inv)
    _tell(db, inv.patient_id, inv.patient_id,
          f"{_name(db, me.person_id)} accepted your invitation and is now one of your trusted people.")
    return _people(db, db.patients_trusting(me.person_id))


@router.post("/invites/{invite_id}/decline", status_code=204)
def decline_invite(invite_id: str, db: DB, me: Me) -> None:
    inv = _my_invite(db, me, invite_id)
    inv.status, inv.answered_at = "declined", datetime.now(UTC)
    db.save_invite(inv)
    _tell(db, inv.patient_id, inv.patient_id,
          f"The person at {mask_phone(inv.phone)} declined your invitation to be a trusted person.")


@router.get("/people/{person_id}/trusted-by")
def trusted_by(person_id: str, db: DB, me: Me) -> list[TrustedPerson]:
    """'People who trust me', the trusted person's entry point: their own list only."""
    if me.person_id is None or me.person_id != person_id:
        raise HTTPException(404, "Not found")
    return _people(db, db.patients_trusting(person_id))


@router.delete("/people/{person_id}/trusted-by/{patient_id}")
def stop_being_trusted(person_id: str, patient_id: str, db: DB, me: Me) -> list[TrustedPerson]:
    """A trusted person steps down. The patient is told."""
    if me.person_id is None or me.person_id != person_id:
        raise HTTPException(404, "Not found")
    if not db.remove_trusted(patient_id, person_id):
        raise HTTPException(404, "Not found")
    _tell(db, patient_id, patient_id,
          f"{_name(db, person_id)} is no longer one of your trusted people (they chose to step down).")
    return _people(db, db.patients_trusting(person_id))
