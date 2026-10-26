"""A visit's name and place: suggested by the model, checked and tidied in code, editable."""

import json
from datetime import date
from pathlib import Path

from app.main import create_app
from app.pipeline.extract import Extraction, clean_title, facility_of
from app.pipeline.run import Media, process_encounter
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    HospitalStay,
    SourceItem,
    SourceType,
)
from app.seed import demo_visits
from app.storage import Storage
from app.views import visits
from tests.conftest import api_client
from tests.test_pipeline import (  # noqa: F401
    CAREFUL_ITEMS,
    DOC,
    FakeAI,
    _item,
    _reviewed_visit,
    setup,
)

NOTES = "Seen at Sunrise Clinic (synthetic). Patient has fever and cough since two days."
FOUND = {
    "visit_title": "Fever and cough",
    "facility": "Sunrise Clinic",
    "facility_source_id": "notes1",
    "facility_quote": "Seen at Sunrise Clinic",
}


def _read(enc: Encounter, extra: dict | None, db: Storage | None = None) -> Encounter:
    db = db or Storage()
    db.save_encounter(enc)
    process_encounter(db, FakeAI([], extra=extra), enc.id, [], notes=NOTES)
    return db.get_encounter(enc.id)


def test_the_title_and_the_facility_are_stored():
    done = _read(Encounter(patient_id="p1"), FOUND)
    assert done.status == EncounterStatus.READY
    assert (done.title, done.title_source) == ("Fever and cough", "ai")
    assert done.facility == "Sunrise Clinic"
    assert done.facility_quote == "Seen at Sunrise Clinic"
    assert done.facility_source_id == done.sources[0].id


def test_a_facility_whose_quote_is_not_in_the_source_is_dropped():
    done = _read(Encounter(patient_id="p1"), {**FOUND, "facility_quote": "Moonlight Hospital"})
    assert done.facility is None and done.facility_quote is None
    assert done.title == "Fever and cough"  # the name does not depend on it
    # The name must be inside its quote, and the source id must exist.
    done = _read(Encounter(patient_id="p1"), {**FOUND, "facility": "Moonlight Hospital"})
    assert done.facility is None
    done = _read(Encounter(patient_id="p1"), {**FOUND, "facility_source_id": "doc7"})
    assert done.facility is None


def test_the_title_is_trimmed_cut_to_60_characters_and_dropped_if_empty():
    assert clean_title("  Fever   and cough \n") == "Fever and cough"
    assert len(clean_title("word " * 30)) <= 60
    assert clean_title("   ") is None and clean_title(None) is None
    done = _read(Encounter(patient_id="p1"), {"visit_title": "   "})
    assert done.title is None and done.title_source is None


def test_facility_of_needs_everything():
    src = SourceItem(id="s1", source_type=SourceType.TEXT, text=NOTES)
    found = Extraction(items=[], **{**FOUND, "facility_source_id": "x"})
    assert facility_of(found, {"x": src}) == (
        "Sunrise Clinic", "Seen at Sunrise Clinic", "s1", None)
    assert facility_of(Extraction(items=[]), {"x": src}) is None
    # Never from a transcript, wherever the words are.
    audio = SourceItem(id="s2", source_type=SourceType.AUDIO, text=NOTES)
    assert facility_of(found, {"x": audio}) is None
    # On a PDF with several pages, the page is kept.
    pages = SourceItem(id="s3", source_type=SourceType.IMAGE, text=f"Page one {NOTES}",
                       page_texts=["Page one", NOTES])
    assert facility_of(found, {"x": pages})[3] == "page 2"


def test_a_reread_never_replaces_a_patients_own_title(setup):  # noqa: F811
    db, enc, media = _reviewed_visit(setup)
    # The first read had no title; the patient named the visit; a careful re-read suggests one.
    done = db.get_encounter(enc.id)
    done.title, done.title_source = "My knee", "patient"
    db.save_encounter(done)
    ai = FakeAI(CAREFUL_ITEMS, extra={"visit_title": "Fever and cough"})
    process_encounter(db, ai, enc.id, media, careful=True)
    again = db.get_encounter(enc.id)
    assert (again.title, again.title_source) == ("My knee", "patient")


