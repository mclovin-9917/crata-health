"""My health numbers: names matched to measures, series by unit, the printed range each number is
compared with, readings added by hand, and that none of it reaches the emergency card."""

from datetime import date, timedelta

import pytest

from app.main import create_app
from app.parameters import PRESETS, is_known, measure_of, unit_key
from app.pipeline.run import local_today
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    Entry,
    EntryKind,
    EntryStatus,
    Provenance,
    SourceType,
    StatusChange,
    TestResult,
)
from app.storage import Storage
from app.views import numbers
from tests.conftest import api_client

TODAY = local_today()  # the app's day (India time), not the machine's


def day(n: int) -> date:
    return TODAY - timedelta(days=n)


def val(label, value, unit=None, on=None, low=None, high=None, **kw):
    kw.setdefault("status", EntryStatus.CONFIRMED)
    kw.setdefault("added_as_read", True)
    return Entry(kind=EntryKind.RESULT, label=label, **kw,
                 result=TestResult(value=value, unit=unit, ref_low=low, ref_high=high,
                                   measured_on=on or day(1)))


def mine(label, value, unit=None, on=None):
    """A reading the patient added by hand."""
    return val(label, value, unit, on, added_as_read=False,
               provenance=[Provenance(source_id="manual", source_type=SourceType.MANUAL)])


def card(entries, key_or_name):
    groups = {g.key: g for g in numbers(entries, []).groups}
    return groups[key_or_name]


# ---- names ------------------------------------------------------------------------------------
@pytest.mark.parametrize("label, key", [
    ("Hemoglobin", "hemoglobin"), ("Hb", "hemoglobin"), ("Haemoglobin (g/dL)", "hemoglobin"),
    ("HbA1c", "hba1c"), ("Glycosylated haemoglobin (HbA1c)", "hba1c"),
    ("Glycosylated Hemoglobin: HbA1c", "hba1c"), ("Hemoglobin A1c", "hba1c"),
    ("Total leucocyte count", "wbc"), ("TLC", "wbc"), ("WBC count", "wbc"),
    ("Total Cholesterol", "cholesterol"), ("LDL-C", "ldl"), ("HDL Cholesterol", "hdl"),
    ("Fasting Blood Sugar", "glucose_fasting"), ("FBS", "glucose_fasting"),
    ("PPBS", "glucose_after_meals"), ("Blood sugar", "glucose"),
    ("BP (systolic)", "bp_systolic"), ("Blood pressure (diastolic)", "bp_diastolic"),
    ("Serum creatinine", "creatinine"), ("SGPT", "alt"), ("Oxygen level (SpO2)", "spo2"),
])
def test_names_a_lab_uses_are_matched_to_one_measure(label, key):
    assert measure_of(label).key == key and is_known(label)


def test_an_unknown_name_is_its_own_measure_so_nothing_is_lost():
    a, b = measure_of("Anti-TPO antibody"), measure_of("anti tpo  antibody")
    assert a.key == b.key == "other:anti tpo antibody" and a.name == "Anti-TPO antibody"
    assert not is_known("Anti-TPO antibody")


def test_units_compare_apart_from_case_spaces_and_a_leading_x():
    assert unit_key("mg/dL") == unit_key(" MG/dl ") and unit_key("x10^3/uL") == unit_key("10^3/µL")
    assert unit_key("mg/dL") != unit_key("mmol/L") and unit_key(None) == ""


def test_the_presets_use_names_the_table_knows():
    for p in PRESETS:
        assert all(is_known(n) for n in (p.parts or [p.name])), p.name


@pytest.mark.parametrize("label", [
    "Mean corpuscular hemoglobin", "MCHC (Mean Corpuscular Hemoglobin Concentration)",
    "Non-HDL cholesterol", "VLDL cholesterol", "Total cholesterol/HDL ratio", "LDL/HDL ratio",
    "Estimated average glucose", "Urine glucose", "Urine creatinine", "BUN/Creatinine ratio",
    "Microalbumin/creatinine ratio", "Creatinine clearance", "Free T4", "Free T3", "Reverse T3",
    "Urine RBC", "Pus cells (WBC)",
])
def test_a_different_quantity_is_never_filed_under_the_measure_it_mentions(label):
    assert measure_of(label).key == f"other:{' '.join(label.casefold().replace('/', ' ').replace('(', ' ').replace(')', ' ').replace('-', ' ').split())}"
    assert not is_known(label)


