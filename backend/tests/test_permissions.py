"""Every patient route answers only the patient who owns the record.

Signed out: 401. Another patient, a trusted person, a doctor account or an admin: 404, as if the
record did not exist. Doctors and trusted people reach a record only through the emergency
routes (tests/test_access_api.py).
"""

import pytest

from app.main import create_app
from app.schemas.access import TrustedLink
from app.schemas.account import Account, AccountRole
from app.schemas.assist import VisitPrep
from app.schemas.patient import Patient
from app.schemas.record import Encounter, EncounterStatus, Entry, EntryKind
from app.storage import Storage
from tests.conftest import api_client

OWNER, TRUSTED, OTHER = "p-owner", "p-trusted", "p-other"
PREP_ID = "prep-owner"


@pytest.fixture(scope="module")
def world():
    db = Storage()
    for pid in (OWNER, TRUSTED, OTHER):
        db.save_patient(Patient(id=pid, name=f"{pid} (synthetic)"))
    db.add_trusted(TrustedLink(patient_id=OWNER, trusted_id=TRUSTED))
    entry = Entry(kind=EntryKind.NOTE, label="A note")
    enc = Encounter(patient_id=OWNER, status=EncounterStatus.READY, entries=[entry])
    db.save_encounter(enc)
    db.save_visit_prep(VisitPrep(id=PREP_ID, patient_id=OWNER, text="a cough"))
    return create_app(db), enc.id, entry.id


def _routes(enc_id: str, entry_id: str) -> list[tuple[str, str, dict]]:
    return [
        ("get", f"/patients/{OWNER}", {}),
        ("get", f"/patients/{OWNER}/encounters", {}),
        ("post", f"/patients/{OWNER}/encounters", {"data": {"notes": "x"}}),
        ("get", f"/encounters/{enc_id}", {}),
        ("patch", f"/encounters/{enc_id}", {"json": {"visit_date": "2026-01-01"}}),
        ("patch", f"/encounters/{enc_id}", {"json": {"title": "Fever and cough"}}),
        ("get", f"/encounters/{enc_id}/removal", {}),
        ("post", f"/encounters/{enc_id}/attach", {"json": {"encounter_id": "some-visit", "test_entry_ids": ["some-test"]}}),
        ("delete", f"/encounters/{enc_id}/attach", {}),
        ("delete", f"/encounters/{enc_id}", {}),
        ("post", f"/encounters/{enc_id}/reread",
         {"files": [("files", ("a.jpg", b"x", "image/jpeg"))], "data": {"source_ids": ["s"]}}),
        ("patch", f"/entries/{entry_id}", {"json": {"action": "reject"}}),
        ("post", f"/patients/{OWNER}/events", {"json": {"kind": "surgery", "label": "x"}}),
        ("post", f"/patients/{OWNER}/stays",
         {"json": {"kind": "admission", "started_on": "2026-01-01"}}),
        ("patch", f"/encounters/{enc_id}/stay", {"json": {"place": "x"}}),
        ("post", f"/encounters/{enc_id}/documents", {"data": {"notes": "x"}}),
        ("post", f"/encounters/{enc_id}/documents", {"data": {"test_id": "some-test"}}),
        ("post", f"/encounters/{enc_id}/review", {"json": {"action": "confirm", "entry_ids": [entry_id]}}),
        ("get", f"/patients/{OWNER}/numbers", {}),
        ("post", f"/patients/{OWNER}/readings",
         {"json": {"readings": [{"name": "Weight", "value": 62}]}}),
        ("get", f"/patients/{OWNER}/record", {}),
        ("get", f"/patients/{OWNER}/emergency", {}),
        ("get", f"/patients/{OWNER}/access-log", {}),
        ("get", f"/patients/{OWNER}/trusted", {}),
        ("delete", f"/patients/{OWNER}/trusted/{TRUSTED}", {}),
        ("get", f"/patients/{OWNER}/trusted/invites", {}),
        ("post", f"/patients/{OWNER}/trusted/invites", {"json": {"phone": "9000099999"}}),
        ("delete", f"/patients/{OWNER}/trusted/invites/some-invite", {}),
        ("get", f"/people/{OWNER}/trusted-by", {}),
        ("delete", f"/people/{OWNER}/trusted-by/{OTHER}", {}),
        ("post", f"/encounters/{enc_id}/entries", {"json": {"kind": "note", "label": "x"}}),
        ("post", f"/patients/{OWNER}/visit-preps", {"json": {"symptoms": "a cough"}}),
        ("post", f"/patients/{OWNER}/visit-preps/questions", {"json": {"symptoms": "a cough"}}),
        ("get", f"/patients/{OWNER}/visit-preps", {}),
        ("get", f"/visit-preps/{PREP_ID}", {}),
        ("delete", f"/visit-preps/{PREP_ID}", {}),
        ("post", f"/patients/{OWNER}/ask", {"json": {"question": "Can I eat rice?"}}),
        ("post", f"/patients/{OWNER}/voice",
         {"files": {"audio": ("voice.wav", b"RIFF", "audio/wav")}}),
    ]


def _accounts():
    patient = AccountRole.PATIENT
    return {
        "another patient": Account(role=patient, name="Other", person_id=OTHER),
        "their trusted person": Account(role=patient, name="Trusted", person_id=TRUSTED),
        "a doctor": Account(role=AccountRole.DOCTOR, name="Dr", registry_id="SYN-1"),
        "an admin": Account(role=patient, name="Admin", admin=True),
    }


def test_signed_out_gets_401_everywhere(world):
    app, enc_id, entry_id = world
    client = api_client(app)
    for method, url, kw in _routes(enc_id, entry_id):
        assert getattr(client, method)(url, **kw).status_code == 401, (method, url)
    for method, url in [("get", "/patients"), ("get", "/meta"), ("get", "/alerts"),
                        ("get", "/medicine-info?name=x"), ("patch", "/alerts/x"),
                        ("get", "/invites"), ("post", "/invites/x/accept"),
                        ("post", "/invites/x/decline")]:
        kw = {"json": {}} if method == "patch" else {}
        assert getattr(client, method)(url, **kw).status_code == 401, (method, url)


@pytest.mark.parametrize("who", list(_accounts()))
def test_nobody_else_can_tell_the_record_exists(world, who):
    app, enc_id, entry_id = world
    client = api_client(app, account=_accounts()[who])
    for method, url, kw in _routes(enc_id, entry_id):
        assert getattr(client, method)(url, **kw).status_code == 404, (who, method, url)


def test_the_owner_gets_in(world):
    app, enc_id, _ = world
    client = api_client(app, person=OWNER)
    assert client.get(f"/patients/{OWNER}/record").status_code == 200
    assert client.get(f"/encounters/{enc_id}").status_code == 200
    assert client.get("/patients").status_code == 403 and client.get("/meta").status_code == 403
