from types import SimpleNamespace

import pytest

from app.main import create_app
from app.schemas.access import TrustedLink
from app.schemas.account import Account, AccountRole
from app.schemas.patient import Patient
from app.schemas.record import Entry, EntryKind, EntryStatus, Tier
from app.storage import Storage
from tests.conftest import api_client

PLACE = {"place": "City Hospital ER (synthetic)"}
NO_ACCOUNT = {"actor_label": "Dr. B (synthetic)", "registry_id": "SYN-777", **PLACE}


@pytest.fixture
def ctx():
    """Asha (p1) trusts Ravi (p2). One client per person, all on the same app."""
    db = Storage()
    p1 = Patient(name="Asha Verma (synthetic)", blood_group="B+")
    p2 = Patient(name="Ravi Verma (synthetic)")
    p3 = Patient(name="Someone Else (synthetic)")
    for p in (p1, p2, p3):
        db.save_patient(p)
    allergy = Entry(patient_id=p1.id, kind=EntryKind.ALLERGY, label="Penicillin",
                    tier=Tier.EMERGENCY_CARD)
    allergy.set_status(EntryStatus.CONFIRMED, by=p1.id)
    surgery = Entry(patient_id=p1.id, kind=EntryKind.SURGERY, label="Appendectomy",
                    occurred_on="2015", tier=Tier.CLINICAL_SUMMARY)
    surgery.set_status(EntryStatus.CONFIRMED, by=p1.id)
    db.save_entry(allergy)
    db.save_entry(surgery)
    app = create_app(db)
    doctor = Account(role=AccountRole.DOCTOR, name="Dr. A (synthetic)", registry_id="SYN-12345")
    c = SimpleNamespace(
        db=db, p1=p1, p2=p2, p3=p3,
        asha=api_client(app, person=p1.id),
        ravi=api_client(app, account=Account(role=AccountRole.PATIENT, name=p2.name,
                                             person_id=p2.id)),
        stranger=api_client(app, person=p3.id),
        doctor=api_client(app, account=doctor),
        guest=api_client(app),  # signed out: a doctor with no account
    )
    db.add_trusted(TrustedLink(patient_id=p1.id, trusted_id=p2.id))  # invites: test_trusted_invites
    yield c
    db.close()


def _open(client, patient, **extra):
    return client.post(f"/patients/{patient.id}/access/emergency", json={**PLACE, **extra})


def test_signed_in_doctor_opens_the_card_under_their_account(ctx):
    r = _open(ctx.doctor, ctx.p1, actor_label="Someone Else", registry_id="FAKE")  # ignored
    assert r.status_code == 201
    body = r.json()
    assert [a["label"] for a in body["summary"]["allergies"]] == ["Penicillin"]
    assert [e["label"] for e in body["summary"]["timeline"]] == ["Appendectomy"]
    entry = body["log_entry"]
    assert entry["actor_label"] == "Dr. A (synthetic)" and entry["actor_registry_id"] == "SYN-12345"
    assert entry["verified"] is False and entry["tiers"] == ["clinical_summary", "emergency_card"]
    assert sorted(body["alerted"]) == sorted([ctx.p1.id, ctx.p2.id])

    alert = ctx.asha.get("/alerts").json()[0]
    assert "Dr. A (synthetic)" in alert["message"] and "not verified" in alert["message"]
    assert "City Hospital ER" in alert["message"] and "no account" not in alert["message"]
    assert ctx.ravi.get("/alerts").json()[0]["log_entry_id"] == entry["id"]


def test_a_doctor_without_an_account_can_still_open_it(ctx):
    assert _open(ctx.guest, ctx.p1, actor_label="Dr. B").status_code == 422  # no registry ID
    assert ctx.asha.get(f"/patients/{ctx.p1.id}/access-log").json()["entries"] == []
    r = _open(ctx.guest, ctx.p1, **NO_ACCOUNT)
    assert r.status_code == 201
    entry = r.json()["log_entry"]
    assert entry["actor_label"] == "Dr. B (synthetic) (no account)"
    assert entry["actor_id"].startswith("no-account:")
    assert "(no account)" in ctx.asha.get("/alerts").json()[0]["message"]


def test_patient_accounts_cannot_use_the_doctor_route(ctx):
    assert _open(ctx.stranger, ctx.p1, **NO_ACCOUNT).status_code == 403
    assert _open(ctx.ravi, ctx.p1, role="trusted").status_code == 403


def _start(client, patient):
    return client.post(f"/patients/{patient.id}/access/trusted-emergency")