@pytest.mark.parametrize("label, key", [
    ("Blood urea nitrogen", "bun"), ("BUN", "bun"), ("Blood urea", "urea"),
    ("Glucose (PP)", "glucose_after_meals"), ("Glucose Random", "glucose_random"),
    ("SpO2 (pulse oximetry)", "spo2"), ("Pulse rate", "pulse"),
])
def test_bun_is_not_urea_and_other_common_spellings_land_right(label, key):
    assert measure_of(label).key == key


def test_the_same_unit_written_differently_is_one_unit():
    assert unit_key("mm of Hg") == unit_key("mmHg") == unit_key("mm/Hg")
    assert unit_key("bpm") == unit_key("/min")


def test_a_hand_reading_does_not_take_its_name_from_a_different_quantity(ctx):
    client, _, pid = ctx
    (e,) = _add(client, pid, {"name": "Non-HDL cholesterol", "value": 131, "unit": "mg/dL"}).json()
    assert e["label"] == "Non-HDL cholesterol"  # the patient's own words are kept


# ---- series -----------------------------------------------------------------------------------
def test_values_with_different_names_for_one_measure_make_one_line_oldest_first():
    g = card([val("Glycosylated haemoglobin (HbA1c)", 7.1, "%", day(90)),
              val("HbA1c", 6.4, "%", day(10)), val("Hemoglobin", 13.2, "g/dL")], "hba1c")
    assert g.name == "HbA1c" and g.count == 2
    (s,) = g.series
    assert [p.value for p in s.points] == [7.1, 6.4] and s.latest.value == 6.4
    assert "hemoglobin" in {x.key for x in numbers([val("Hemoglobin", 13, "g/dL")], []).groups}


def test_blood_pressure_is_one_card_with_two_lines():
    g = card([val("BP (systolic)", 138, "mmHg"), val("BP (diastolic)", 88, "mmHg"),
              mine("Blood pressure (systolic)", 134, "mmHg", day(0))], "blood_pressure")
    assert g.name == "Blood pressure"
    assert [(s.key, len(s.points)) for s in g.series] == [("bp_systolic", 2), ("bp_diastolic", 1)]
    assert g.count == 2  # a blood pressure is two numbers but one reading: the longest line counts


def test_different_units_are_one_card_each_and_never_converted_or_mixed():
    out = numbers([val("Fasting blood sugar", 96, "mg/dL", day(40)), val("FBS", 5.4, "mmol/L", day(5)),
                   val("FBS", 101, "mg/dl", day(2))], [])
    # "mg/dL" and "mg/dl" are one unit; mmol/L is another card with its own scale.
    by_name = {g.name.lower(): [p.value for s in g.series for p in s.points] for g in out.groups}
    assert by_name == {"blood sugar (fasting), mg/dl": [96, 101], "blood sugar (fasting), mmol/l": [5.4]}


def test_a_value_with_no_unit_joins_the_only_unit_the_measure_has():
    g = card([val("HbA1c", 7.0, "%", day(30)), val("HbA1c", 6.2, None, day(3))], "hba1c")
    (s,) = g.series
    assert [p.value for p in s.points] == [7.0, 6.2] and s.unit == "%" and not s.unit_missing


def test_a_value_with_no_unit_stands_apart_when_there_are_several_units():
    out = numbers([val("FBS", 96, "mg/dL"), val("FBS", 5.4, "mmol/L"), val("FBS", 100, None)], [])
    assert sorted(g.name.lower() for g in out.groups) == [
        "blood sugar (fasting), mg/dl", "blood sugar (fasting), mmol/l",
        "blood sugar (fasting), unit not printed"]
    apart = next(g for g in out.groups if g.series[0].unit_missing)
    assert [p.value for p in apart.series[0].points] == [100]


