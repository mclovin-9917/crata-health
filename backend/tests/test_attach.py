"""Smart attach: a new visit of reports may be the report for tests waiting on an earlier visit.
The report is matched to a visit, then its tests are paired with that visit's waiting tests. Code
builds the candidates and checks the model's answer; the patient accepts or keeps the visit.
"""

import json
from datetime import timedelta

import pytest

from app.ai.client import AIError
from app.main import create_app
from app.pipeline import attach
from app.pipeline.attach import AttachChoice, AttachPairChoice
from app.pipeline.bills import SortedDocument
from app.pipeline.run import REPLACED_NOTE, Media, local_today, process_encounter
from app.schemas.patient import Patient
from app.schemas.record import (
    AttachPair,
    AttachSuggestion,
    Discrepancy,
    DocumentKind,
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    HospitalStay,
    Provenance,
    SourceItem,
    SourceType,
    StatusChange,
    TestResult,
)
from app.storage import Changed, Storage
from app.storage import sqlite as storage_sqlite
from tests.conftest import api_client
from tests.test_pipeline import _item
from tests.test_results import REPORT, VALUES, ReportAI

TODAY = local_today()
PID = "p1"
LAB = [("Complete blood count (CBC)", ["Hemoglobin", "Platelets"]),
       ("Glycosylated haemoglobin", ["HbA1c"]), ("Thyroid profile", ["TSH"]),
       ("Urine routine", ["Colour"])]


@pytest.fixture
def db():
    s = Storage()
    s.save_patient(Patient(id=PID, name="Asha (synthetic)"))
    s.save_patient(Patient(id="p2", name="Ravi (synthetic)"))
    yield s
    s.close()


def _test(label="CBC", status=EntryStatus.CONFIRMED) -> Entry:
    return Entry(kind=EntryKind.TEST, label=label, status=status, added_as_read=True,
                 status_history=[StatusChange(status=status, by="system", note="Read as it is")])


def _earlier(db, *, days=30, labels=("CBC",), patient=PID, title="Fever and cough",
             title_source="ai", **kw):
    """A visit that ordered tests and has no report for them yet."""
    tests = [_test(label) for label in labels]
    status = kw.pop("status", EncounterStatus.READY)
    enc = Encounter(patient_id=patient, status=status, title=title, title_source=title_source,
                    visit_date=TODAY - timedelta(days=days), entries=list(tests),
                    sources=[SourceItem(source_type=SourceType.TEXT, text="ordered tests")], **kw)
    db.save_encounter(enc)
    return db.get_encounter(enc.id), tests


def _value(source: SourceItem, label="Hemoglobin", links=(), status=EntryStatus.CONFIRMED) -> Entry:
    return Entry(
        kind=EntryKind.RESULT, label=label, status=status, added_as_read=True,
        status_history=[StatusChange(status=status, by="system", note="Read from the report")],
        provenance=[Provenance(source_id=source.id, source_type=SourceType.IMAGE,
                               quote=f"{label} 13.2 g/dL")],
        result=TestResult(value=13.2, unit="g/dL", measured_on=TODAY), links=list(links),
    )


def _report(db, *, tests=(("CBC", ["Hemoglobin", "Platelets"]),), kind=DocumentKind.REPORT,
            days=0, patient=PID, extra=(), source=None, **kw):
    """A new visit made of one report: its own copies of the tests, each with its values."""
    src = source or SourceItem(source_type=SourceType.IMAGE, filename="report.jpg",
                               document_kind=kind, text=REPORT)
    entries = []
    for label, names in tests:
        test = _test(label)
        entries += [test, *(_value(src, n, [test.id]) for n in names)]
    enc = Encounter(patient_id=patient, status=EncounterStatus.READY,
                    visit_date=TODAY - timedelta(days=days), entries=[*entries, *extra],
                    sources=[src], **kw)
    db.save_encounter(enc)
    return db.get_encounter(enc.id)


def _ids(enc, label):
    return next(e.id for e in enc.entries if e.kind == EntryKind.TEST and e.label == label)


def _pairs(new, earlier, *matches):
    """Suggestion pairs from (new label, waiting label) pairs."""
    return [AttachPair(new_test_id=_ids(new, a), waiting_test_id=_ids(earlier, b))
            for a, b in matches]