def test_trusted_person_gets_basics_and_a_code_never_the_summary(ctx):
    assert ctx.ravi.get(f"/people/{ctx.p2.id}/trusted-by").json() == [
        {"id": ctx.p1.id, "name": ctx.p1.name}]
    r = _start(ctx.ravi, ctx.p1)
    assert r.status_code == 201
    body = r.json()
    assert body["basics"] == {"name": ctx.p1.name, "blood_group": "B+",
                              "allergies": [{"label": "Penicillin", "reaction": None}]}
    assert "summary" not in body and "medications" not in body["basics"]
    assert len(body["code"]) >= 20
    assert body["alerted"] == [ctx.p1.id]  # the trusted person isn't alerted about themself
    entry = body["log_entry"]
    assert entry["actor_role"] == "trusted" and entry["tiers"] == []
    assert entry["actor_id"] == ctx.p2.id and entry["actor_label"] == ctx.p2.name  # from the session
    alert = ctx.asha.get("/alerts").json()[0]
    assert "started an emergency" in alert["message"] and "only the name" in alert["message"]


def test_strangers_cannot_start_an_emergency_or_read_lists(ctx):
    assert _start(ctx.stranger, ctx.p1).status_code == 404
    assert _start(ctx.guest, ctx.p1).status_code == 401
    assert _start(ctx.asha, ctx.p1).status_code == 404  # not her own trusted person
    assert ctx.asha.get(f"/patients/{ctx.p1.id}/access-log").json()["entries"] == []
    assert ctx.stranger.get(f"/people/{ctx.p2.id}/trusted-by").status_code == 404
    assert ctx.ravi.get(f"/patients/{ctx.p1.id}/trusted").status_code == 404


def test_a_doctor_opens_the_card_with_the_code_once(ctx):
    code = _start(ctx.ravi, ctx.p1).json()["code"]
    r = _open(ctx.doctor, ctx.p1, code=code)
    assert r.status_code == 201
    assert r.json()["log_entry"]["via"] == ctx.p2.name
    alert = ctx.asha.get("/alerts").json()[0]
    assert f"using an emergency code from {ctx.p2.name}" in alert["message"]
    assert _open(ctx.doctor, ctx.p1, code=code).status_code == 403  # single use
    log = ctx.asha.get(f"/patients/{ctx.p1.id}/access-log").json()
    assert log["chain_intact"] is True and len(log["entries"]) == 2


def test_expired_wrong_or_unknown_codes_are_refused(ctx):
    from datetime import UTC, datetime, timedelta

    code = _start(ctx.ravi, ctx.p1).json()["code"]
    stored = ctx.db.get_code(code)
    stored.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    ctx.db.save_code(stored)
    assert _open(ctx.doctor, ctx.p1, code=code).status_code == 403
    assert _open(ctx.doctor, ctx.p1, code="made-up").status_code == 403
    fresh = _start(ctx.ravi, ctx.p1).json()["code"]
    assert _open(ctx.doctor, ctx.p2, code=fresh).status_code == 403  # another patient


def test_access_log_is_chained_ordered_and_only_for_the_patient(ctx):
    _open(ctx.doctor, ctx.p1)
    _start(ctx.ravi, ctx.p1)
    log = ctx.asha.get(f"/patients/{ctx.p1.id}/access-log").json()
    assert log["chain_intact"] is True
    assert [e["actor_role"] for e in log["entries"]] == ["doctor", "trusted"]
    assert log["entries"][1]["prev_hash"] == log["entries"][0]["hash"]
    for other in (ctx.ravi, ctx.doctor, ctx.stranger):
        assert other.get(f"/patients/{ctx.p1.id}/access-log").status_code == 404
    assert ctx.guest.get(f"/patients/{ctx.p1.id}/access-log").status_code == 401


def test_alerts_are_your_own(ctx):
    _open(ctx.doctor, ctx.p1)
    alert = ctx.asha.get("/alerts").json()[0]
    assert alert["read"] is False
    # Someone else's alerts: not listed, not changeable. A doctor account has none.
    assert ctx.asha.get("/alerts", params={"recipient_id": ctx.p2.id}).json()[0]["recipient_id"] \
        == ctx.p1.id
    assert ctx.stranger.patch(f"/alerts/{alert['id']}", json={"read": True}).status_code == 404
    assert ctx.doctor.get("/alerts").json() == []
    r = ctx.asha.patch(f"/alerts/{alert['id']}", json={"reported": True})
    assert r.json()["reported"] is True and r.json()["read"] is True
    r = ctx.asha.patch(f"/alerts/{alert['id']}", json={"reported": False, "read": False})
    assert r.json()["reported"] is True  # a report cannot be taken back
    assert ctx.asha.patch("/alerts/nope", json={"read": True}).status_code == 404


def test_trusted_list_rules(ctx):
    url = f"/patients/{ctx.p1.id}/trusted"
    assert ctx.asha.get(url).json() == [{"id": ctx.p2.id, "name": ctx.p2.name}]
    assert ctx.stranger.get(url).status_code == 404
