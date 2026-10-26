"""The lighter Review: only essential kinds wait for a tap, the rest is read as it is, a row the
code doubts is flagged, and a box is confirmed or turned down in one request."""

import pytest

from app.main import create_app
from app.pipeline.review import LOW_CONFIDENCE, READ_NOTE, flagged, read_as_is
from app.pipeline.run import Media, process_encounter
from app.schemas.patient import Patient
from app.schemas.record import (
    ESSENTIAL_KINDS,
    Discrepancy,
    Encounter,
    EncounterStatus,
    Entry,
    EntryCheck,
    EntryKind,
    EntryStatus,
    SourceItem,
    SourceType,
)
from app.storage import Storage
from tests.conftest import api_client
from tests.test_pipeline import FakeAI, _item

PAGE = (
    "Complaints: fever and cough\n"
    "Diagnosis: acute bronchitis\n"
    "Rx: Azithromycin 500 mg once a day for 3 days\n"
    "Allergy noted: Penicillin\n"
    "Advice: steam inhalation\n"
    "Review after 5 days\n"
    "Test: CBC"
)


class PageAI(FakeAI):
    def perceive(self, parts, instruction, **kw):
        self.calls.append(("perceive", parts[0]["type"], instruction))
        return PAGE


def _doc(kind, label, quote, **kw):
    return _item(kind=kind, label=label, source_id="doc1", quote=quote, said_by="document", **kw)


ITEMS = [
    _doc("symptom", "Fever", "fever and cough"),
    _doc("diagnosis", "Acute bronchitis", "Diagnosis: acute bronchitis"),
    _doc("medication", "Azithromycin 500 mg", "Azithromycin 500 mg once a day for 3 days",
         doses_per_day=1, duration_value=3, duration_unit="day"),
    _doc("allergy", "Penicillin", "Allergy noted: Penicillin"),
    _doc("note", "Steam inhalation", "Advice: steam inhalation"),
    _doc("follow_up", "Review after 5 days", "Review after 5 days"),
    _doc("test", "CBC", "Test: CBC"),
]


@pytest.fixture
def db():
    s = Storage()
    yield s
    s.close()


def _read(db, items=ITEMS):
    enc = Encounter(patient_id="p1", sources=[SourceItem(source_type=SourceType.IMAGE, filename="r.jpg")])
    db.save_encounter(enc)
    process_encounter(db, PageAI(items), enc.id, [Media(enc.sources[0].id, "image", b"i", "image/jpeg")])
    return db.get_encounter(enc.id)


# ---- the pipeline ----------------------------------------------------------------------------
def test_only_the_essential_kinds_wait_for_a_tap():
    assert ESSENTIAL_KINDS == {
        EntryKind.MEDICATION, EntryKind.ALLERGY, EntryKind.DIAGNOSIS,
        EntryKind.EMERGENCY_VISIT, EntryKind.ADMISSION, EntryKind.PROCEDURE, EntryKind.SURGERY,
        EntryKind.IMPLANT,
    }


def test_a_visit_is_read_with_the_essentials_waiting_and_the_rest_in_the_record(db):
    done = _read(db)
    by_kind = {e.kind: e for e in done.entries}
    for kind in ESSENTIAL_KINDS & by_kind.keys():
        assert by_kind[kind].status == EntryStatus.PROPOSED and not by_kind[kind].added_as_read
    for kind in (EntryKind.SYMPTOM, EntryKind.NOTE, EntryKind.FOLLOW_UP, EntryKind.TEST):
        e = by_kind[kind]
        assert e.status == EntryStatus.CONFIRMED and e.added_as_read
        assert e.status_history[-1].by == "system" and e.status_history[-1].note == READ_NOTE
    assert {k for k, e in by_kind.items() if e.status == EntryStatus.PROPOSED} == {
        EntryKind.DIAGNOSIS, EntryKind.MEDICATION, EntryKind.ALLERGY}


def test_read_as_is_leaves_decided_entries_alone():
    done = Entry(kind=EntryKind.NOTE, label="x", status=EntryStatus.REJECTED)
    read_as_is([done])
    assert done.status == EntryStatus.REJECTED and not done.added_as_read


def _reread(db, enc):
    process_encounter(db, PageAI(ITEMS), enc.id,
                      [Media(enc.sources[0].id, "image", b"i", "image/jpeg")], careful=True)
    return db.get_encounter(enc.id)