# ---- when it looks at all ------------------------------------------------------------------
def test_a_new_visit_of_reports_gets_the_visits_with_waiting_tests_as_candidates(db):
    earlier, (cbc, hba1c) = _earlier(db, labels=("CBC", "HbA1c"))
    (found,) = attach.candidates(db, _report(db))
    assert found.number == 1 and found.visit.id == earlier.id
    assert [t.id for t in found.tests] == [cbc.id, hba1c.id]


@pytest.mark.parametrize("kind", [DocumentKind.REPORT, DocumentKind.IMAGING])
def test_reports_and_scans_count(db, kind):
    _earlier(db)
    assert len(attach.candidates(db, _report(db, kind=kind))) == 1


@pytest.mark.parametrize("kind", [DocumentKind.PRESCRIPTION, DocumentKind.BILL,
                                  DocumentKind.OTHER, None])
def test_a_prescription_a_bill_or_an_unsorted_document_means_no_candidates(db, kind):
    _earlier(db)
    assert attach.candidates(db, _report(db, kind=kind)) == []


def test_a_recording_or_typed_notes_mean_no_candidates(db):
    _earlier(db)
    for source in (SourceItem(source_type=SourceType.AUDIO, filename="c.wav"),
                   SourceItem(source_type=SourceType.TEXT, text="my notes")):
        new = _report(db)
        new.sources.append(source)
        assert attach.candidates(db, new) == []


def test_every_page_of_a_pdf_must_be_a_report(db):
    _earlier(db)
    new = _report(db)
    new.sources[0].page_texts = ["p1", "p2"]
    new.sources[0].page_kinds = [DocumentKind.REPORT, DocumentKind.BILL]
    assert attach.candidates(db, new) == []
    new.sources[0].page_kinds = [DocumentKind.REPORT, DocumentKind.IMAGING]
    assert len(attach.candidates(db, new)) == 1


def test_a_report_without_values_has_nothing_to_move(db):
    _earlier(db)
    new = _report(db)
    for e in new.entries:
        if e.kind == EntryKind.RESULT:
            e.status = EntryStatus.REJECTED  # the patient removed them
    assert attach.candidates(db, new) == []


def test_a_stay_is_never_the_new_visit(db):
    _earlier(db)
    stay = HospitalStay(kind="admission", started_on=TODAY, entry_id="x")
    assert attach.candidates(db, _report(db, stay=stay)) == []


# ---- which visits are candidates -------------------------------------------------------------
def test_the_window_is_120_days_before_the_report_and_not_after_it(db):
    _earlier(db, days=120, labels=("HbA1c",), title="A")
    _earlier(db, days=121, labels=("TSH",), title="B")
    _earlier(db, days=-1, labels=("Lipid profile",), title="C")  # a day after the report
    _earlier(db, days=0, labels=("Urine routine",), title="D")  # the same day counts
    got = attach.candidates(db, _report(db))
    assert [c.tests[0].label for c in got] == ["Urine routine", "HbA1c"]
    # The window moves with the report's date.
    old = _report(db, days=10)
    assert {c.tests[0].label for c in attach.candidates(db, old)} == {"HbA1c", "TSH"}


def test_only_tests_without_confirmed_values_count_and_a_visit_with_none_is_left_out(db):
    done, (test,) = _earlier(db, labels=("HbA1c",))
    done.entries.append(_value(done.sources[0], "HbA1c", [test.id]))
    db.save_encounter(done)
    mixed, (valued, waiting) = _earlier(db, labels=("Lipid profile", "TSH"))
    mixed.entries.append(_value(mixed.sources[0], "LDL", [valued.id]))
    db.save_encounter(mixed)
    removed, (removed_test,) = _earlier(db, labels=("Urine routine",))
    removed.entries.append(_value(removed.sources[0], "Colour", [removed_test.id],
                                  status=EntryStatus.REJECTED))
    db.save_encounter(removed)
    got = attach.candidates(db, _report(db))
    # The first visit has no waiting test at all; in the second only TSH waits; a removed value
    # does not count.
    assert {c.visit.id: [t.label for t in c.tests] for c in got} == {
        mixed.id: ["TSH"], removed.id: ["Urine routine"]}
    assert waiting.id in {t.id for c in got for t in c.tests}


def test_a_test_the_patient_has_not_confirmed_or_turned_down_is_not_waiting(db):
    _earlier(db)
    for status in (EntryStatus.PROPOSED, EntryStatus.REJECTED):
        _, (test,) = _earlier(db, labels=("TSH",))
        test.status = status
        db.save_entry(test)
    assert [c.tests[0].label for c in attach.candidates(db, _report(db))] == ["CBC"]