def test_a_reread_replaces_an_ai_title_and_keeps_a_facility_it_does_not_find(setup):  # noqa: F811
    db, enc, media = _reviewed_visit(setup)
    done = db.get_encounter(enc.id)
    done.title, done.title_source = "Old name", "ai"
    done.facility, done.facility_quote = "Sunrise Clinic", "Sunrise Clinic"
    db.save_encounter(done)
    ai = FakeAI(CAREFUL_ITEMS, extra={"visit_title": "Antibiotic course"})
    process_encounter(db, ai, enc.id, media, careful=True)
    again = db.get_encounter(enc.id)
    assert (again.title, again.title_source) == ("Antibiotic course", "ai")
    assert again.facility == "Sunrise Clinic"


def test_a_facility_is_found_in_a_document():
    db = Storage()
    enc = Encounter(patient_id="p1", sources=[SourceItem(source_type=SourceType.IMAGE)])
    db.save_encounter(enc)
    media = [Media(source_id=enc.sources[0].id, kind="image", data=b"img", mime="image/jpeg")]
    extra = {"visit_title": "Throat infection", "facility": "Rx",
             "facility_source_id": "doc1", "facility_quote": "Rx: Azithromycin"}
    process_encounter(db, FakeAI([], extra=extra), enc.id, media)
    done = db.get_encounter(enc.id)
    assert done.facility == "Rx" and DOC.startswith(done.facility_quote)


def test_a_stays_facility_is_its_place():
    stay = HospitalStay(kind="admission", place="City Hospital (synthetic)",
                        started_on=date(2026, 10, 1), entry_id="e1")
    done = _read(Encounter(patient_id="p1", stay=stay), FOUND)
    assert done.facility == "City Hospital (synthetic)"
    assert done.title is None  # nor for a stay: it has its kind and place
    # A stay without a place falls back to what a document prints.
    stay = HospitalStay(kind="emergency_visit", started_on=date(2026, 10, 1), entry_id="e1")
    done = _read(Encounter(patient_id="p1", stay=stay), FOUND)
    assert done.facility == "Sunrise Clinic"


def test_old_visits_load_without_a_name():
    old = json.loads(Encounter(patient_id="p1").model_dump_json())
    for key in ("title", "title_source", "facility", "facility_quote", "facility_source_id",
                "facility_locator"):
        old.pop(key)
    enc = Encounter.model_validate(old)
    assert enc.title is None and enc.facility is None
    visit = visits([enc], [])[0]
    assert visit.title is None and visit.title_source is None and visit.facility is None


def test_the_visit_view_carries_the_name_and_facility():
    enc = Encounter(patient_id="p1", title="Fever and cough", title_source="ai",
                    facility="Sunrise Clinic", status=EncounterStatus.READY)
    visit = visits([enc], [])[0]
    assert (visit.title, visit.title_source, visit.facility) == (
        "Fever and cough", "ai", "Sunrise Clinic")


def test_demo_visits_without_the_new_keys_still_load():
    path = Path(__file__).parent.parent / "app" / "demo_visits.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert rows
    assert all(isinstance(enc, Encounter) for enc in demo_visits())
    for row in rows:  # a file made before names existed has none of the keys
        for key in ("title", "title_source", "facility", "facility_quote", "facility_source_id",
                    "facility_locator"):
            row.pop(key, None)
        assert Encounter.model_validate(row).title is None


# ---- the patient edits the name -----------------------------------------------------------
def _api():
    db = Storage()
    p = Patient(name="Asha (synthetic)")
    other = Patient(name="Ravi (synthetic)")
    db.save_patient(p)
    db.save_patient(other)
    entry = Entry(kind=EntryKind.NOTE, label="A note")
    enc = Encounter(patient_id=p.id, status=EncounterStatus.READY, title="Visit one",
                    title_source="ai", visit_date=date(2026, 10, 1), entries=[entry])
    db.save_encounter(enc)
    return db, p, other, enc


