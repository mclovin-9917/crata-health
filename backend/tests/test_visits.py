"""Consolidation across visits, visit dates, who-said-it rules."""

from datetime import date, timedelta

import pytest

from app.main import create_app
from app.pipeline.extract import ExtractedItem, Extraction, document_date, to_entries
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    Medication,
    SourceItem,
    SourceType,
    Speaker,
    Tier,
    VisitDateSource,
)
from app.storage import Storage
from app.views import consolidate, emergency_summary, fact_key, visits
from tests.conftest import api_client

TODAY = date(2026, 10, 12)


def _confirmed(kind, label, encounter_id=None, **kw):
    e = Entry(patient_id="p1", encounter_id=encounter_id, kind=kind, label=label, **kw)
    e.set_status(EntryStatus.CONFIRMED, by="p1")
    return e


def test_fact_key_matches_the_same_fact_written_differently():
    a = _confirmed(EntryKind.ALLERGY, "Penicillin")
    assert fact_key(a) == fact_key(_confirmed(EntryKind.ALLERGY, "penicillin allergy"))
    assert fact_key(_confirmed(EntryKind.ALLERGY, "Sulfa drugs")) == fact_key(
        _confirmed(EntryKind.ALLERGY, "sulfa"))
    med = _confirmed(EntryKind.MEDICATION, "Azithromycin 500 mg")
    assert fact_key(med) == fact_key(_confirmed(EntryKind.MEDICATION, "azithromycin 250mg"))
    assert fact_key(med) != fact_key(_confirmed(EntryKind.MEDICATION, "Amoxicillin 500 mg"))
    assert fact_key(a) != fact_key(_confirmed(EntryKind.NOTE, "Penicillin"))  # kind matters


def test_consolidate_shows_each_fact_once_with_every_sighting():
    dates = {"v1": date(2026, 9, 1), "v2": date(2026, 10, 1)}
    entries = [
        _confirmed(EntryKind.ALLERGY, "Penicillin", "v1"),
        _confirmed(EntryKind.ALLERGY, "penicillin", "v2", fields={"reaction": "rash"}),
        _confirmed(EntryKind.DIAGNOSIS, "Hypertension", "v1"),
    ]
    facts = {f.key: f for f in consolidate(entries, dates)}
    pen = facts["allergy:penicillin"]
    assert pen.latest.fields == {"reaction": "rash"}  # newest statement wins
    assert [s.encounter_id for s in pen.sightings] == ["v2", "v1"]
    assert len(facts) == 2


def test_emergency_card_lists_each_allergy_once_and_uses_the_latest_course():
    dates = {"v1": TODAY - timedelta(days=60), "v2": TODAY}
    old_course = _confirmed(EntryKind.MEDICATION, "Azithromycin 500 mg", "v1",
                            medication=Medication(duration_days=3, started_on=dates["v1"]))
    new_course = _confirmed(EntryKind.MEDICATION, "Azithromycin 500 mg", "v2",
                            medication=Medication(duration_days=3, started_on=TODAY))
    encounters = [Encounter(id=k, patient_id="p1", visit_date=d) for k, d in dates.items()]
    s = emergency_summary(
        Patient(name="Asha"),
        [_confirmed(EntryKind.ALLERGY, "Penicillin", "v1"),
         _confirmed(EntryKind.ALLERGY, "Penicillin", "v2"), old_course, new_course],
        encounters, today=TODAY,
    )
    assert [a.label for a in s.allergies] == ["Penicillin"]
    assert [(m.entry.id, m.state.value) for m in s.medications] == [(new_course.id, "current")]


def test_a_stopped_latest_course_hides_the_medicine_even_if_an_older_one_has_no_end():
    dates = {"v1": TODAY - timedelta(days=60), "v2": TODAY - timedelta(days=1)}
    old = _confirmed(EntryKind.MEDICATION, "Paracetamol 650 mg", "v1")  # no end known
    new = _confirmed(EntryKind.MEDICATION, "Paracetamol 650 mg", "v2",
                     medication=Medication(started_on=dates["v2"], stopped_on=TODAY))
    encounters = [Encounter(id=k, patient_id="p1", visit_date=d) for k, d in dates.items()]
    s = emergency_summary(Patient(name="Asha"), [old, new], encounters, today=TODAY)
    assert s.medications == []


def test_visits_are_newest_first_with_hand_added_entries_last():
    v1 = Encounter(id="v1", patient_id="p1", visit_date=date(2026, 9, 1),
                   status=EncounterStatus.READY)
    v2 = Encounter(id="v2", patient_id="p1", visit_date=date(2026, 10, 1),
                   status=EncounterStatus.READY,
                   sources=[SourceItem(source_type=SourceType.IMAGE, filename="rx.png")])
    pending = Entry(patient_id="p1", encounter_id="v2", kind=EntryKind.NOTE, label="pending")
    entries = [_confirmed(EntryKind.ALLERGY, "Penicillin", "v1"),
               _confirmed(EntryKind.DIAGNOSIS, "Bronchitis", "v2"), pending,
               _confirmed(EntryKind.SURGERY, "Appendectomy")]
    out = visits([v1, v2], entries)
    assert [v.encounter_id for v in out] == ["v2", "v1", None]
    assert out[0].pending == 1 and [e.label for e in out[0].entries] == ["Bronchitis"]
    assert out[0].sources[0].filename == "rx.png"
    assert [e.label for e in out[2].entries] == ["Appendectomy"]


