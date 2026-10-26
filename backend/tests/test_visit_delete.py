"""Deleting a visit: what the confirmation says, that everything goes, and that nobody else's
visit (or the patient's other visits) is touched; a visit cut off by a restart can be put right."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.api import routes
from app.main import create_app
from app.pipeline.run import INTERRUPTED, recover_interrupted_reads
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    HospitalStay,
    Medication,
    SourceItem,
    SourceType,
    TestResult,
)
from app.storage import Storage
from app.views import consolidate
from tests.conftest import api_client

TODAY = date.today()


def _entry(kind, label, **kw):
    kw.setdefault("status", EntryStatus.CONFIRMED)
    return Entry(kind=kind, label=label, **kw)


def _visit(patient, days_ago, *entries, **kw):
    return Encounter(patient_id=patient.id, status=EncounterStatus.READY,
                     visit_date=TODAY - timedelta(days=days_ago), entries=list(entries), **kw)


@pytest.fixture
def ctx():
    db = Storage()
    patient = Patient(name="Meera Iyer (synthetic)")
    db.save_patient(patient)
    first = _visit(patient, 5, _entry(EntryKind.ALLERGY, "Penicillin"))
    test = _entry(EntryKind.TEST, "CBC", added_as_read=True)
    value = _entry(EntryKind.RESULT, "Hemoglobin", added_as_read=True, links=[test.id],
                   result=TestResult(value=13.2))
    second = _visit(
        patient, 1,
        _entry(EntryKind.ALLERGY, "Penicillin"),  # also in the first visit: stays in the record
        _entry(EntryKind.ALLERGY, "Sulfa drugs"),  # only here: leaves, and the card
        _entry(EntryKind.MEDICATION, "Dolo 650", medication=Medication(ongoing=True)),
        _entry(EntryKind.NOTE, "Steam inhalation", added_as_read=True),
        test, value,
        _entry(EntryKind.DIAGNOSIS, "Acute bronchitis", status=EntryStatus.PROPOSED),
    )
    db.save_encounter(first)
    db.save_encounter(second)
    db.save_entry(_entry(EntryKind.SURGERY, "Appendectomy", patient_id=patient.id))  # by hand
    app = create_app(db)
    yield api_client(app, person=patient.id), app, patient, first, second
    db.close()


def _removal(client, visit):
    return client.get(f"/encounters/{visit.id}/removal").json()


def _labels(items):
    return {x["label"] for x in items}


def test_the_confirmation_says_what_leaves_the_record_and_the_card(ctx):
    client, _, _, _, second = ctx
    got = _removal(client, second)
    assert got["confirmed"] == 6 and got["values"] == 1 and got["waiting"] == 1
    assert got["stay"] is False and got["visit_date"] == second.visit_date.isoformat()
    # Penicillin is in the other visit too, so it stays; the values are counted, not listed.
    assert _labels(got["leaving"]) == {"Sulfa drugs", "Dolo 650", "Steam inhalation", "CBC"}
    assert _labels(got["leaves_card"]) == {"Sulfa drugs", "Dolo 650"}
    assert got["joins_card"] == []


def test_a_fact_said_twice_in_one_visit_is_listed_once(ctx):
    client, app, patient, _, _ = ctx
    twice = _visit(patient, 0, _entry(EntryKind.NOTE, "Rest", added_as_read=True),
                   _entry(EntryKind.NOTE, "Rest", added_as_read=True))
    app.state.storage.save_encounter(twice)
    assert [x["label"] for x in _removal(client, twice)["leaving"]] == ["Rest"]


def test_the_card_shows_the_latest_statement_so_the_panel_works_from_the_card(ctx):
    client, app, patient, _, _ = ctx
    db = app.state.storage
    # An old course that has ended, then a newer ongoing one: deleting the newer one takes the
    # medicine off the card although another visit still says it.
    old = _entry(EntryKind.MEDICATION, "Amlodipine 5 mg",
                 medication=Medication(duration_days=5, started_on=TODAY - timedelta(days=60)))
    new = _entry(EntryKind.MEDICATION, "Amlodipine 5 mg", medication=Medication(ongoing=True))
    db.save_encounter(_visit(patient, 60, old))
    newer = _visit(patient, 2, new)
    db.save_encounter(newer)
    got = _removal(client, newer)
    assert got["leaving"] == [] and _labels(got["leaves_card"]) == {"Amlodipine 5 mg"}


def test_deleting_a_stopped_course_brings_the_older_one_back_onto_the_card(ctx):
    client, app, patient, _, _ = ctx
    db = app.state.storage
    db.save_encounter(_visit(patient, 60, _entry(EntryKind.MEDICATION, "Metformin 500 mg",
                                                 medication=Medication(ongoing=True))))
    stopped = _visit(patient, 2, _entry(EntryKind.MEDICATION, "Metformin 500 mg",
                                        medication=Medication(ongoing=True,
                                                              stopped_on=TODAY - timedelta(days=1))))
    db.save_encounter(stopped)
    got = _removal(client, stopped)
    assert _labels(got["joins_card"]) == {"Metformin 500 mg"} and got["leaves_card"] == []


def test_a_stay_is_named_as_the_admission_not_as_a_diagnosis(ctx):
    client, app, patient, _, _ = ctx
    admission = _entry(EntryKind.ADMISSION, "Pneumonia", occurred_on=str(TODAY - timedelta(days=3)),
                       unverified=True)
    stay = _visit(patient, 3, admission, stay=HospitalStay(
        kind="admission", place="City Hospital", started_on=TODAY - timedelta(days=3),
        entry_id=admission.id))
    app.state.storage.save_encounter(stay)
    got = _removal(client, stay)
    assert got["stay"] is True
    assert "Hospital admission: Pneumonia" in _labels(got["leaves_card"])
    assert any(x.startswith('Shown as "In hospital now"') for x in _labels(got["leaves_card"]))


def test_deleting_a_visit_erases_it_and_leaves_the_rest(ctx):
    client, app, patient, first, second = ctx
    assert client.delete(f"/encounters/{second.id}").status_code == 204
    assert client.get(f"/encounters/{second.id}").status_code == 404
    db = app.state.storage
    assert [e.label for e in db.list_entries(patient.id)] == ["Penicillin", "Appendectomy"]
    card = client.get(f"/patients/{patient.id}/emergency").json()
    assert [e["label"] for e in card["allergies"]] == ["Penicillin"] and card["medications"] == []
    assert [v["encounter_id"] for v in client.get(f"/patients/{patient.id}/record").json()["visits"]
            if v["encounter_id"]] == [first.id]


def test_a_visit_being_read_is_not_deleted(ctx):
    client, app, _, _, second = ctx
    busy = app.state.storage.get_encounter(second.id)
    busy.status = EncounterStatus.PROCESSING
    app.state.storage.save_encounter(busy)
    assert client.delete(f"/encounters/{second.id}").status_code == 409


def test_nobody_else_can_see_or_delete_it(ctx):
    _, app, _, _, second = ctx
    other = api_client(app, person="someone-else")
    assert other.get(f"/encounters/{second.id}/removal").status_code == 404
    assert other.delete(f"/encounters/{second.id}").status_code == 404
    assert api_client(app).delete(f"/encounters/{second.id}").status_code == 401
    assert app.state.storage.get_encounter(second.id) is not None


def test_a_translation_that_finishes_after_the_delete_brings_nothing_back(ctx, monkeypatch):
    client, app, _, _, second = ctx
    db = app.state.storage
    medicine = next(e for e in db.get_encounter(second.id).entries if e.label == "Dolo 650")
    monkeypatch.setattr(routes, "translate_confirmed", lambda e, ai: True)
    assert client.delete(f"/encounters/{second.id}").status_code == 204
    routes.translate_later(medicine.id, db, app.state.ai)
    assert db.get_entry(medicine.id) is None  # an update that never inserts


# ---- a read cut off by a restart ---------------------------------------------------------------
def test_a_read_cut_off_by_a_restart_is_put_right_and_can_then_be_deleted(ctx):
    client, app, patient, _, _ = ctx
    db = app.state.storage
    never_read = Encounter(patient_id=patient.id, status=EncounterStatus.PROCESSING, sources=[
        SourceItem(source_type=SourceType.IMAGE, filename="rx.jpg")])
    reread = _visit(patient, 4, _entry(EntryKind.NOTE, "Rest", added_as_read=True), sources=[
        SourceItem(source_type=SourceType.IMAGE, filename="rx.jpg", text="Rest"),
        SourceItem(source_type=SourceType.IMAGE, filename="rx.jpg", careful=True)])  # never read
    reread.status = EncounterStatus.PROCESSING
    db.save_encounter(never_read)
    db.save_encounter(reread)

    assert recover_interrupted_reads(db) == 2
    new = db.get_encounter(never_read.id)
    assert new.status == EncounterStatus.FAILED and new.error.startswith(INTERRUPTED)
    assert len(new.sources) == 1  # kept: the browser's files are retried against them
    old = db.get_encounter(reread.id)
    assert old.status == EncounterStatus.READY and old.error == INTERRUPTED
    assert [e.label for e in old.entries] == ["Rest"] and len(old.sources) == 1
    assert client.delete(f"/encounters/{never_read.id}").status_code == 204
    assert recover_interrupted_reads(db) == 0


# ---- which statement of a fact is the latest ---------------------------------------------------
def test_the_latest_statement_on_the_same_day_is_from_the_visit_uploaded_last():
    day = TODAY - timedelta(days=2)
    older, newer = (_entry(EntryKind.TEST, "CBC", added_as_read=True, encounter_id=i) for i in "ab")
    uploaded = {"a": datetime(2026, 10, 18, 9, 0, tzinfo=UTC), "b": datetime(2026, 10, 18, 9, 30, tzinfo=UTC)}
    for order in ([older, newer], [newer, older]):  # whatever the storage order
        (fact,) = consolidate(order, {"a": day, "b": day}, uploaded)
        assert fact.latest is newer


def test_confirming_order_does_not_decide_the_latest_statement():
    # Proposals have no history until they are confirmed, so an earlier upload confirmed last
    # must still not win over a later upload on the same day.
    day = TODAY - timedelta(days=2)
    later_upload, earlier_upload = (
        _entry(EntryKind.MEDICATION, "Dolo 650", encounter_id=i, medication=Medication(ongoing=True))
        for i in "ba")
    uploaded = {"a": datetime(2026, 10, 18, 9, 0, tzinfo=UTC), "b": datetime(2026, 10, 18, 9, 30, tzinfo=UTC)}
    (fact,) = consolidate([earlier_upload, later_upload], {"a": day, "b": day}, uploaded)
    assert fact.latest is later_upload
