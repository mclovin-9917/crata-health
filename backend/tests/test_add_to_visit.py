"""The patient adds something to a visit (POST /encounters/{id}/entries). It is their own
statement: confirmed and marked as added by them."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.main import create_app
from app.schemas.access import TrustedLink
from app.schemas.account import Account, AccountRole
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    SourceType,
    Speaker,
    Tier,
)
from app.storage import Storage
from tests.conftest import api_client


def build():
    db = Storage()
    asha = Patient(name="Asha Verma (synthetic)")
    other = Patient(name="Someone Else (synthetic)")
    ravi = Patient(name="Ravi Verma (synthetic)")
    for p in (asha, other, ravi):
        db.save_patient(p)
    db.add_trusted(TrustedLink(patient_id=asha.id, trusted_id=ravi.id))
    doctor = Account(role=AccountRole.DOCTOR, name="Dr. A. Mehta (synthetic)", registry_id="SYN-1")
    app = create_app(db)
    return SimpleNamespace(
        db=db, asha=asha, other=other, ravi=ravi, doctor=doctor,
        a=api_client(app, person=asha.id),
        o=api_client(app, person=other.id),
        d=api_client(app, account=doctor),
        guest=api_client(app),
    )


@pytest.fixture
def ctx():
    c = build()
    yield c
    c.db.close()


def own_visit(c, status=EncounterStatus.READY, **kw):
    enc = Encounter(patient_id=c.asha.id, status=status, visit_date=date(2026, 10, 1), **kw,
                    entries=[Entry(kind=EntryKind.NOTE, label="Rest")])
    c.db.save_encounter(enc)
    return enc


def add(client, enc_id, **body):
    body.setdefault("kind", "note")
    body.setdefault("label", "Drink more water")
    return client.post(f"/encounters/{enc_id}/entries", json=body)


def test_adding_to_an_own_visit(ctx):
    enc = own_visit(ctx)
    r = add(ctx.a, enc.id, kind="symptom", label="  Dizzy in the morning ",
            fields={"since": "Monday", "empty": ""})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["label"] == "Dizzy in the morning" and body["kind"] == "symptom"
    assert body["status"] == "confirmed" and body["said_by"] == "patient"
    assert body["encounter_id"] == enc.id
    assert body["patient_id"] == ctx.asha.id
    assert body["fields"] == {"since": "Monday"}
    assert body["provenance"][0]["source_id"] == "manual"
    assert body["provenance"][0]["source_type"] == SourceType.MANUAL.value
    [h] = body["status_history"]
    assert h["status"] == "confirmed" and h["by"] == ctx.asha.id and h["note"] == "added by the patient"
    saved = ctx.db.get_encounter(enc.id)
    assert {e.label for e in saved.entries} == {"Rest", "Dizzy in the morning"}
    assert ctx.a.get(f"/encounters/{enc.id}").status_code == 200


def test_a_medicine_keeps_its_details_and_is_on_the_card(ctx):
    enc = own_visit(ctx)
    r = add(ctx.a, enc.id, kind="medication", label="Cetirizine 10 mg",
            medication={"dose": "10 mg", "doses_per_day": 1, "duration_days": 5})
    assert r.status_code == 201, r.text
    e = ctx.db.get_entry(r.json()["id"])
    assert e.tier == Tier.EMERGENCY_CARD
    assert e.medication.dose == "10 mg" and e.medication.doses_per_day == 1
    assert e.medication.duration_days == 5 and e.medication.started_on == date(2026, 10, 1)
    assert e.said_by == Speaker.PATIENT


def test_a_medicine_without_details_and_an_ongoing_one(ctx):
    enc = own_visit(ctx)
    plain = add(ctx.a, enc.id, kind="medication", label="Vitamin D").json()
    assert ctx.db.get_entry(plain["id"]).medication.duration_days is None
    long_term = add(ctx.a, enc.id, kind="medication", label="Amlodipine 5 mg",
                    medication={"ongoing": True, "duration_days": 30}).json()
    m = ctx.db.get_entry(long_term["id"]).medication
    assert m.ongoing and m.duration_days is None


def test_medicine_details_only_for_a_medicine(ctx):
    enc = own_visit(ctx)
    assert add(ctx.a, enc.id, kind="note", medication={"dose": "1"}).status_code == 422


def test_every_addable_kind_works(ctx):
    enc = own_visit(ctx)
    for kind in ("medication", "test", "note", "symptom", "diagnosis", "allergy", "follow_up"):
        assert add(ctx.a, enc.id, kind=kind, label=f"A {kind}").status_code == 201, kind


def test_bad_kind_or_label_is_422(ctx):
    enc = own_visit(ctx)
    for body in ({"kind": "surgery"}, {"kind": "result"}, {"kind": "banana"}, {"label": ""},
                 {"label": "   "}, {"label": "x" * 121}, {"fields": {"a": "x" * 201}},
                 {"medication": {"doses_per_day": -1}, "kind": "medication"},
                 {"medication": {"duration_days": 0}, "kind": "medication"}):
        assert add(ctx.a, enc.id, **body).status_code == 422, body
    assert len(ctx.db.get_encounter(enc.id).entries) == 1
    r = ctx.a.post(f"/encounters/{enc.id}/entries", json={"kind": "note"})
    assert r.status_code == 422


def test_nobody_else_can_add_to_a_visit(ctx):
    own = own_visit(ctx)
    assert add(ctx.o, own.id).status_code == 404  # another patient
    assert add(ctx.d, own.id).status_code == 404  # a doctor
    assert add(ctx.guest, own.id).status_code == 401
    assert add(ctx.a, "nope").status_code == 404
    assert len(ctx.db.get_encounter(own.id).entries) == 1


def test_a_visit_being_read_answers_409(ctx):
    enc = own_visit(ctx, status=EncounterStatus.PROCESSING)
    assert add(ctx.a, enc.id).status_code == 409
    assert len(ctx.db.get_encounter(enc.id).entries) == 1


def test_a_failed_visit_answers_409(ctx):
    enc = own_visit(ctx, status=EncounterStatus.FAILED)
    assert add(ctx.a, enc.id).status_code == 409
    assert len(ctx.db.get_encounter(enc.id).entries) == 1


def test_a_medicine_gets_its_generic_names_so_the_allergy_check_sees_it(ctx):
    enc = own_visit(ctx)
    r = add(ctx.a, enc.id, kind="medication", label="Augmentin 625")
    assert r.status_code == 201
    m = ctx.db.get_entry(r.json()["id"]).medication
    assert "amoxicillin" in m.generic_names and m.generic_from == "table"


def test_the_patient_can_edit_and_remove_what_they_added(ctx):
    enc = own_visit(ctx)
    eid = add(ctx.a, enc.id, kind="symptom", label="Headache").json()["id"]
    r = ctx.a.patch(f"/entries/{eid}", json={"action": "edit", "label": "Headache at night"})
    assert r.status_code == 200 and r.json()["label"] == "Headache at night"
    assert r.json()["edits"][0]["by"] == ctx.asha.id
    r = ctx.a.patch(f"/entries/{eid}", json={"action": "reject"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert ctx.db.get_entry(eid).status == EntryStatus.REJECTED


def test_an_allergy_added_this_way_reaches_the_emergency_card(ctx):
    enc = own_visit(ctx)
    eid = add(ctx.a, enc.id, kind="allergy", label="Penicillin").json()["id"]
    assert ctx.db.get_entry(eid).tier == Tier.EMERGENCY_CARD
    card = ctx.a.get(f"/patients/{ctx.asha.id}/emergency").json()
    assert [e["label"] for e in card["allergies"]] == ["Penicillin"]
    ravi = api_client(ctx.a.app, person=ctx.ravi.id)  # a trusted person's basics
    basics = ravi.post(f"/patients/{ctx.asha.id}/access/trusted-emergency").json()["basics"]
    assert [a["label"] for a in basics["allergies"]] == ["Penicillin"]


def test_a_current_medicine_added_this_way_is_on_the_card(ctx):
    enc = own_visit(ctx)
    add(ctx.a, enc.id, kind="medication", label="Metformin 500 mg",
        medication={"ongoing": True})
    card = ctx.a.get(f"/patients/{ctx.asha.id}/emergency").json()
    assert [m["entry"]["label"] for m in card["medications"]] == ["Metformin 500 mg"]