def test_stay_and_unread_visits_are_not_candidates(db):
    _earlier(db, labels=("CBC",))
    _earlier(db, labels=("C",), stay=HospitalStay(kind="admission", started_on=TODAY, entry_id="x"))
    _earlier(db, labels=("D",), status=EncounterStatus.PROCESSING)
    _earlier(db, labels=("E",), status=EncounterStatus.FAILED)
    assert [c.tests[0].label for c in attach.candidates(db, _report(db))] == ["CBC"]


def test_another_patients_tests_are_never_candidates(db):
    _earlier(db, patient="p2")
    assert attach.candidates(db, _report(db)) == []


def test_the_nearest_visits_come_first_and_are_numbered(db):
    for days in (50, 5, 20):
        _earlier(db, days=days, labels=(f"Test {days}",))
    got = attach.candidates(db, _report(db))
    assert [c.tests[0].label for c in got] == ["Test 5", "Test 20", "Test 50"]
    assert [c.number for c in got] == [1, 2, 3]


def test_only_the_nearest_ten_visits_are_offered(db):
    for i in range(12):
        _earlier(db, days=i + 1, labels=(f"Test {i}",))
    got = attach.candidates(db, _report(db))
    assert len(got) == attach.MAX_CANDIDATES and got[0].tests[0].label == "Test 0"


# ---- what the model sees and answers ---------------------------------------------------------
def _offer(db, labels=("CBC", "HbA1c", "TSH"), **kw):
    earlier, _ = _earlier(db, labels=labels, **kw)
    new = _report(db, tests=LAB)
    return earlier, new, attach.candidates(db, new)


def test_the_prompt_holds_names_and_dates_only(db):
    earlier, new, found = _offer(db)
    text = attach.prompt_text(new, found)
    assert "1. Complete blood count (CBC): Hemoglobin; Platelets" in text
    assert "4. Urine routine: Colour" in text
    assert f"Visit 1: on {earlier.visit_date.isoformat()} | Fever and cough" in text
    assert "  1.1 CBC\n  1.2 HbA1c\n  1.3 TSH" in text
    assert "Asha" not in text and "13.2" not in text and earlier.id not in text


def test_only_a_name_the_ai_gave_goes_to_the_model(db):
    _, new, found = _offer(db, title="My own name for it", title_source="patient")
    text = attach.prompt_text(new, found)
    assert "My own name" not in text and "| " not in text.split("Earlier visits")[1]


def _choice(visit, *pairs):
    return AttachChoice(visit=visit, pairs=[AttachPairChoice(new_test=n, waiting=w)
                                            for n, w in pairs])


def test_a_visit_or_pair_the_model_was_not_offered_is_dropped(db):
    _, new, found = _offer(db)
    for visit in (None, 0, 2, 99, -1):
        assert attach.accept(_choice(visit, (1, "1.1")), new, found) is None
    # No pair stands: a test of the report that does not exist, a waiting test that is not offered,
    # one from another visit, a malformed number.
    for pair in ((9, "1.1"), (1, "1.9"), (1, "2.1"), (1, "1.0"), (1, "x"), (1, "1"), (0, "1.1")):
        assert attach.accept(_choice(1, pair), new, found) is None, pair
    assert attach.accept(_choice(1), new, found) is None


def test_valid_pairs_stand_and_the_rest_are_dropped(db):
    earlier, new, found = _offer(db)
    got = attach.accept(_choice(1, (1, "1.1"), (2, "1.2"), (3, "2.1"), (9, "1.3"), (3, "1.3"),
                                (1, "1.3"), (4, "1.1")), new, found)
    assert got.encounter_id == earlier.id
    # The repeats of test 1 and of waiting test 1.1 are dropped, as are the bad numbers.
    assert got.pairs == _pairs(new, earlier, ("Complete blood count (CBC)", "CBC"),
                               ("Glycosylated haemoglobin", "HbA1c"), ("Thyroid profile", "TSH"))


def test_a_waiting_number_the_model_gives_as_a_number_still_reads(db):
    earlier, new, found = _offer(db)
    pair = AttachPairChoice(new_test=1, waiting=1.2)
    assert pair.waiting == "1.2"
    got = attach.accept(AttachChoice(visit=1, pairs=[pair]), new, found)
    assert got.pairs == _pairs(new, earlier, ("Complete blood count (CBC)", "HbA1c"))