# ---- who said it -------------------------------------------------------------------------
def _item(**kw):
    return ExtractedItem(**{"confidence": 0.9, "source_id": "audio1", **kw})


AUDIO = SourceItem(source_type=SourceType.AUDIO,
                   text="I think it is an infection. I take amlodipine daily. Take crocin if needed. "
                        "Doctor: this is acute bronchitis. Take azithromycin 500 mg once a day.")


def test_patient_words_cannot_create_a_diagnosis_or_prescription():
    items = [
        _item(kind=EntryKind.DIAGNOSIS, label="Infection", said_by="patient",
              quote="I think it is an infection"),
        _item(kind=EntryKind.MEDICATION, label="Amlodipine", said_by="patient", ongoing=True,
              quote="I take amlodipine daily"),
        _item(kind=EntryKind.MEDICATION, label="Crocin", said_by="patient",
              quote="Take crocin if needed"),
        _item(kind=EntryKind.DIAGNOSIS, label="Acute bronchitis", said_by="doctor",
              quote="this is acute bronchitis"),
        _item(kind=EntryKind.MEDICATION, label="Azithromycin 500 mg", said_by="doctor",
              quote="Take azithromycin 500 mg once a day"),
    ]
    entries, failed = to_entries(Extraction(items=items), {"audio1": AUDIO}, TODAY)
    assert failed == []
    got = [(e.kind, e.label, e.said_by) for e in entries]
    assert got == [
        (EntryKind.SYMPTOM, "Infection", Speaker.PATIENT),  # a guess, not a diagnosis
        (EntryKind.MEDICATION, "Amlodipine", Speaker.PATIENT),  # history: already taken
        (EntryKind.NOTE, "Patient mentions Crocin", Speaker.PATIENT),  # not a prescription
        (EntryKind.DIAGNOSIS, "Acute bronchitis", Speaker.DOCTOR),
        (EntryKind.MEDICATION, "Azithromycin 500 mg", Speaker.DOCTOR),
    ]
    assert entries[0].tier == Tier.FULL_RECORD and entries[2].medication is None


def test_documents_always_speak_as_document():
    doc = SourceItem(source_type=SourceType.IMAGE, text="Dx: Acute bronchitis")
    item = _item(kind=EntryKind.DIAGNOSIS, label="Acute bronchitis", said_by="patient",
                 source_id="doc1", quote="Dx: Acute bronchitis")
    (entry,), _ = to_entries(Extraction(items=[item]), {"doc1": doc}, TODAY)
    assert entry.said_by == Speaker.DOCUMENT and entry.kind == EntryKind.DIAGNOSIS


@pytest.mark.parametrize(("printed", "expected"), [
    ("2026-10-12", date(2026, 10, 12)),
    ("2026-13-01", None),  # not a date
    ("2027-01-01", None),  # in the future
    ("1901-01-01", None),  # implausible
    (None, None),
])
def test_document_date_is_checked(printed, expected):
    assert document_date(Extraction(items=[], visit_date=printed), TODAY) == expected


# ---- through the API ---------------------------------------------------------------------
def test_api_visit_date_review_hints_and_correction():
    db = Storage()
    p = Patient(name="Asha")
    db.save_patient(p)
    known = _confirmed(EntryKind.ALLERGY, "Penicillin")
    known.patient_id = p.id
    db.save_entry(known)
    enc = Encounter(patient_id=p.id, status=EncounterStatus.READY,
                    visit_date=date(2026, 10, 1), visit_date_source=VisitDateSource.UPLOAD,
                    entries=[Entry(kind=EntryKind.ALLERGY, label="penicillin allergy"),
                             Entry(kind=EntryKind.ALLERGY, label="Sulfa drugs")])
    db.save_encounter(enc)
    client = api_client(create_app(db), person=p.id)

    view = client.get(f"/encounters/{enc.id}").json()
    assert view["already_in_record"] == {enc.entries[0].id: "Penicillin"}

    r = client.patch(f"/encounters/{enc.id}", json={"visit_date": "2026-09-30"})
    assert r.json()["visit_date"] == "2026-09-30" and r.json()["visit_date_source"] == "patient"
    future = (date.today() + timedelta(days=2)).isoformat()
    assert client.patch(f"/encounters/{enc.id}", json={"visit_date": future}).status_code == 422

    record = client.get(f"/patients/{p.id}/record").json()
    assert record["visits"][0]["visit_date"] == "2026-09-30"


def test_upload_dates_the_visit_by_the_upload_day():
    from tests.test_pipeline import FakeAI

    db = Storage()
    p = Patient(name="Asha")
    db.save_patient(p)
    client = api_client(create_app(db, FakeAI([])), person=p.id)
    r = client.post(f"/patients/{p.id}/encounters", data={"notes": "nothing much"})
    assert r.json()["visit_date_source"] == "upload" and r.json()["visit_date"]
