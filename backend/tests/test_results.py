"""Test values read from reports and prescriptions: the checks in code, how they enter the record
(read as they are, no confirm tap), "Add report" on a test, and that they never reach the card."""

from datetime import date, timedelta

import pytest

from app.main import create_app
from app.pipeline.extract import ExtractedResult, Extraction
from app.pipeline.results import (
    READ_NOTE,
    check_result,
    numbers_in,
    printed,
    printed_date,
    to_results,
)
from app.pipeline.run import Media, local_today, process_encounter
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    SourceItem,
    SourceType,
    TestResult,
)
from app.storage import Storage
from tests.conftest import api_client
from tests.test_pipeline import FakeAI, _item

TODAY = date.today()
REPORT = (
    "City Lab - Report\n"
    f"Collected on {TODAY.strftime('%d/%m/%Y')}\n"
    "CBC\n"
    "Hemoglobin 13.2 g/dL (13.0 - 17.0)\n"
    "Total leucocyte count 14,200 /cu mm (4,000 - 11,000)\n"
    "HbA1c 6.9 % (4.0 - 5.6)\n"
    "Urine sugar: Nil\n"
    "BP: 138/88 mmHg"
)


def _source(text=REPORT, kind=SourceType.IMAGE) -> SourceItem:
    return SourceItem(source_type=kind, text=text)


def _result(**kw) -> ExtractedResult:
    base = {"source_id": "doc1", "name": "Hemoglobin", "quote": "Hemoglobin 13.2 g/dL (13.0 - 17.0)",
            "value": 13.2, "unit": "g/dL", "ref_low": 13.0, "ref_high": 17.0}
    return ExtractedResult(**{**base, **kw})


# ---- the checks ------------------------------------------------------------------------------
def test_numbers_are_read_as_numbers():
    assert numbers_in("TLC 14,200 /cu mm (4,000 - 11,000)") == [14200.0, 4000.0, 11000.0]
    assert numbers_in("Platelets 1,42,000.5") == [142000.5]
    assert numbers_in("Hb 13.2.") == [13.2]
    assert printed(13.2, "Hemoglobin 13.2 g/dL") and not printed(132, "Hemoglobin 13.2 g/dL")
    assert not printed(13.0, "Hemoglobin 13.25 g/dL") and not printed(None, "x 1")


def test_a_value_printed_in_its_row_is_kept_with_its_range_and_unit():
    got = check_result(_result(), _source())
    assert got == TestResult(value=13.2, unit="g/dL", ref_low=13.0, ref_high=17.0)


def test_a_number_that_is_not_printed_in_the_row_is_dropped():
    assert check_result(_result(value=13.9), _source()) is None
    assert check_result(_result(value=132), _source()) is None  # 13.2 is not 132


def test_the_name_must_be_in_the_row():
    assert check_result(_result(name="Platelets"), _source()) is None


def test_the_row_must_be_in_the_document():
    assert check_result(_result(quote="Hemoglobin 13.2 g/dL (12.0 - 16.0)"), _source()) is None
    assert check_result(_result(source_id="gone"), None) is None


def test_only_documents_make_values():
    spoken = _source("My sugar was 140 yesterday", SourceType.AUDIO)
    r = _result(name="sugar", quote="My sugar was 140", value=140, unit=None, ref_low=None,
                ref_high=None)
    assert check_result(r, spoken) is None
    assert check_result(r, _source("My sugar was 140 yesterday", SourceType.TEXT)) is None


def test_a_range_or_unit_that_is_not_printed_is_dropped_but_the_value_stays():
    got = check_result(_result(ref_low=12.0, unit="mg/dL"), _source())
    assert got == TestResult(value=13.2, ref_high=17.0)


def test_a_percent_unit_is_kept_only_if_printed():
    r = _result(name="HbA1c", quote="HbA1c 6.9 % (4.0 - 5.6)", value=6.9, unit="%",
                ref_low=4.0, ref_high=5.6)
    assert check_result(r, _source()).unit == "%"
    assert check_result(r.model_copy(update={"quote": "HbA1c 6.9 (4.0 - 5.6)"}),
                        _source("HbA1c 6.9 (4.0 - 5.6)")).unit is None