def test_ask_sends_one_call_with_no_thinking_and_uses_the_answer(db):
    earlier, new, found = _offer(db)
    calls = []

    class AI:
        def reason_json(self, system, user, schema, **kw):
            calls.append((system, user, schema, kw))
            return _choice(1, (1, "1.1"))

    got = attach.ask(AI(), new, found)
    assert got.pairs == _pairs(new, earlier, ("Complete blood count (CBC)", "CBC"))
    ((system, _, schema, kw),) = calls
    assert system.startswith("Attach (v2, 2026-10-28)") and schema is AttachChoice
    assert kw["thinking"] is False and kw["temperature"] == 0.0


def test_a_pair_no_value_could_be_filed_under_is_dropped(db):
    _earlier(db, labels=("CBC",))
    src = SourceItem(source_type=SourceType.IMAGE, document_kind=DocumentKind.REPORT, text="x")
    bare, sugar = _test("Complete blood count"), _test("Blood sugar")
    new = Encounter(patient_id=PID, status=EncounterStatus.READY, visit_date=TODAY, sources=[src],
                    entries=[bare, sugar, _value(src, "Fasting", [sugar.id])])
    db.save_encounter(new)
    found = attach.candidates(db, db.get_encounter(new.id))
    assert attach.accept(_choice(1, (1, "1.1")), new, found) is None  # the CBC has no values


# ---- through the pipeline --------------------------------------------------------------------
class AttachAI(ReportAI):
    """Reads a report and answers the attach question as scripted."""

    def __init__(self, answer, results=VALUES):
        super().__init__([_item(kind="test", label="CBC", source_id="doc1", quote="CBC",
                                said_by="document")], results)
        self.docs = [SortedDocument(source_id="doc1", kind="report")]
        self.answer, self.asked = answer, []

    def reason_json(self, system, user, schema, **kw):
        if schema is AttachChoice:
            self.asked.append(user)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer
        return super().reason_json(system, user, schema, **kw)


def _upload(db, ai):
    enc = Encounter(patient_id=PID,
                    sources=[SourceItem(source_type=SourceType.IMAGE, filename="r.jpg")])
    db.save_encounter(enc)
    process_encounter(db, ai, enc.id, [Media(enc.sources[0].id, "image", b"img", "image/jpeg")])
    return db.get_encounter(enc.id)


def test_a_read_report_with_waiting_tests_gets_a_suggestion(db):
    earlier, (cbc,) = _earlier(db)
    ai = AttachAI(_choice(1, (1, "1.1")))
    done = _upload(db, ai)
    assert done.status == EncounterStatus.READY and done.step is None
    s = done.attach_suggestion
    assert s.encounter_id == earlier.id
    assert [(p.waiting_test_id) for p in s.pairs] == [cbc.id]
    assert s.pairs[0].new_test_id == _ids(done, "CBC")
    assert "matching_report" in done.step_seconds
    assert len(ai.asked) == 1 and "Visit 1: on" in ai.asked[0]
    # Nothing moved yet: the patient decides.
    assert len(db.get_encounter(earlier.id).entries) == 1


def test_no_candidates_means_no_model_call_and_no_change(db):
    ai = AttachAI(_choice(1, (1, "1.1")))
    done = _upload(db, ai)
    assert done.attach_suggestion is None and ai.asked == []
    assert "matching_report" not in done.step_seconds


def test_a_failing_or_useless_answer_leaves_the_visit_as_its_own(db):
    _earlier(db)
    done = _upload(db, AttachAI(AIError("endpoint busy")))
    assert done.status == EncounterStatus.READY and done.attach_suggestion is None
    done = _upload(db, AttachAI(_choice(9, (1, "9.1"))))
    assert done.status == EncounterStatus.READY and done.attach_suggestion is None
    done = _upload(db, AttachAI(AttachChoice(visit=None)))
    assert done.attach_suggestion is None


def test_a_visit_with_a_prescription_is_not_asked_about(db):
    _earlier(db)
    ai = AttachAI(_choice(1, (1, "1.1")))
    ai.docs = [SortedDocument(source_id="doc1", kind="prescription")]
    assert _upload(db, ai).attach_suggestion is None and ai.asked == []