def test_a_value_with_no_unit_neither_lends_nor_borrows_a_range():
    # A ratio printed with "up to 5" and no unit joined the mg/dL line: it must not become that
    # line's range, and a unitless value must not be compared with the line's range either.
    g = card([val("Total cholesterol", 214, "mg/dL", day(40), None, 200),
              val("Total cholesterol", 4.1, None, day(20), None, 5.0),
              mine("Total cholesterol", 190, "mg/dL", day(2)),
              mine("Total cholesterol", 6.0, None, day(1))], "cholesterol")
    (s,) = g.series
    assert (s.range.high, s.range.on) == (200, day(40))
    hand, unitless_hand = s.points[-2], s.points[-1]
    assert hand.position == "within" and hand.range.borrowed and hand.range.high == 200
    assert unitless_hand.range is None and unitless_hand.position is None
    assert s.points[1].position == "within" and s.points[1].range.high == 5.0  # its own range


def test_blood_pressure_stays_together_when_only_one_line_has_a_second_unit():
    out = numbers([val("BP (systolic)", 138, "mmHg"), val("BP (diastolic)", 88, "mmHg", day(3)),
                   val("BP (diastolic)", 11.7, "kPa", day(1))], [])
    cards = {g.name: [s.key for s in g.series] for g in out.groups}
    assert cards == {"Blood pressure, mmHg": ["bp_systolic", "bp_diastolic"],
                     "Blood pressure, kPa": ["bp_diastolic"]}


def test_blood_pressure_written_with_different_spellings_of_mmhg_is_one_card():
    out = numbers([val("BP (systolic)", 138, "mmHg"), val("BP (diastolic)", 88, "mm of Hg")], [])
    assert [g.name for g in out.groups] == ["Blood pressure"]


def test_a_report_and_a_reading_for_one_measure_are_one_fact_in_the_summary():
    from app.views import fact_key
    assert fact_key(val("Fasting blood sugar", 96, "mg/dL")) == fact_key(mine("Blood sugar (fasting)", 118, "mg/dL"))
    assert fact_key(val("Weight", 70, "kg")) != fact_key(val("Pulse", 70, "/min"))


def test_cards_of_the_same_day_are_in_alphabetical_order():
    out = numbers([val("Weight", 70, "kg", day(3)), val("Pulse", 70, "/min", day(3)),
                   val("Triglycerides", 150, "mg/dL", day(3))], [])
    assert [g.name for g in out.groups] == ["Pulse", "Triglycerides", "Weight"]


def test_only_numbers_that_are_in_the_record_are_charted():
    words = Entry(kind=EntryKind.RESULT, label="Urine sugar", status=EntryStatus.CONFIRMED,
                  result=TestResult(value_text="Nil", measured_on=day(1)))
    replaced = val("HbA1c", 9.9, "%", status=EntryStatus.REJECTED)
    replaced.status_history = [StatusChange(status=EntryStatus.REJECTED, by="system")]
    waiting = val("HbA1c", 8.8, "%", status=EntryStatus.PROPOSED)
    removed = val("HbA1c", 7.7, "%", status=EntryStatus.REJECTED)
    removed.status_history = [StatusChange(status=EntryStatus.REJECTED, by="p1")]
    kept = val("HbA1c", 6.6, "%")
    out = numbers([words, replaced, waiting, removed, kept], [])
    assert [g.key for g in out.groups] == ["hba1c"]  # no card for words
    (s,) = out.groups[0].series
    assert sorted((p.value, p.removed) for p in s.points) == [(6.6, False), (7.7, True)]
    assert s.latest.value == 6.6 and out.groups[0].count == 1


def test_a_value_without_its_own_date_takes_its_visits():
    enc = Encounter(patient_id="p", visit_date=day(9))
    e = val("Weight", 70, "kg")
    e.result.measured_on = None
    e.encounter_id = enc.id
    (g,) = numbers([e], [enc]).groups
    assert g.series[0].points[0].on == day(9)


def test_cards_are_newest_first():
    out = numbers([val("Weight", 70, "kg", day(30)), val("Pulse", 70, "/min", day(2)),
                   val("HbA1c", 6, "%", day(10))], [])
    assert [g.key for g in out.groups] == ["pulse", "hba1c", "weight"]


# ---- ranges -----------------------------------------------------------------------------------
def test_a_number_is_compared_with_the_range_printed_beside_it():
    g = card([val("HbA1c", 6.9, "%", day(5), 4.0, 5.6), val("HbA1c", 5.0, "%", day(40), 4.0, 5.6),
              val("HbA1c", 3.1, "%", day(80), 4.0, 5.6)], "hba1c")
    assert [p.position for p in g.series[0].points] == ["low", "within", "high"]
    assert g.flagged and g.series[0].latest.position == "high"
    assert all(not p.range.borrowed for p in g.series[0].points)