def test_a_result_in_words_is_kept_when_the_words_are_printed():
    r = _result(name="Urine sugar", quote="Urine sugar: Nil", value=None, value_text="Nil",
                unit=None, ref_low=None, ref_high=None)
    assert check_result(r, _source()) == TestResult(value_text="Nil")
    assert check_result(r.model_copy(update={"value_text": "Absent"}), _source()) is None


def test_words_are_dropped_but_a_printed_number_stays():
    got = check_result(_result(value_text="normal"), _source())
    assert got is not None and got.value == 13.2 and got.value_text is None


def test_null_and_number_values_in_fields_no_longer_fail_the_extraction():
    item = _item(kind="medication", label="Dolo 650", source_id="doc1", quote="Dolo",
                 fields={"doses_per_day": None, "duration_value": 3, "frequency": "once a day"})
    assert item.fields == {"duration_value": "3", "frequency": "once a day"}


@pytest.mark.parametrize("words, row", [
    ("Reactive", "HBsAg: Non-reactive"),
    ("Reactive", "HIV: not reactive"),
    ("Detected", "H. pylori: Not detected"),
    ("Growth", "Urine culture: No growth"),
    ("<0.5", "CRP >0.5 mg/L"),
])
def test_a_result_in_words_is_not_taken_out_of_a_negation_or_a_sign(words, row):
    r = _result(name=row.split(":")[0].split(" >")[0], quote=row, value=None, value_text=words,
                unit=None, ref_low=None, ref_high=None)
    assert check_result(r, _source(row)) is None


@pytest.mark.parametrize("words, row", [
    ("Non-reactive", "HBsAg: Non-reactive"), ("Not detected", "H. pylori: Not detected"),
    ("No growth", "Urine culture: No growth"), ("<0.5", "CRP <0.5 mg/L"),
])
def test_a_result_in_words_that_is_printed_as_typed_is_kept(words, row):
    r = _result(name=row.split(":")[0].split(" <")[0], quote=row, value=None, value_text=words,
                unit=None, ref_low=None, ref_high=None)
    assert check_result(r, _source(row)).value_text == words


def test_a_quote_over_two_rows_cannot_lend_one_rows_number_to_anothers_name():
    two = "Hemoglobin 13.2 g/dL (13.0 - 17.0)\nTotal leucocyte count 14,200 /cu mm"
    r = _result(quote=two, value=14200, unit=None, ref_low=None, ref_high=None)
    assert check_result(r, _source(two)) is None
    ok = _result(quote=two, value=13.2, unit="g/dL", ref_low=13.0, ref_high=17.0)
    assert check_result(ok, _source(two)).value == 13.2


def test_a_result_with_nothing_in_it_is_dropped():
    assert check_result(_result(value=None, unit=None, ref_low=None, ref_high=None), _source()) is None


def test_position_against_the_printed_range():
    assert TestResult(value=6.9, ref_low=4.0, ref_high=5.6).position() == "high"
    assert TestResult(value=3.0, ref_low=4.0, ref_high=5.6).position() == "low"
    assert TestResult(value=5.0, ref_low=4.0, ref_high=5.6).position() == "within"
    assert TestResult(value=5.0).position() is None
    assert TestResult(value_text="Nil", ref_high=1).position() is None


@pytest.mark.parametrize("text", [
    "Collected on 17/10/2026", "Collected 17-10-2026", "Date 17.10.2026", "Date: 2026-10-17",
    "Reported 17 Oct 2026", "Reported 17 October 2026", "Reported Oct 17, 2026",
])
def test_a_value_date_must_be_printed_in_the_document(text):
    assert printed_date("2026-10-17", text, TODAY) == date(2026, 10, 17)


def test_a_date_that_is_not_printed_or_not_real_is_ignored():
    assert printed_date("2026-10-17", "Collected 18/10/2026", TODAY) is None
    assert printed_date("2026-10-17", None, TODAY) is None
    assert printed_date("17/10/2026", "Collected 17/10/2026", TODAY) is None  # not ISO
    assert printed_date((TODAY + timedelta(days=2)).isoformat(),
                        (TODAY + timedelta(days=2)).strftime("%d/%m/%Y"), TODAY) is None  # future
    assert printed_date("1900-01-01", "01/01/1900", TODAY) is None
    assert printed_date("2026-10-01", "Collected 11/10/2026", TODAY) is None  # 1/10/2026 is not in it
    assert printed_date("2020-10-17", "Collected 17/10/2026", TODAY) is None  # 17/10/20 is not in it
    assert printed_date("2026-03-05", "Date 5/3/2026", TODAY) == date(2026, 3, 5)  # no padding