def test_a_report_added_to_a_test_is_not_asked_about(db):
    _earlier(db)
    ai = AttachAI(_choice(1, (1, "1.1")))
    other, (tsh,) = _earlier(db, labels=("TSH",), days=3)
    other.sources.append(SourceItem(source_type=SourceType.IMAGE, filename="r.jpg"))
    db.save_encounter(other)
    process_encounter(db, ai, other.id,
                      [Media(other.sources[-1].id, "image", b"img", "image/jpeg")],
                      adding=True, report_for=tsh.id)
    assert db.get_encounter(other.id).attach_suggestion is None and ai.asked == []


def test_old_rows_without_the_field_still_load(db):
    enc, _ = _earlier(db)
    row = db._conn.execute("SELECT data FROM encounters WHERE id = ?", (enc.id,)).fetchone()[0]
    assert "attach_suggestion" in row
    stripped = Encounter.model_validate_json(row).model_dump(
        exclude={"attach_suggestion", "entries"}, mode="json")
    db._conn.execute("UPDATE encounters SET data = ? WHERE id = ?", (json.dumps(stripped), enc.id))
    loaded = db.get_encounter(enc.id)
    assert loaded.attach_suggestion is None and len(loaded.entries) == 1
    assert loaded.sources[0].attached_to is None


# ---- the route -------------------------------------------------------------------------------
@pytest.fixture
def ctx(db):
    """An earlier visit that ordered CBC, HbA1c and TSH, and a new lab report visit (those three
    tests under other names, plus a urine test the earlier visit did not order) that points at it."""
    earlier, _ = _earlier(db, labels=("CBC", "HbA1c", "TSH"), title="Tiredness and thirst")
    note = Entry(kind=EntryKind.FOLLOW_UP, label="Repeat in 3 months", status=EntryStatus.CONFIRMED,
                 added_as_read=True)
    new = _report(db, tests=LAB, extra=[note])
    new.attach_suggestion = AttachSuggestion(encounter_id=earlier.id, pairs=_pairs(
        new, earlier, ("Complete blood count (CBC)", "CBC"), ("Glycosylated haemoglobin", "HbA1c"),
        ("Thyroid profile", "TSH")))
    db.save_encounter(new)
    return api_client(create_app(db), person=PID), db, earlier, new, note


def _body(new):
    s = new.attach_suggestion
    return {"encounter_id": s.encounter_id, "test_entry_ids": [p.waiting_test_id for p in s.pairs]}


def test_the_visit_page_shows_the_suggestion_with_the_waiting_tests_and_visit(ctx):
    client, _, earlier, new, _ = ctx
    got = client.get(f"/encounters/{new.id}").json()["attach_to"]
    assert got == {
        "encounter_id": earlier.id, "test_entry_ids": [p.waiting_test_id for p in
                                                       new.attach_suggestion.pairs],
        "test_labels": ["CBC", "HbA1c", "TSH"], "visit_date": earlier.visit_date.isoformat(),
        "visit_title": "Tiredness and thirst"}
    assert "reason" not in got  # the model's own words never reach the screen
    assert client.get(f"/encounters/{earlier.id}").json()["attach_to"] is None


def test_accepting_moves_the_values_tests_and_files_and_removes_the_empty_visit(ctx):
    client, db, earlier, new, note = ctx
    r = client.post(f"/encounters/{new.id}/attach", json=_body(new))
    assert r.status_code == 200 and r.json() == {"encounter_id": earlier.id}

    target = db.get_encounter(earlier.id)
    assert db.get_encounter(new.id) is None
    assert client.get(f"/encounters/{new.id}").status_code == 404
    moved = {e.id: e for e in target.entries}
    waiting = {e.label: e.id for e in target.entries
               if e.kind == EntryKind.TEST and e.label in ("CBC", "HbA1c", "TSH")}
    by_label = {e.label: e for e in new.entries}
    # Each paired test's values sit under its waiting test, with history and quotes.
    for value, test in (("Hemoglobin", "CBC"), ("Platelets", "CBC"), ("HbA1c", "HbA1c"),
                        ("TSH", "TSH")):
        got = moved[by_label[value].id]
        assert got.links == [waiting[test]] and got.encounter_id == earlier.id
        assert got.status_history == by_label[value].status_history
        assert got.added_as_read and got.status == EntryStatus.CONFIRMED
    # The paired new tests are gone; the unpaired one moved with its value; so did the follow-up.
    labels = [e.label for e in target.entries if e.kind == EntryKind.TEST]
    assert labels == ["CBC", "HbA1c", "TSH", "Urine routine"]
    urine = next(e for e in target.entries if e.label == "Urine routine")
    assert moved[by_label["Colour"].id].links == [urine.id]
    assert note.id in moved and moved[note.id].encounter_id == earlier.id
    for label in ("Complete blood count (CBC)", "Glycosylated haemoglobin", "Thyroid profile"):
        assert db.get_entry(by_label[label].id) is None  # the dropped copies are not left behind
    # Every quote still has its source, marked as attached; the earlier visit keeps its date/name.
    ids = {s.id for s in target.sources}
    assert new.sources[0].id in ids
    assert all(p.source_id in ids for e in target.entries for p in e.provenance
               if p.source_type == SourceType.IMAGE)
    attached = next(s for s in target.sources if s.id == new.sources[0].id)
    assert attached.attached_to == waiting["CBC"] and attached.report_for is None
    assert target.visit_date == earlier.visit_date and target.title == "Tiredness and thirst"
    assert len(db.list_encounters(PID)) == 1