def test_a_reading_with_no_range_borrows_the_latest_printed_one_and_says_whose():
    g = card([val("Fasting blood sugar", 90, "mg/dL", day(60), 70, 99),
              val("Fasting blood sugar", 96, "mg/dL", day(20), 70, 100),
              mine("Blood sugar (fasting)", 118, "mg/dL", day(2))], "glucose_fasting")
    (s,) = g.series
    hand = s.points[-1]
    assert hand.source == "you" and hand.position == "high"
    assert hand.range.borrowed and (hand.range.low, hand.range.high, hand.range.on) == (70, 100, day(20))
    assert not s.points[0].range.borrowed and s.range.on == day(20)


def test_no_printed_range_anywhere_means_no_marker():
    g = card([mine("Weight", 62, "kg"), mine("Weight", 71, "kg", day(40))], "weight")
    assert all(p.position is None and p.range is None for p in g.series[0].points) and not g.flagged


def test_a_range_is_borrowed_only_from_the_same_unit():
    out = numbers([val("FBS", 5.4, "mmol/L", day(30), 3.9, 5.5), mine("FBS", 130, "mg/dL", day(1))], [])
    mg = next(g for g in out.groups if g.name.endswith("mg/dL"))
    assert mg.series[0].points[0].position is None  # the mmol/L range does not apply to mg/dL


def test_a_removed_reading_is_not_the_latest_or_the_range_source():
    gone = val("HbA1c", 9.0, "%", day(1), 4.0, 5.6, status=EntryStatus.REJECTED)
    gone.status_history = [StatusChange(status=EntryStatus.REJECTED, by="p1")]
    g = card([val("HbA1c", 5.0, "%", day(30), 4.0, 6.0), gone], "hba1c")
    (s,) = g.series
    assert s.latest.value == 5.0 and s.range.high == 6.0 and any(p.removed for p in s.points)


# ---- the routes -------------------------------------------------------------------------------
@pytest.fixture
def ctx():
    db = Storage()
    patient = Patient(name="Meera Iyer (synthetic)")
    db.save_patient(patient)
    yield api_client(create_app(db), person=patient.id), db, patient.id
    db.close()


def _add(client, pid, *readings, **body):
    return client.post(f"/patients/{pid}/readings", json={"readings": list(readings), **body})


def test_a_reading_added_by_hand_is_the_patients_own_confirmed_statement(ctx):
    client, _, pid = ctx
    r = _add(client, pid, {"name": "weight", "value": 62.5, "unit": "kg"}, on=day(2).isoformat())
    assert r.status_code == 201
    (e,) = r.json()
    assert e["kind"] == "result" and e["label"] == "Weight" and e["status"] == "confirmed"
    assert e["result"] == {"value": 62.5, "value_text": None, "unit": "kg", "ref_low": None,
                           "ref_high": None, "ref_text": None, "measured_on": day(2).isoformat()}
    assert e["provenance"][0]["source_type"] == "manual" and e["said_by"] == "patient"
    assert e["status_history"][-1]["by"] == pid and e["added_as_read"] is False
    assert e["encounter_id"] is None


def test_a_blood_pressure_is_two_readings_that_land_on_one_card(ctx):
    client, _, pid = ctx
    r = _add(client, pid, {"name": "Blood pressure (systolic)", "value": 138, "unit": "mmHg"},
             {"name": "Blood pressure (diastolic)", "value": 88, "unit": "mmHg"})
    assert [e["result"]["unit"] for e in r.json()] == ["mmHg", "mmHg"]
    groups = client.get(f"/patients/{pid}/numbers").json()["groups"]
    assert [(g["name"], [s["key"] for s in g["series"]]) for g in groups] == [
        ("Blood pressure", ["bp_systolic", "bp_diastolic"])]


def test_a_name_the_app_does_not_know_is_kept_as_typed(ctx):
    client, _, pid = ctx
    (e,) = _add(client, pid, {"name": "  Anti-TPO   antibody ", "value": 12, "unit": "IU/mL"}).json()
    assert e["label"] == "Anti-TPO antibody" and e["result"]["unit"] == "IU/mL"