def test_each_value_is_dated_by_its_own_document():
    # Fixed dates: the shared REPORT prints today's date, which would make the 18th "printed".
    src = _source("Hemoglobin 13.2 g/dL (13.0 - 17.0)\nCollected on 17/10/2026")
    got, _ = to_results(
        Extraction(items=[], results=[_result(measured_on="2026-10-17"),
                                      _result(name="Hemoglobin", measured_on="2026-10-18"),
                                      _result(measured_on=None)]),
        {"doc1": src}, date(2026, 10, 16), [], today=date(2026, 10, 19))
    # The 17th is printed in the report (the 18th is not), so the others take the fallback.
    assert [e.result.measured_on for e in got] == [date(2026, 10, 17), date(2026, 10, 16),
                                                   date(2026, 10, 16)]


# ---- becoming entries ------------------------------------------------------------------------
def test_results_enter_as_read_without_a_confirm_tap():
    src = _source()
    cbc = Entry(kind=EntryKind.TEST, label="CBC")
    got, failed = to_results(
        Extraction(items=[], results=[_result(test="cbc"), _result(name="Platelets"),
                                      _result(name="BP", quote="BP: 138/88 mmHg", value=138,
                                              part="systolic", unit="mmHg", ref_low=None,
                                              ref_high=None)]),
        {"doc1": src}, TODAY, [cbc])
    assert [e.label for e in got] == ["Hemoglobin", "BP (systolic)"]
    assert failed == ["Platelets (value)"]
    first = got[0]
    assert first.kind == EntryKind.RESULT and first.status == EntryStatus.CONFIRMED
    assert first.added_as_read and first.said_by.value == "document"
    assert first.status_history[-1].by == "system" and first.status_history[-1].note == READ_NOTE
    assert first.result.measured_on == TODAY and first.provenance[0].quote.startswith("Hemoglobin")
    assert first.links == [cbc.id] and got[1].links == []  # vitals on a prescription stand alone


def test_a_value_with_no_matching_test_goes_to_the_one_the_patient_tapped():
    src = _source()
    cbc, tsh = Entry(kind=EntryKind.TEST, label="CBC"), Entry(kind=EntryKind.TEST, label="TSH")
    got, _ = to_results(
        Extraction(items=[], results=[_result(test="TSH"), _result(test="Lipid profile"),
                                      _result(test=None)]),
        {"doc1": src}, TODAY, [cbc, tsh], fallback_test=cbc)
    assert [e.links for e in got] == [[tsh.id], [cbc.id], [cbc.id]]


# ---- through the pipeline --------------------------------------------------------------------
class ReportAI(FakeAI):
    """Reads REPORT from every document and returns the scripted items and results."""

    def __init__(self, items, results):
        super().__init__(items)
        self.results = results

    def perceive(self, parts, instruction, **kw):
        self.calls.append(("perceive", parts[0]["type"], instruction))
        return REPORT

    def reason_json(self, system, user, schema, **kw):
        answer = super().reason_json(system, user, schema, **kw)
        if isinstance(answer, Extraction):
            self.instructions = system
            return answer.model_copy(update={"results": self.results})
        return answer


CBC_ITEM = _item(kind="test", label="CBC", source_id="doc1", quote="CBC", said_by="document")
VALUES = [
    _result(test="CBC"),
    _result(name="Total leucocyte count", quote="Total leucocyte count 14,200 /cu mm (4,000 - 11,000)",
            value=14200, unit="/cu mm", ref_low=4000, ref_high=11000, test="CBC"),
    _result(name="HbA1c", quote="HbA1c 6.9 % (4.0 - 5.6)", value=6.9, unit="%", ref_low=4.0,
            ref_high=5.6),
    _result(name="Urine sugar", quote="Urine sugar: Nil", value=None, value_text="Nil", unit=None,
            ref_low=None, ref_high=None),
    _result(name="Platelets", quote="Platelets 250 x10^3/uL", value=250),  # not on the report
]


def _visit(db, *, entries=()):
    enc = Encounter(patient_id="p1", status=EncounterStatus.READY, entries=list(entries),
                    visit_date=TODAY - timedelta(days=9),
                    sources=[SourceItem(source_type=SourceType.TEXT, text="ordered CBC")])
    db.save_encounter(enc)
    return enc