def test_a_source_marked_for_a_dropped_test_is_marked_for_its_waiting_test(db):
    earlier, _ = _earlier(db, labels=("CBC",))
    src = SourceItem(source_type=SourceType.IMAGE, document_kind=DocumentKind.REPORT, text=REPORT)
    new = _report(db, source=src)
    new.sources[0].report_for = _ids(new, "CBC")  # an "Add report" file of the new visit's test
    new.attach_suggestion = AttachSuggestion(encounter_id=earlier.id,
                                             pairs=_pairs(new, earlier, ("CBC", "CBC")))
    db.save_encounter(new)
    client = api_client(create_app(db), person=PID)
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 200
    target = db.get_encounter(earlier.id)
    (source,) = [s for s in target.sources if s.id == src.id]
    assert source.report_for == _ids(target, "CBC")


def test_a_clash_on_a_medicine_read_off_the_report_moves_with_it_and_still_stops_a_bulk_confirm(db):
    earlier, _ = _earlier(db, labels=("CBC",))
    allergy = Entry(kind=EntryKind.ALLERGY, label="Penicillin", status=EntryStatus.CONFIRMED)
    earlier.entries.append(allergy)
    db.save_encounter(earlier)
    medicine = Entry(kind=EntryKind.MEDICATION, label="Amoxicillin 500 mg",
                     status=EntryStatus.PROPOSED)
    new = _report(db, extra=[medicine])
    clash = Discrepancy(check="allergy_clash", message="Amoxicillin may clash with penicillin",
                        entry_ids=[medicine.id, allergy.id], severity="urgent")
    new.discrepancies = [clash]
    new.attach_suggestion = AttachSuggestion(encounter_id=earlier.id,
                                             pairs=_pairs(new, earlier, ("CBC", "CBC")))
    db.save_encounter(new)
    client = api_client(create_app(db), person=PID)
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 200
    got = client.get(f"/encounters/{earlier.id}").json()
    assert [d["id"] for d in got["discrepancies"]] == [clash.id]
    assert got["flagged"][medicine.id] == "clash"
    res = client.post(f"/encounters/{earlier.id}/review",
                      json={"action": "confirm", "entry_ids": [medicine.id]}).json()
    assert res["entries"] == [] and res["skipped"] == [{"id": medicine.id, "reason": "clash"}]