def test_a_unit_nobody_gave_is_not_made_up(ctx):
    client, _, pid = ctx
    (e,) = _add(client, pid, {"name": "Blood sugar (fasting)", "value": 6.2}).json()
    assert e["result"]["unit"] is None  # not "mg/dL": this may well be mmol/L
    (e2,) = _add(client, pid, {"name": "Weight", "value": 62, "unit": "  "}).json()
    assert e2["result"]["unit"] is None


def test_a_reading_can_be_dated_today_where_the_patient_is(ctx):
    from app.pipeline.run import local_today
    client, _, pid = ctx
    assert _add(client, pid, {"name": "Weight", "value": 62, "unit": "kg"},
                on=local_today().isoformat()).status_code == 201
    assert _add(client, pid, {"name": "Weight", "value": 62},
                on=(local_today() + timedelta(days=1)).isoformat()).status_code == 422


def test_a_value_edit_refuses_nan_and_negative_numbers(ctx):
    client, _, pid = ctx
    (e,) = _add(client, pid, {"name": "Weight", "value": 62, "unit": "kg"}).json()
    headers = {"content-type": "application/json"}
    for body in ('{"action":"edit","result":{"value":NaN}}', '{"action":"edit","result":{"value":-3}}',
                 '{"action":"edit","result":{"ref_high":Infinity}}'):
        assert client.patch(f"/entries/{e['id']}", content=body, headers=headers).status_code == 422
    assert client.get(f"/patients/{pid}/numbers").json()["groups"][0]["series"][0]["points"][0]["value"] == 62


@pytest.mark.parametrize("body", [
    {"readings": [{"name": "   ", "value": 1}]},
    {"readings": []},
    {"readings": [{"name": "Weight", "value": -1}]},
    {"readings": [{"name": "", "value": 1}]},
    {"readings": [{"name": "Weight", "value": "x"}]},
    {"readings": [{"name": "Weight", "value": 60}], "on": (TODAY + timedelta(days=1)).isoformat()},
    {"readings": [{"name": "Weight", "value": 60}] * 11},
])
def test_bad_readings_are_refused(ctx, body):
    client, _, pid = ctx
    assert client.post(f"/patients/{pid}/readings", json=body).status_code == 422


def test_the_numbers_route_tells_the_screen_what_to_offer(ctx):
    client, _, pid = ctx
    got = client.get(f"/patients/{pid}/numbers").json()
    assert got["groups"] == [] and got["presets"][0]["parts"] == [
        "Blood pressure (systolic)", "Blood pressure (diastolic)"]


def test_a_reading_can_be_corrected_removed_and_put_back_like_any_value(ctx):
    client, _, pid = ctx
    (e,) = _add(client, pid, {"name": "Weight", "value": 62}).json()
    r = client.patch(f"/entries/{e['id']}", json={"action": "edit", "result": {"value": 63}})
    assert r.status_code == 200 and r.json()["result"]["value"] == 63
    client.patch(f"/entries/{e['id']}", json={"action": "reject"})
    (p,) = client.get(f"/patients/{pid}/numbers").json()["groups"][0]["series"][0]["points"]
    assert p["removed"] is True
    client.patch(f"/entries/{e['id']}", json={"action": "confirm"})
    (p,) = client.get(f"/patients/{pid}/numbers").json()["groups"][0]["series"][0]["points"]
    assert p["removed"] is False and p["value"] == 63


def test_readings_never_reach_the_emergency_card(ctx):
    client, _, pid = ctx
    _add(client, pid, {"name": "Weight", "value": 62}, {"name": "Pulse", "value": 70})
    card = client.get(f"/patients/{pid}/emergency").json()
    assert "Weight" not in str(card) and "Pulse" not in str(card)


def test_only_the_patient_can_read_or_add_their_numbers(ctx):
    client, _, pid = ctx
    app = client.app
    db = app.state.storage
    for who in (api_client(app, person="someone-else"), api_client(app)):
        assert who.get(f"/patients/{pid}/numbers").status_code in (401, 404)
        assert _add(who, pid, {"name": "Weight", "value": 1}).status_code in (401, 404)
    assert db.list_entries(pid) == []
