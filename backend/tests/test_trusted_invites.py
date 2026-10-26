"""Trusted people by invitation: a patient invites a phone number, its owner accepts or declines.
Either side can end the link; the other side is told."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.main import create_app
from app.schemas.access import TrustedInvite
from app.schemas.account import Account, AccountRole
from app.schemas.patient import Patient
from app.storage import Storage
from tests.conftest import api_client

MEERA_PHONE, KABIR_PHONE, ROHAN_PHONE, DOCTOR_PHONE = (
    "+919000010001", "+919000010002", "+919000010004", "+919000010003")


@pytest.fixture
def ctx():
    db = Storage()
    meera, kabir, rohan = (Patient(name=f"{n} (synthetic)") for n in ("Meera Iyer", "Kabir Iyer",
                                                                     "Rohan Das"))
    for p in (meera, kabir, rohan):
        db.save_patient(p)
    app = create_app(db)

    def patient(p, phone):
        return api_client(app, account=Account(role=AccountRole.PATIENT, name=p.name,
                                               person_id=p.id, phone=phone))

    c = SimpleNamespace(
        db=db, meera=meera, kabir=kabir, rohan=rohan,
        as_meera=patient(meera, MEERA_PHONE), as_kabir=patient(kabir, KABIR_PHONE),
        as_rohan=patient(rohan, ROHAN_PHONE),
        as_doctor=api_client(app, account=Account(role=AccountRole.DOCTOR, name="Dr. N. Rao",
                                                  registry_id="SYN-2002", phone=DOCTOR_PHONE)),
    )
    yield c
    db.close()


def _invite(c, phone="90000 10002"):
    return c.as_meera.post(f"/patients/{c.meera.id}/trusted/invites", json={"phone": phone})


def test_an_accepted_invitation_makes_a_trusted_person(ctx):
    r = _invite(ctx)
    assert r.status_code == 201 and r.json()["phone"] == KABIR_PHONE
    assert [i["phone"] for i in ctx.as_meera.get(f"/patients/{ctx.meera.id}/trusted/invites").json()] \
        == [KABIR_PHONE]
    assert ctx.as_meera.get(f"/patients/{ctx.meera.id}/trusted").json() == []  # not yet

    (inv,) = ctx.as_kabir.get("/invites").json()
    assert inv["from_name"] == ctx.meera.name
    r = ctx.as_kabir.post(f"/invites/{inv['id']}/accept")
    assert r.status_code == 200 and [p["id"] for p in r.json()] == [ctx.meera.id]

    assert [p["name"] for p in ctx.as_meera.get(f"/patients/{ctx.meera.id}/trusted").json()] \
        == [ctx.kabir.name]
    assert ctx.as_meera.get(f"/patients/{ctx.meera.id}/trusted/invites").json() == []
    assert ctx.as_kabir.get("/invites").json() == []
    (alert,) = ctx.as_meera.get("/alerts").json()
    assert alert["message"].startswith("Kabir Iyer (synthetic) accepted your invitation")
    assert alert["log_entry_id"] is None
    # The new trusted person can start an emergency for her.
    assert ctx.as_kabir.post(f"/patients/{ctx.meera.id}/access/trusted-emergency").status_code == 201


def test_a_declined_invitation_links_nobody_and_tells_the_patient(ctx):
    inv_id = _invite(ctx).json()["id"]
    assert ctx.as_kabir.post(f"/invites/{inv_id}/decline").status_code == 204
    assert ctx.as_meera.get(f"/patients/{ctx.meera.id}/trusted").json() == []
    (alert,) = ctx.as_meera.get("/alerts").json()
    assert "declined" in alert["message"] and "010002" not in alert["message"]  # number masked
    assert ctx.as_kabir.post(f"/invites/{inv_id}/accept").status_code == 404  # answered already


def test_only_the_invited_number_can_answer(ctx):
    inv_id = _invite(ctx).json()["id"]
    assert ctx.as_rohan.get("/invites").json() == []
    assert ctx.as_rohan.post(f"/invites/{inv_id}/accept").status_code == 404
    assert ctx.as_meera.post(f"/invites/{inv_id}/accept").status_code == 404  # not her own
    # A doctor account has no record to be trusted from, even with the invited number.
    _invite(ctx, phone=DOCTOR_PHONE)
    assert ctx.as_doctor.get("/invites").json() == []


@pytest.mark.parametrize(("phone", "status"), [
    ("90000 10001", 422),  # her own number
    ("12345", 422),  # not a phone number
])
def test_bad_numbers_are_refused(ctx, phone, status):
    assert _invite(ctx, phone).status_code == status


def test_repeats_and_the_limit_are_refused(ctx):
    assert _invite(ctx).status_code == 201
    assert _invite(ctx).status_code == 409  # invited already
    for n in range(3, 7):
        assert _invite(ctx, f"90000 2000{n}").status_code == 201
    assert _invite(ctx, "90000 20009").status_code == 409  # 5 at most, invitations included

    inv = ctx.as_kabir.get("/invites").json()[0]
    ctx.as_kabir.post(f"/invites/{inv['id']}/accept")
    assert _invite(ctx).status_code == 409  # already a trusted person


def test_the_patient_can_cancel_an_invitation(ctx):
    inv_id = _invite(ctx).json()["id"]
    url = f"/patients/{ctx.meera.id}/trusted/invites/{inv_id}"
    assert ctx.as_rohan.delete(url).status_code == 404  # not his
    assert ctx.as_meera.delete(url).status_code == 204
    assert ctx.as_kabir.get("/invites").json() == []
    assert ctx.as_kabir.post(f"/invites/{inv_id}/accept").status_code == 404
    assert ctx.as_meera.delete(url).status_code == 404


def test_an_unanswered_invitation_lapses(ctx):
    old = TrustedInvite(patient_id=ctx.meera.id, phone=KABIR_PHONE)
    old.created_at -= timedelta(days=15)
    ctx.db.save_invite(old)
    assert ctx.as_kabir.get("/invites").json() == []
    assert ctx.as_kabir.post(f"/invites/{old.id}/accept").status_code == 404
    assert _invite(ctx).status_code == 201  # a fresh one can be sent


def _linked(ctx):
    inv_id = _invite(ctx).json()["id"]
    ctx.as_kabir.post(f"/invites/{inv_id}/accept")


def test_the_patient_can_remove_a_trusted_person_who_is_told(ctx):
    _linked(ctx)
    url = f"/patients/{ctx.meera.id}/trusted/{ctx.kabir.id}"
    assert ctx.as_meera.delete(url).json() == []
    assert ctx.as_kabir.get(f"/people/{ctx.kabir.id}/trusted-by").json() == []
    (alert,) = ctx.as_kabir.get("/alerts").json()
    assert alert["message"].startswith("Meera Iyer (synthetic) removed you as a trusted person")
    assert ctx.as_meera.delete(url).status_code == 404
    assert ctx.as_kabir.post(f"/patients/{ctx.meera.id}/access/trusted-emergency").status_code == 404


def test_a_trusted_person_can_step_down_and_the_patient_is_told(ctx):
    _linked(ctx)
    url = f"/people/{ctx.kabir.id}/trusted-by/{ctx.meera.id}"
    assert ctx.as_rohan.delete(url).status_code == 404  # only Kabir himself
    assert ctx.as_kabir.delete(url).json() == []
    assert ctx.as_meera.get(f"/patients/{ctx.meera.id}/trusted").json() == []
    messages = [a["message"] for a in ctx.as_meera.get("/alerts").json()]
    assert any("step down" in m for m in messages)