def test_a_careful_reread_of_the_target_replaces_only_the_values_of_an_attached_file(ctx):
    client, db, earlier, new, note = ctx
    src = new.sources[0]
    proof = [Provenance(source_id=src.id, source_type=SourceType.IMAGE, quote="CBC")]
    next(e for e in new.entries if e.id == note.id).provenance = proof
    diagnosis = Entry(kind=EntryKind.DIAGNOSIS, label="Anaemia", status=EntryStatus.PROPOSED,
                      provenance=proof)
    new.entries.append(diagnosis)
    db.save_encounter(new)
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 200
    old_values = [e for e in db.get_encounter(earlier.id).entries if e.kind == EntryKind.RESULT]

    ai = AttachAI(None)
    process_encounter(db, ai, earlier.id, [Media(src.id, "image", b"img", "image/jpeg")],
                      careful=True)
    done = db.get_encounter(earlier.id)
    assert done.status == EncounterStatus.READY and done.error is None
    got = {e.id: e for e in done.entries}
    # The follow-up, the unpaired test and the suggestion from the file are left alone.
    assert got[note.id].status == EntryStatus.CONFIRMED
    assert got[diagnosis.id].status == EntryStatus.PROPOSED
    assert got[_ids(done, "Urine routine")].status == EntryStatus.CONFIRMED
    # Its untouched values were replaced, by values filed under the earlier visit's tests.
    assert all(got[v.id].status == EntryStatus.REJECTED
               and got[v.id].status_history[-1].note == REPLACED_NOTE for v in old_values)
    old_ids = {v.id for v in old_values}
    fresh = [e for e in done.entries if e.kind == EntryKind.RESULT and e.id not in old_ids]
    cbc = _ids(done, "CBC")
    assert fresh and all(e.links == [cbc] for e in fresh)
    # The visit's date and name did not change, and no copy of the report's tests came back.
    assert done.visit_date == earlier.visit_date and done.title == "Tiredness and thirst"
    assert [e.label for e in done.entries if e.kind == EntryKind.TEST] == [
        "CBC", "HbA1c", "TSH", "Urine routine"]
def ctx_pairs(new, earlier):
    return _pairs(new, earlier, ("Complete blood count (CBC)", "CBC"),
                  ("Glycosylated haemoglobin", "HbA1c"), ("Thyroid profile", "TSH"))


def test_a_visit_that_is_not_the_patients_answers_404(ctx):
    _, db, earlier, new, _ = ctx
    other = api_client(create_app(db), person="p2")
    assert other.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 404
    assert other.delete(f"/encounters/{new.id}/attach").status_code == 404
    assert db.get_encounter(new.id) is not None  # nothing changed
    assert len(db.get_encounter(earlier.id).entries) == 3


def test_a_stale_suggestion_answers_409_and_changes_nothing(ctx):
    client, db, earlier, new, _ = ctx

    def untouched():
        assert db.get_encounter(new.id) is not None
        assert len(db.get_encounter(earlier.id).entries) == 3

    body = _body(new)
    # What the screen showed is not what is stored.
    assert client.post(f"/encounters/{new.id}/attach",
                       json={**body, "test_entry_ids": body["test_entry_ids"][:2]}).status_code == 409
    assert client.post(f"/encounters/{new.id}/attach",
                       json={**body, "encounter_id": "other"}).status_code == 409
    untouched()
    # One of the tests got values meanwhile (an "Add report" on it): every pair is checked again.
    target = db.get_encounter(earlier.id)
    tsh = _ids(target, "TSH")
    target.entries.append(_value(target.sources[0], "TSH", [tsh]))
    db.save_encounter(target)
    assert client.post(f"/encounters/{new.id}/attach", json=body).status_code == 409
    assert client.get(f"/encounters/{new.id}").json()["attach_to"] is None  # the card is gone too
    untouched_count = len(db.get_encounter(earlier.id).entries)
    assert untouched_count == 4 and db.get_encounter(new.id) is not None


def test_a_suggestion_whose_visit_was_deleted_answers_409(ctx):
    client, db, earlier, new, _ = ctx
    db.delete_encounter(earlier.id)
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 409
    assert db.get_encounter(new.id) is not None


def test_a_test_the_patient_removed_answers_409(ctx):
    client, _, _, new, _ = ctx
    client.patch(f"/entries/{new.attach_suggestion.pairs[0].waiting_test_id}",
                 json={"action": "reject"})
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 409


def test_a_new_test_the_patient_removed_answers_409(ctx):
    client, _, _, new, _ = ctx
    client.patch(f"/entries/{new.attach_suggestion.pairs[1].new_test_id}",
                 json={"action": "reject"})
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 409


def test_a_visit_without_a_suggestion_answers_409(ctx):
    client, _, _, new, _ = ctx
    body = _body(new)
    assert client.delete(f"/encounters/{new.id}/attach").status_code == 204
    assert client.post(f"/encounters/{new.id}/attach", json=body).status_code == 409


def test_a_visit_being_read_answers_409(ctx):
    client, db, _, new, _ = ctx
    new.status = EncounterStatus.PROCESSING
    db.save_encounter(new)
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 409
    assert client.delete(f"/encounters/{new.id}/attach").status_code == 409