@pytest.fixture
def db():
    s = Storage()
    yield s
    s.close()


def test_a_report_uploaded_as_a_visit_keeps_its_values_under_the_test(db):
    enc = Encounter(patient_id="p1", sources=[SourceItem(source_type=SourceType.IMAGE, filename="r.jpg")])
    db.save_encounter(enc)
    media = [Media(enc.sources[0].id, "image", b"img", "image/jpeg")]
    process_encounter(db, ReportAI([CBC_ITEM], VALUES), enc.id, media)

    done = db.get_encounter(enc.id)
    (test,) = [e for e in done.entries if e.kind == EntryKind.TEST]
    values = [e for e in done.entries if e.kind == EntryKind.RESULT]
    assert test.status == EntryStatus.CONFIRMED and test.added_as_read  # a test is read as it is
    assert [v.label for v in values] == ["Hemoglobin", "Total leucocyte count", "HbA1c", "Urine sugar"]
    assert all(v.status == EntryStatus.CONFIRMED and v.added_as_read for v in values)
    assert [v.links for v in values] == [[test.id], [test.id], [], []]
    assert values[1].result.value == 14200 and values[3].result.value_text == "Nil"
    # No date was returned, so the upload day where the patient is (the app's clock, not the
    # test machine's: CI runs in UTC, the app in India time).
    assert {v.result.measured_on for v in values} == {local_today()}
    assert done.quote_failures == ["Platelets (value)"]


def test_a_report_added_to_a_test_gives_its_values_and_moves_nothing(db):
    cbc = Entry(kind=EntryKind.TEST, label="CBC", status=EntryStatus.CONFIRMED)
    enc = _visit(db, entries=[cbc])
    before = db.get_encounter(enc.id)
    enc.sources.append(SourceItem(source_type=SourceType.IMAGE, filename="r.jpg"))
    db.save_encounter(enc)
    ai = ReportAI([_item(kind="diagnosis", label="Anaemia", source_id="doc1", quote="CBC",
                         said_by="document")], VALUES)
    process_encounter(db, ai, enc.id, [Media(enc.sources[-1].id, "image", b"img", "image/jpeg")],
                      adding=True, report_for=cbc.id)

    done = db.get_encounter(enc.id)
    assert done.status == EncounterStatus.READY and done.visit_date == before.visit_date
    assert done.visit_date_source == before.visit_date_source
    assert "Tests of this visit:\n- CBC" in ai.instructions
    new = done.entries[1:]
    assert [e.kind for e in new] == [EntryKind.RESULT] * 4  # the diagnosis was not kept
    # CBC values and the unmatched ones land on the test the patient tapped.
    assert all(e.links == [cbc.id] for e in new)
    assert done.quote_failures == ["Platelets (value)"]


def test_a_careful_reread_replaces_untouched_values_and_keeps_edited_ones(db):
    enc = Encounter(patient_id="p1", sources=[SourceItem(source_type=SourceType.IMAGE, filename="r.jpg")])
    db.save_encounter(enc)
    sid = enc.sources[0].id
    process_encounter(db, ReportAI([CBC_ITEM], VALUES), enc.id, [Media(sid, "image", b"i", "image/jpeg")])
    first = db.get_encounter(enc.id)
    hb = next(e for e in first.entries if e.label == "Hemoglobin")
    hb.apply_edit("p1", result={"value": 13.4})
    db.save_entry(hb)

    process_encounter(db, ReportAI([CBC_ITEM], VALUES), enc.id, [Media(sid, "image", b"i", "image/jpeg")],
                      careful=True)
    done = db.get_encounter(enc.id)
    live = [e for e in done.entries if e.kind == EntryKind.RESULT and e.status == EntryStatus.CONFIRMED]
    assert sorted(e.label for e in live) == ["HbA1c", "Hemoglobin", "Total leucocyte count", "Urine sugar"]
    assert next(e for e in live if e.label == "Hemoglobin").result.value == 13.4  # the edit stayed
    assert sum(e.label == "Hemoglobin" for e in done.entries) == 1
    # Values point at tests that are still there, not at the ones the re-read replaced.
    tests = {e.id for e in done.entries if e.kind == EntryKind.TEST and e.status != EntryStatus.REJECTED}
    assert all(i in tests for e in live for i in e.links)
    assert next(e for e in live if e.label == "Hemoglobin").links  # the edited one moved too