def test_the_patient_can_rename_a_visit():
    db, p, _, enc = _api()
    client = api_client(create_app(db), person=p.id)
    r = client.patch(f"/encounters/{enc.id}", json={"title": "  Fever   and cough "})
    assert r.status_code == 200
    assert r.json()["title"] == "Fever and cough" and r.json()["title_source"] == "patient"
    assert r.json()["visit_date"] == "2026-10-01"  # untouched
    record = client.get(f"/patients/{p.id}/record").json()
    assert record["visits"][0]["title"] == "Fever and cough"
    assert record["visits"][0]["title_source"] == "patient"


def test_a_name_and_a_date_can_be_sent_together_or_alone_but_not_neither():
    db, p, _, enc = _api()
    client = api_client(create_app(db), person=p.id)
    r = client.patch(f"/encounters/{enc.id}", json={"title": "Check-up", "visit_date": "2026-09-30"})
    assert r.json()["title"] == "Check-up" and r.json()["visit_date"] == "2026-09-30"
    assert r.json()["visit_date_source"] == "patient"
    r = client.patch(f"/encounters/{enc.id}", json={"visit_date": "2026-09-29"})
    assert r.json()["title"] == "Check-up" and r.json()["title_source"] == "patient"
    assert client.patch(f"/encounters/{enc.id}", json={}).status_code == 422


def test_a_name_must_be_1_to_60_characters():
    db, p, _, enc = _api()
    client = api_client(create_app(db), person=p.id)
    for bad in ("", "   ", "x" * 61):
        assert client.patch(f"/encounters/{enc.id}", json={"title": bad}).status_code == 422, bad
    assert client.patch(f"/encounters/{enc.id}", json={"title": "x" * 60}).status_code == 200
    assert db.get_encounter(enc.id).title == "x" * 60


def test_nobody_else_can_rename_a_visit():
    db, _p, other, enc = _api()
    app = create_app(db)
    assert api_client(app, person=other.id).patch(
        f"/encounters/{enc.id}", json={"title": "Not mine"}).status_code == 404
    assert db.get_encounter(enc.id).title == "Visit one"


# ---- when a name is set, and which names are allowed -----------------------------------------
def test_files_added_later_never_rename_the_visit_or_move_its_facility():
    db = Storage()
    enc = Encounter(patient_id="p1", title="Old name", title_source="ai", facility="Old Clinic")
    db.save_encounter(enc)
    process_encounter(db, FakeAI([], extra=FOUND), enc.id, [], notes=NOTES, adding=True)
    done = db.get_encounter(enc.id)
    assert (done.title, done.title_source, done.facility) == ("Old name", "ai", "Old Clinic")


def test_a_name_with_the_patients_name_or_a_medicine_is_dropped():
    db = Storage()
    p = Patient(name="Asha Verma (synthetic)")
    db.save_patient(p)
    notes = "Dr. says take azithromycin 500 mg once a day for the throat."
    items = [_item(kind=EntryKind.MEDICATION, label="Azithromycin 500 mg", source_id="notes1",
                   quote="azithromycin 500 mg once a day", dose="500 mg", doses_per_day=1)]

    def read(title: str) -> Encounter:
        enc = Encounter(patient_id=p.id)
        db.save_encounter(enc)
        process_encounter(db, FakeAI(items, extra={"visit_title": title}), enc.id, [], notes=notes)
        return db.get_encounter(enc.id)

    assert read("Throat infection").title == "Throat infection"
    assert read("Asha's fever").title is None
    assert read("VERMA check-up").title is None
    assert read("Azithromycin course").title is None


def test_a_clean_title_avoids_given_words():
    assert clean_title("Fever and cough", {"asha"}) == "Fever and cough"
    assert clean_title("Asha fever", {"asha"}) is None


def test_a_patients_own_name_survives_a_full_read():
    done = _read(Encounter(patient_id="p1", title="Mine", title_source="patient"), FOUND)
    assert (done.title, done.title_source) == ("Mine", "patient")


def test_a_visit_being_read_cannot_be_renamed_or_redated():
    db, p, _, _ = _api()
    reading = Encounter(patient_id=p.id)  # processing by default
    db.save_encounter(reading)
    client = api_client(create_app(db), person=p.id)
    assert client.patch(f"/encounters/{reading.id}", json={"title": "x"}).status_code == 409
    assert client.patch(f"/encounters/{reading.id}",
                        json={"visit_date": "2026-09-01"}).status_code == 409
    assert db.get_encounter(reading.id).title is None
