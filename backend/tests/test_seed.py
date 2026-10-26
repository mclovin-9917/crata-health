from datetime import date

from app.main import create_app
from app.pipeline.quotes import quote_in_source
from app.schemas.access import TrustedLink
from app.schemas.patient import Patient
from app.schemas.record import ESSENTIAL_KINDS, EncounterStatus, Entry, EntryKind, EntryStatus
from app.seed import DEMO_PATIENT_ID, DEMO_PEOPLE, DEMO_TRUSTED_ID, demo_visits, seed_demo
from app.storage import Storage
from tests.conftest import api_client


def test_seed_makes_the_demo_people():
    db = Storage()
    seed_demo(db, visits=False)
    assert {p.id for p in db.list_patients()} == {p.person_id for p in DEMO_PEOPLE if p.person_id}
    assert db.trusted_people(DEMO_PATIENT_ID) == [DEMO_TRUSTED_ID]
    assert db.list_entries("demo-meera") == []  # Meera starts empty, for the testing guide

    card = api_client(create_app(db), person=DEMO_PATIENT_ID).get(f"/patients/{DEMO_PATIENT_ID}/emergency").json()
    assert [a["label"] for a in card["allergies"]] == ["Sulfa drugs"]
    assert [(m["entry"]["label"], m["state"]) for m in card["medications"]] == [
        ("Amlodipine 5 mg", "current")
    ]
    assert [c["label"] for c in card["conditions"]] == ["Hypertension"]
    assert [e["label"] for e in card["timeline"]] == ["Appendectomy", "Dengue fever, ER visit"]


def test_seed_starts_the_demo_people_again_and_leaves_everyone_else():
    db = Storage()
    seed_demo(db)
    asha_entries = len(db.list_entries(DEMO_PATIENT_ID))
    # What demo visitors did: a new entry, a trust link, a changed profile.
    db.save_entry(Entry(kind=EntryKind.NOTE, label="Added by a visitor", patient_id="demo-meera"))
    db.add_trusted(TrustedLink(patient_id="demo-meera", trusted_id="demo-kabir"))
    db.save_patient(Patient(id=DEMO_PATIENT_ID, name="Changed"))
    # Someone who registered with their own number, and their link to a demo person.
    db.save_patient(Patient(id="p-real", name="Tara Sen"))
    db.save_entry(Entry(kind=EntryKind.NOTE, label="Mine", patient_id="p-real"))

    seed_demo(db)
    assert db.list_entries("demo-meera") == []
    assert db.trusted_people("demo-meera") == []
    assert db.get_patient(DEMO_PATIENT_ID).name == "Asha Verma"
    assert len(db.list_entries(DEMO_PATIENT_ID)) == asha_entries
    assert db.trusted_people(DEMO_PATIENT_ID) == [DEMO_TRUSTED_ID]
    assert [e.label for e in db.list_entries("p-real")] == ["Mine"]


def test_ashas_saved_visits():
    """The visits in demo_visits.json came from a real run; they must still load and keep the
    rules: every quote is in its source, the old visit is confirmed, the planted one waits."""
    visits = demo_visits()
    assert [v.visit_date for v in visits] == sorted(v.visit_date for v in visits)  # oldest first
    assert len(visits) == 4  # two lab reports, a check-up and the planted prescription and bill
    old = next(v for v in visits if v.visit_date == date(2026, 7, 14))
    planted = visits[-1]
    assert old.visit_date < planted.visit_date
    for v in visits:
        assert v.status == EncounterStatus.READY and v.patient_id == DEMO_PATIENT_ID
        texts = {s.id: s.text or "" for s in v.sources}
        for e in v.entries:
            for p in e.provenance:
                assert quote_in_source(p.quote or "", texts[p.source_id]), e.label
    assert old.entries and all(e.status == EntryStatus.CONFIRMED for e in old.entries)
    # The essentials wait for the patient; the rest was read as it is.
    waiting = [e for e in planted.entries if e.kind in ESSENTIAL_KINDS]
    assert waiting and all(e.status == EntryStatus.PROPOSED for e in waiting)
    assert all(e.status == EntryStatus.CONFIRMED and e.added_as_read
               for e in planted.entries if e.kind not in ESSENTIAL_KINDS)
    checks = {d.check for d in planted.discrepancies}
    assert {"allergy_clash", "billed_not_prescribed"} <= checks

    db = Storage()
    seed_demo(db)
    review = api_client(create_app(db), person=DEMO_PATIENT_ID).get(f"/encounters/{planted.id}")
    assert review.status_code == 200