def _reread(db, enc, ai=None, source=0):
    sid = enc.sources[source].id
    process_encounter(db, ai or ReportAI([CBC_ITEM], VALUES), enc.id,
                      [Media(sid, "image", b"i", "image/jpeg")], careful=True)
    return db.get_encounter(enc.id)


def test_a_second_careful_reread_does_not_duplicate_the_values(db):
    enc = Encounter(patient_id="p1", sources=[SourceItem(source_type=SourceType.IMAGE, filename="r.jpg")])
    db.save_encounter(enc)
    process_encounter(db, ReportAI([CBC_ITEM], VALUES), enc.id,
                      [Media(enc.sources[0].id, "image", b"i", "image/jpeg")])
    _reread(db, enc)
    done = _reread(db, enc)
    live = [e.label for e in done.entries
            if e.kind == EntryKind.RESULT and e.status == EntryStatus.CONFIRMED]
    assert sorted(live) == ["HbA1c", "Hemoglobin", "Total leucocyte count", "Urine sugar"]


def test_a_careful_reread_of_a_report_added_to_a_test_is_still_a_report(db):
    cbc = Entry(kind=EntryKind.TEST, label="CBC", status=EntryStatus.CONFIRMED)
    enc = _visit(db, entries=[cbc])
    enc.sources.append(SourceItem(source_type=SourceType.IMAGE, filename="r.jpg", report_for=cbc.id))
    db.save_encounter(enc)
    visit_date = enc.visit_date

    class DatedAI(ReportAI):
        def reason_json(self, system, user, schema, **kw):
            answer = super().reason_json(system, user, schema, **kw)
            if isinstance(answer, Extraction):  # the report prints its own date and a finding
                return answer.model_copy(update={"visit_date": TODAY.isoformat()})
            return answer

    finding = _item(kind="diagnosis", label="Leucocytosis", source_id="doc1", quote="CBC",
                    said_by="document")
    done = _reread(db, enc, DatedAI([finding], VALUES), source=1)
    assert done.visit_date == visit_date  # the report's date did not become the visit's
    assert [e.label for e in done.entries if e.kind == EntryKind.DIAGNOSIS] == []
    assert any(e.kind == EntryKind.RESULT and e.links == [cbc.id] for e in done.entries)


def test_adding_a_report_keeps_what_the_visit_left_out_before(db):
    cbc = Entry(kind=EntryKind.TEST, label="CBC", status=EntryStatus.CONFIRMED)
    enc = _visit(db, entries=[cbc])
    enc.quote_failures = ["Ghost"]
    enc.sources.append(SourceItem(source_type=SourceType.IMAGE, filename="r.jpg"))
    db.save_encounter(enc)
    process_encounter(db, ReportAI([], VALUES), enc.id,
                      [Media(enc.sources[-1].id, "image", b"i", "image/jpeg")],
                      adding=True, report_for=cbc.id)
    assert db.get_encounter(enc.id).quote_failures == ["Ghost", "Platelets (value)"]


# ---- the card, edits and routes --------------------------------------------------------------
@pytest.fixture
def ctx():
    s = Storage()
    patient = Patient(name="Meera Iyer (synthetic)")
    s.save_patient(patient)
    cbc = Entry(kind=EntryKind.TEST, label="CBC", status=EntryStatus.CONFIRMED)
    hb = Entry(kind=EntryKind.RESULT, label="Hemoglobin", status=EntryStatus.CONFIRMED,
               added_as_read=True, links=[cbc.id],
               result=TestResult(value=13.2, unit="g/dL", measured_on=TODAY))
    enc = Encounter(patient_id=patient.id, status=EncounterStatus.READY, entries=[cbc, hb])
    s.save_encounter(enc)
    yield api_client(create_app(s, ReportAI([], VALUES)), person=patient.id), patient.id, enc, cbc, hb
    s.close()


def test_values_never_reach_the_emergency_card(ctx):
    client, pid, *_ = ctx
    card = client.get(f"/patients/{pid}/emergency").json()
    assert "Hemoglobin" not in str(card)
    from app.schemas.record import emergency_entries
    assert emergency_entries([ctx[4]]) == []