def test_a_careful_reread_replaces_untouched_facts_read_as_is_and_keeps_edited_ones(db):
    first = _read(db)
    note = next(e for e in first.entries if e.kind == EntryKind.NOTE)
    note.apply_edit("p1", label="Steam inhalation twice a day")
    db.save_entry(note)

    done = _reread(db, first)
    live = [(e.kind.value, e.label) for e in done.entries
            if e.added_as_read and e.status == EntryStatus.CONFIRMED]
    # The note was corrected by the patient, so it stays and its repeat is dropped; the symptom,
    # follow-up and test were never touched, so the new reading replaces them.
    assert sorted(live) == [("follow_up", "Review after 5 days"), ("note", "Steam inhalation twice a day"),
                            ("symptom", "Fever"), ("test", "CBC")]
    assert sum(e.kind == EntryKind.SYMPTOM for e in done.entries) == 2  # old one rejected, new one live
    assert sum(e.kind == EntryKind.NOTE for e in done.entries) == 1


def test_a_careful_reread_does_not_duplicate_facts_read_from_typed_notes(db):
    enc = Encounter(patient_id="p1", sources=[SourceItem(source_type=SourceType.IMAGE, filename="r.jpg")])
    db.save_encounter(enc)
    typed = [_item(kind="symptom", label="Fever", source_id="notes1", quote="fever for three days",
                   said_by="patient")]
    ai = PageAI(typed)
    process_encounter(db, ai, enc.id, [Media(enc.sources[0].id, "image", b"i", "image/jpeg")],
                      "fever for three days")
    first = db.get_encounter(enc.id)
    assert [e.status for e in first.entries] == [EntryStatus.CONFIRMED]

    process_encounter(db, PageAI(typed), enc.id,
                      [Media(first.sources[0].id, "image", b"i", "image/jpeg")], careful=True)
    done = db.get_encounter(enc.id)
    live = [e for e in done.entries if e.status == EntryStatus.CONFIRMED]
    assert [e.label for e in live] == ["Fever"]  # the first reading was replaced, not repeated


# ---- what is flagged -------------------------------------------------------------------------
def _entry(kind=EntryKind.MEDICATION, **kw):
    return Entry(kind=kind, label=kw.pop("label", "x"), **kw)


def test_a_check_note_or_unclear_wording_flags_an_entry():
    checked = _entry(checks=[EntryCheck.MEDICINE_UNNAMED])
    unsure = _entry(confidence=LOW_CONFIDENCE - 0.01)
    sure = _entry(confidence=LOW_CONFIDENCE)
    assert flagged([checked, unsure, sure], []) == {checked.id: "check", unsure.id: "unsure"}


def test_a_clash_flags_the_drug_and_the_allergy_it_rests_on():
    # Turning the allergy down would make the warning disappear, so it is decided on its own too.
    drug, allergy, other = _entry(), _entry(EntryKind.ALLERGY), _entry(EntryKind.ALLERGY)
    finding = Discrepancy(check="allergy_clash", message="m", entry_ids=[drug.id, allergy.id])
    assert flagged([drug, allergy, other], [finding]) == {drug.id: "clash", allergy.id: "clash"}


def test_what_the_patient_decided_is_not_flagged():
    confirmed = _entry(status=EntryStatus.CONFIRMED, checks=[EntryCheck.NOT_ENGLISH])
    rejected = _entry(status=EntryStatus.REJECTED, checks=[EntryCheck.NOT_ENGLISH])
    asked = _entry(EntryKind.NOTE, status=EntryStatus.CONFIRMED, added_as_read=True,
                   checks=[EntryCheck.NOT_ENGLISH])
    assert flagged([confirmed, rejected], []) == {}
    assert flagged([asked], []) == {asked.id: "check"}  # read as it is and not yet corrected
    asked.apply_edit("p1", label="fixed")
    assert flagged([asked], []) == {}  # the patient looked at it


# ---- the bulk route --------------------------------------------------------------------------
@pytest.fixture
def ctx(monkeypatch):
    s = Storage()
    patient = Patient(name="Meera Iyer (synthetic)")
    s.save_patient(patient)
    diagnosis = Entry(kind=EntryKind.DIAGNOSIS, label="Acute bronchitis", confidence=0.95)
    allergy = Entry(kind=EntryKind.ALLERGY, label="Penicillin", confidence=0.95)
    drug = Entry(kind=EntryKind.MEDICATION, label="Septran DS", confidence=0.95)
    vague = Entry(kind=EntryKind.MEDICATION, label="BP tablets", confidence=0.5)
    test = Entry(kind=EntryKind.TEST, label="CBC", confidence=0.95)  # a legacy suggestion
    clash = Discrepancy(check="allergy_clash", message="m", severity="urgent",
                        entry_ids=[drug.id, allergy.id])
    enc = Encounter(patient_id=patient.id, status=EncounterStatus.READY, discrepancies=[clash],
                    entries=[diagnosis, allergy, drug, vague, test])
    s.save_encounter(enc)
    warmed: list[str] = []
    monkeypatch.setattr("app.api.routes.prewarm", lambda label, *a, **k: warmed.append(label))
    app = create_app(s, PageAI([]))
    yield api_client(app, person=patient.id), app, enc, (diagnosis, allergy, drug, vague, test), warmed
    s.close()