def test_a_target_being_read_again_answers_409(ctx):
    client, db, earlier, new, _ = ctx
    target = db.get_encounter(earlier.id)
    target.status = EncounterStatus.PROCESSING
    db.save_encounter(target)
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 409
    assert db.get_encounter(new.id) is not None


def test_a_visit_that_changes_while_the_attach_is_worked_out_answers_409(ctx, monkeypatch):
    client, db, earlier, new, _ = ctx
    real = attach.merge

    def merge_then_change(source, target, pairs):
        done = real(source, target, pairs)
        # Meanwhile the target is read again (or an entry confirmed): the picture is stale.
        other = db.get_encounter(earlier.id)
        other.status = EncounterStatus.PROCESSING
        db.save_encounter(other)
        return done

    monkeypatch.setattr(attach, "merge", merge_then_change)
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 409
    monkeypatch.setattr(attach, "merge", real)
    assert db.get_encounter(new.id) is not None and len(db.get_encounter(new.id).entries) > 0
    assert len(db.get_encounter(earlier.id).entries) == 3  # nothing moved


def test_a_visit_confirmed_meanwhile_is_not_overwritten(ctx, monkeypatch):
    client, db, _, new, note = ctx
    real = attach.merge

    def merge_then_change(source, target, pairs):
        done = real(source, target, pairs)
        changed = db.get_entry(note.id)  # the patient edits the new visit's follow-up meanwhile
        changed.apply_edit(PID, label="Repeat in 6 months")
        db.save_entry(changed)
        return done

    monkeypatch.setattr(attach, "merge", merge_then_change)
    assert client.post(f"/encounters/{new.id}/attach", json=_body(new)).status_code == 409
    assert db.get_entry(note.id).label == "Repeat in 6 months"


def test_keeping_the_visit_clears_the_suggestion_and_nothing_else(ctx):
    client, db, earlier, new, _ = ctx
    assert client.delete(f"/encounters/{new.id}/attach").status_code == 204
    kept = db.get_encounter(new.id)
    assert kept.attach_suggestion is None and len(kept.entries) == len(new.entries)
    assert client.get(f"/encounters/{new.id}").json()["attach_to"] is None
    assert client.delete(f"/encounters/{new.id}/attach").status_code == 204  # again: no error
    assert len(db.get_encounter(earlier.id).entries) == 3


def test_nothing_is_lost_when_the_save_fails_halfway(ctx, monkeypatch):
    _, db, earlier, new, _ = ctx
    real, calls = storage_sqlite._upsert_entry, []

    def failing(conn, e):
        calls.append(e.id)
        if len(calls) == 3:
            raise RuntimeError("disk full")
        real(conn, e)

    target, source = db.get_encounter(earlier.id), db.get_encounter(new.id)
    found = attach.target_of(db, source)
    assert attach.merge(source, target, found[1])
    monkeypatch.setattr(storage_sqlite, "_upsert_entry", failing)
    with pytest.raises(RuntimeError):
        db.merge_encounter(target, new.id)
    monkeypatch.setattr(storage_sqlite, "_upsert_entry", real)
    # Both visits are exactly as they were.
    assert db.get_encounter(new.id) is not None
    assert len(db.get_encounter(new.id).entries) == len(new.entries)
    assert len(db.get_encounter(earlier.id).entries) == 3
    assert {e.encounter_id for e in db.get_encounter(new.id).entries} == {new.id}


def test_a_changed_visit_raises_before_anything_is_written(ctx):
    _, db, earlier, new, _ = ctx
    target, source = db.get_encounter(earlier.id), db.get_encounter(new.id)
    before = [target.model_copy(deep=True), source.model_copy(deep=True)]
    found = attach.target_of(db, source)
    assert attach.merge(source, target, found[1])
    db.merge_encounter(db.get_encounter(earlier.id), "nothing", expect=before)  # as stored: fine
    assert db.get_encounter(new.id) is not None
    changed = db.get_entry(before[0].entries[0].id)
    changed.apply_edit(PID, label="Complete blood count")
    db.save_entry(changed)
    with pytest.raises(Changed):
        db.merge_encounter(target, new.id, expect=before)
    assert db.get_encounter(new.id) is not None


def test_after_an_attach_the_tests_have_values_so_they_are_no_longer_waiting(ctx):
    client, db, _, new, _ = ctx
    client.post(f"/encounters/{new.id}/attach", json=_body(new))
    assert attach.candidates(db, _report(db)) == []  # their values are there now