def test_the_record_lists_values_dated_by_their_report(ctx):
    client, pid, *_ = ctx
    facts = client.get(f"/patients/{pid}/record").json()["facts"]
    (fact,) = [f for f in facts if f["kind"] == "result"]
    assert fact["sightings"][0]["on"] == TODAY.isoformat()


def test_editing_a_value_keeps_the_original_in_the_history(ctx):
    client, _, _, _, hb = ctx
    r = client.patch(f"/entries/{hb.id}", json={"action": "edit", "result": {"value": 12.9}})
    got = r.json()
    assert r.status_code == 200 and got["result"]["value"] == 12.9 and got["status"] == "confirmed"
    assert [(e["field"], e["old"], e["new"]) for e in got["edits"]] == [("result.value", "13.2", "12.9")]
    assert client.patch(f"/entries/{hb.id}", json={"action": "edit", "result": {"value": "x"}}).status_code == 422
    assert client.patch(f"/entries/{hb.id}", json={"action": "edit", "result": {"foo": 1}}).status_code == 422
    future = (TODAY + timedelta(days=3)).isoformat()
    assert client.patch(f"/entries/{hb.id}", json={"action": "edit", "result": {"measured_on": future}}).status_code == 422


def test_removing_a_test_takes_its_values_along_and_adding_it_back_returns_them(ctx):
    client, _, enc, cbc, _ = ctx
    client.patch(f"/entries/{cbc.id}", json={"action": "reject"})
    got = {e["label"]: e for e in client.get(f"/encounters/{enc.id}").json()["entries"]}
    assert got["Hemoglobin"]["status"] == "rejected"
    client.patch(f"/entries/{cbc.id}", json={"action": "confirm"})
    got = {e["label"]: e for e in client.get(f"/encounters/{enc.id}").json()["entries"]}
    assert got["Hemoglobin"]["status"] == "confirmed"


def test_a_value_the_patient_removed_or_edited_is_left_alone_by_its_test(ctx):
    client, _, enc, cbc, hb = ctx
    client.patch(f"/entries/{hb.id}", json={"action": "edit", "result": {"value": 12.9}})
    client.patch(f"/entries/{cbc.id}", json={"action": "reject"})
    assert client.get(f"/encounters/{enc.id}").json()["entries"][1]["status"] == "confirmed"
    client.patch(f"/entries/{hb.id}", json={"action": "reject"})
    client.patch(f"/entries/{cbc.id}", json={"action": "confirm"})
    assert client.get(f"/encounters/{enc.id}").json()["entries"][1]["status"] == "rejected"


def _add(client, enc_id, **data):
    return client.post(f"/encounters/{enc_id}/documents", data=data,
                       files=[("documents", ("report.jpg", b"img", "image/jpeg"))])


def test_add_report_reads_the_values_into_the_visit(ctx):
    client, _, enc, cbc, _ = ctx
    assert _add(client, enc.id, test_id=cbc.id).status_code == 202
    done = client.get(f"/encounters/{enc.id}").json()
    assert done["status"] == "ready" and done["error"] is None
    assert [e["label"] for e in done["entries"] if e["kind"] == "result"] == [
        "Hemoglobin", "Hemoglobin", "Total leucocyte count", "HbA1c", "Urine sugar"]


def test_add_report_needs_a_test_of_that_visit(ctx):
    client, _, enc, cbc, hb = ctx
    assert _add(client, enc.id).status_code == 409  # a visit takes documents only as a report
    assert _add(client, enc.id, test_id="nope").status_code == 422
    assert _add(client, enc.id, test_id=hb.id).status_code == 422  # a value is not a test
    client.patch(f"/entries/{cbc.id}", json={"action": "reject"})
    assert _add(client, enc.id, test_id=cbc.id).status_code == 422  # turned down


@pytest.mark.parametrize("marker", ["- ", "* ", "\u2022 ", "\u2013 ", "1. ", "2) ", ""])
def test_a_list_marker_is_not_kept_in_a_result_in_words(marker):
    line = "Centrilobular emphysema, predominantly involving both upper lobes."
    src = _source(f"IMPRESSION:\n{marker}{line}")
    for text in (marker + line, line):  # validates with or without the marker
        r = _result(name="Centrilobular emphysema", quote=marker + line, value=None,
                    value_text=text, unit=None, ref_low=None, ref_high=None)
        assert check_result(r, src) == TestResult(value_text=line)