def _review(client, enc, action, *entries):
    return client.post(f"/encounters/{enc.id}/review",
                       json={"action": action, "entry_ids": [e.id for e in entries]})


def test_the_review_screen_is_told_which_rows_need_a_look(ctx):
    client, _, enc, (_, allergy, drug, vague, _), _ = ctx
    view = client.get(f"/encounters/{enc.id}").json()
    assert view["flagged"] == {allergy.id: "clash", drug.id: "clash", vague.id: "unsure"}


def test_confirm_all_confirms_the_rest_and_leaves_what_needs_a_look(ctx):
    client, _, enc, (diagnosis, allergy, drug, vague, _), _ = ctx
    r = _review(client, enc, "confirm", diagnosis, allergy, drug, vague)
    assert r.status_code == 200
    got = r.json()
    assert [e["label"] for e in got["entries"]] == ["Acute bronchitis"]
    assert all(e["status"] == "confirmed" and e["status_history"][-1]["by"] == enc.patient_id
               for e in got["entries"])
    assert got["skipped"] == [{"id": allergy.id, "reason": "clash"},
                              {"id": drug.id, "reason": "clash"}, {"id": vague.id, "reason": "unsure"}]
    view = client.get(f"/encounters/{enc.id}").json()
    status = {e["label"]: e["status"] for e in view["entries"]}
    assert status["Septran DS"] == status["BP tablets"] == status["Penicillin"] == "proposed"


def test_confirming_runs_what_a_single_confirm_runs(ctx):
    client, _, enc, _, warmed = ctx
    ok = Entry(kind=EntryKind.MEDICATION, label="Dolo 650", confidence=0.95)
    enc2 = Encounter(patient_id=enc.patient_id, status=EncounterStatus.READY, entries=[ok])
    client.app.state.storage.save_encounter(enc2)
    assert _review(client, enc2, "confirm", ok).status_code == 200
    assert warmed == ["Dolo 650"]  # its summary is fetched in the background, as on a single tap


def test_dont_add_any_turns_down_even_a_flagged_row(ctx):
    client, _, enc, (diagnosis, _, drug, vague, _), _ = ctx
    r = _review(client, enc, "reject", drug, vague, diagnosis)
    assert [e["status"] for e in r.json()["entries"]] == ["rejected"] * 3 and r.json()["skipped"] == []
    # And "Add it after all" works as for any rejected row.
    assert client.patch(f"/entries/{drug.id}", json={"action": "confirm"}).json()["status"] == "confirmed"


def test_a_test_turned_down_in_a_box_takes_its_values_along(ctx):
    client, _, enc, (_, _, _, _, test), _ = ctx
    from app.schemas.record import TestResult
    value = Entry(kind=EntryKind.RESULT, label="Hemoglobin", status=EntryStatus.CONFIRMED,
                  added_as_read=True, links=[test.id], result=TestResult(value=13.2))
    client.app.state.storage.save_entry(value.model_copy(update={
        "patient_id": enc.patient_id, "encounter_id": enc.id}))
    assert _review(client, enc, "reject", test).status_code == 200
    got = {e["label"]: e["status"] for e in client.get(f"/encounters/{enc.id}").json()["entries"]}
    assert got["Hemoglobin"] == "rejected"


def test_only_waiting_entries_of_this_visit_are_touched(ctx):
    client, _, enc, (diagnosis, *_), _ = ctx
    assert _review(client, enc, "confirm", diagnosis).status_code == 200
    again = _review(client, enc, "confirm", diagnosis).json()  # already confirmed
    assert again["entries"] == [] and again["skipped"] == [{"id": diagnosis.id, "reason": "not_waiting"}]
    stranger = Entry(kind=EntryKind.DIAGNOSIS, label="Elsewhere")
    again = client.post(f"/encounters/{enc.id}/review",
                        json={"action": "confirm", "entry_ids": [stranger.id]}).json()
    assert again["skipped"] == [{"id": stranger.id, "reason": "not_waiting"}]


def test_the_route_refuses_bad_requests(ctx):
    client, app, enc, (diagnosis, *_), _ = ctx
    assert client.post(f"/encounters/{enc.id}/review", json={"action": "confirm", "entry_ids": []}).status_code == 422
    assert client.post(f"/encounters/{enc.id}/review", json={"action": "edit", "entry_ids": ["x"]}).status_code == 422
    assert api_client(app, person="someone-else").post(
        f"/encounters/{enc.id}/review", json={"action": "confirm", "entry_ids": [diagnosis.id]}
    ).status_code == 404
    db = app.state.storage
    busy = db.get_encounter(enc.id)
    busy.status = EncounterStatus.PROCESSING
    db.save_encounter(busy)
    assert _review(client, enc, "confirm", diagnosis).status_code == 409
