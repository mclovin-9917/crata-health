"""Emergency and hospital stays the patient logs themselves."""

from datetime import date, timedelta

import pytest

from app.main import create_app
from app.schemas.patient import Patient
from app.storage import Storage
from tests.conftest import api_client

TODAY = date.today()


@pytest.fixture
def ctx():
    db = Storage()
    patient = Patient(name="Meera Iyer (synthetic)", blood_group="O+")
    db.save_patient(patient)
    yield api_client(create_app(db), person=patient.id), patient.id
    db.close()


def _log(client, pid, **body):
    base = {"kind": "admission", "place": " City  Hospital ", "reason": "Pneumonia",
            "started_on": (TODAY - timedelta(days=2)).isoformat()}
    return client.post(f"/patients/{pid}/stays", json={**base, **body})


def test_logging_a_stay_puts_the_patients_own_entry_in_the_record(ctx):
    client, pid = ctx
    r = _log(client, pid)
    assert r.status_code == 201
    enc = r.json()
    stay, (entry,) = enc["stay"], enc["entries"]
    assert stay["place"] == "City Hospital" and stay["ended_on"] is None
    assert enc["status"] == "ready" and enc["visit_date"] == stay["started_on"]
    assert entry["id"] == stay["entry_id"]
    assert entry["kind"] == "admission" and entry["label"] == "Pneumonia"
    assert entry["status"] == "confirmed" and entry["unverified"]
    assert entry["status_history"][-1]["by"] == pid
    assert entry["fields"] == {"place": "City Hospital"}
    assert entry["occurred_on"] == stay["started_on"]


def test_an_emergency_visit_without_a_reason_gets_a_plain_label(ctx):
    client, pid = ctx
    entry = _log(client, pid, kind="emergency_visit", reason=None, place=None).json()["entries"][0]
    assert entry["label"] == "Emergency visit" and entry["fields"] == {}


@pytest.mark.parametrize("body", [
    {"started_on": (TODAY + timedelta(days=1)).isoformat()},
    {"ended_on": (TODAY - timedelta(days=5)).isoformat()},  # before it started
    {"kind": "surgery"},
])
def test_bad_stays_are_refused(ctx, body):
    client, pid = ctx
    assert _log(client, pid, **body).status_code == 422


def test_an_open_stay_shows_on_the_card_until_it_is_closed(ctx):
    client, pid = ctx
    enc = _log(client, pid).json()
    (shown,) = client.get(f"/patients/{pid}/emergency").json()["open_stays"]
    assert shown == {"kind": "admission", "place": "City Hospital", "reason": "Pneumonia",
                     "since": enc["stay"]["started_on"]}

    r = client.patch(f"/encounters/{enc['id']}/stay", json={"ended_on": TODAY.isoformat()})
    assert r.status_code == 200 and r.json()["stay"]["ended_on"] == TODAY.isoformat()
    card = client.get(f"/patients/{pid}/emergency").json()
    assert card["open_stays"] == []
    assert [e["label"] for e in card["timeline"]] == ["Pneumonia"]  # still in the timeline


def test_closing_and_editing_go_through_the_entrys_edit_history(ctx):
    client, pid = ctx
    enc = _log(client, pid).json()
    r = client.patch(f"/encounters/{enc['id']}/stay",
                     json={"ended_on": TODAY.isoformat(), "reason": "Pneumonia, oxygen given"})
    entry = r.json()["entries"][0]
    assert entry["label"] == "Pneumonia, oxygen given"
    assert entry["fields"] == {"place": "City Hospital", "until": TODAY.isoformat()}
    assert {(x["field"], x["old"]) for x in entry["edits"]} == {
        ("label", "Pneumonia"), ("fields.until", None)}
    assert entry["status"] == "confirmed"

    # Opening it again (no end day) takes "until" away.
    entry = client.patch(f"/encounters/{enc['id']}/stay", json={"ended_on": None}).json()["entries"][0]
    assert "until" not in entry["fields"]


def test_a_stay_the_patient_removed_is_not_on_the_card(ctx):
    client, pid = ctx
    entry_id = _log(client, pid).json()["entries"][0]["id"]
    client.patch(f"/entries/{entry_id}", json={"action": "reject"})
    assert client.get(f"/patients/{pid}/emergency").json()["open_stays"] == []


def test_closing_dates_are_checked_and_only_stays_can_be_closed(ctx):
    client, pid = ctx
    enc = _log(client, pid).json()
    early = (TODAY - timedelta(days=9)).isoformat()
    assert client.patch(f"/encounters/{enc['id']}/stay", json={"ended_on": early}).status_code == 422
    visit = client.post(f"/patients/{pid}/encounters", data={"notes": "x"}).json()
    assert client.patch(f"/encounters/{visit['id']}/stay", json={"place": "x"}).status_code == 409


# ---- documents added later -----------------------------------------------------------------
DISCHARGED = TODAY - timedelta(days=1)
SUMMARY = (
    "City Hospital - Discharge Summary\n"
    f"Admitted: {(TODAY - timedelta(days=3)).strftime('%d/%m/%Y')}  "
    f"Discharged on {DISCHARGED.strftime('%d/%m/%Y')}\n"
    "Diagnosis: Community acquired pneumonia\n"
    "Treatment given: Inj Ceftriaxone 1 g IV twice daily\n"
    "Advice on discharge: Tab Azithral 500 once daily for 3 days"
)


def _stay_ai(items, *, discharge_quote=None, docs=None, fail=False):
    from app.pipeline.extract import Extraction
    from tests.test_pipeline import FakeAI

    class StayAI(FakeAI):
        def perceive(self, parts, instruction, **kw):
            self.calls.append(("perceive", parts[0]["type"], instruction))
            if fail:
                from app.ai.client import AIError
                raise AIError("endpoint busy")
            return SUMMARY

        def reason_json(self, system, user, schema, **kw):
            answer = super().reason_json(system, user, schema, **kw)
            if isinstance(answer, Extraction):
                self.instructions = system
                quote = discharge_quote or f"Discharged on {DISCHARGED.strftime('%d/%m/%Y')}"
                return answer.model_copy(update={
                    "discharge_date": DISCHARGED.isoformat(), "discharge_source_id": "doc1",
                    "discharge_quote": quote})
            return answer

    return StayAI(items, docs=docs)


def _stay_items():
    from tests.test_pipeline import _item

    return [
        _item(kind="diagnosis", label="Community acquired pneumonia", source_id="doc1",
              quote="Diagnosis: Community acquired pneumonia", said_by="document"),
        _item(kind="medication", label="Azithral 500", source_id="doc1",
              quote="Tab Azithral 500 once daily for 3 days", said_by="document",
              doses_per_day=1, duration_value=3, duration_unit="day"),
        _item(kind="note", label="Given in hospital: Ceftriaxone", source_id="doc1",
              quote="Inj Ceftriaxone 1 g IV twice daily", said_by="document"),
        # The model described the admission itself: the patient's own entry already is that.
        _item(kind="admission", label="Admission for pneumonia", source_id="doc1",
              quote="City Hospital - Discharge Summary", said_by="document"),
    ]


def _stay_client(ai):
    db = Storage()
    patient = Patient(name="Meera Iyer (synthetic)")
    db.save_patient(patient)
    client = api_client(create_app(db, ai), person=patient.id)
    enc = _log(client, patient.id, started_on=(TODAY - timedelta(days=3)).isoformat()).json()
    return client, enc


def _add(client, enc_id, **data):
    files = [("documents", ("discharge.jpg", b"img", "image/jpeg"))]
    return client.post(f"/encounters/{enc_id}/documents", files=files, data=data)


def test_documents_added_to_a_stay_are_suggested_and_linked_to_it():
    ai = _stay_ai(_stay_items())
    client, enc = _stay_client(ai)
    assert _add(client, enc["id"]).status_code == 202
    done = client.get(f"/encounters/{enc['id']}").json()

    assert done["status"] == "ready" and done["error"] is None
    stay_entry, *new = done["entries"]
    assert stay_entry["id"] == enc["stay"]["entry_id"] and stay_entry["status"] == "confirmed"
    assert [e["label"] for e in new] == [
        "Community acquired pneumonia", "Azithral 500", "Given in hospital: Ceftriaxone"]
    assert all(e["links"] == [stay_entry["id"]] for e in new)
    # The diagnosis and the medicine wait for the patient; the note is read as it is.
    assert [e["status"] for e in new] == ["proposed", "proposed", "confirmed"]
    assert new[2]["added_as_read"] and not new[0]["added_as_read"]
    assert "Do not make an item for the stay itself" in ai.instructions
    # The discharge date is a suggestion; the stay stays open until the patient closes it.
    stay = done["stay"]
    assert stay["ended_on"] is None and stay["suggested_end"] == DISCHARGED.isoformat()
    assert stay["suggested_end_quote"].startswith("Discharged on")
    # Home medicines start the day the patient went home; the stay keeps its own start date.
    assert new[1]["medication"]["started_on"] == DISCHARGED.isoformat()
    assert done["visit_date"] == enc["visit_date"]


def test_a_discharge_date_whose_words_are_not_in_the_document_is_not_suggested():
    client, enc = _stay_client(_stay_ai(_stay_items(), discharge_quote="Discharged yesterday"))
    _add(client, enc["id"])
    assert client.get(f"/encounters/{enc['id']}").json()["stay"]["suggested_end"] is None


def test_a_failed_read_leaves_the_stay_as_it_was():
    client, enc = _stay_client(_stay_ai(_stay_items(), fail=True))
    _add(client, enc["id"])
    done = client.get(f"/encounters/{enc['id']}").json()
    assert done["status"] == "ready" and done["error"].startswith("Reading the added documents failed")
    assert done["sources"] == [] and len(done["entries"]) == 1


def test_documents_only_go_to_stays_and_not_while_one_is_being_read(ctx):
    client, pid = ctx
    visit = client.post(f"/patients/{pid}/encounters", data={"notes": "x"}).json()
    assert _add(client, visit["id"]).status_code == 409
    stay = _log(client, pid).json()
    assert client.post(f"/encounters/{stay['id']}/documents", data={}).status_code == 422


def test_a_hospital_bill_in_a_stay_is_checked_for_allergies_only():
    from app.pipeline.bills import ReadBillLine, SortedDocument

    lines = [ReadBillLine(name="Inj Ceftriaxone 1 g", quote="Inj Ceftriaxone 1 g IV twice daily",
                          quantity=None, item_type="medicine")]
    ai = _stay_ai([], docs=[SortedDocument(source_id="doc1", kind="bill", lines=lines)])
    client, enc = _stay_client(ai)
    _add(client, enc["id"])
    done = client.get(f"/encounters/{enc['id']}").json()
    assert len(done["bill_lines"]) == 1
    assert len(done["entries"]) == 1  # no medicine suggestions from a hospital bill
    assert done["discrepancies"] == []  # and no "billed, not prescribed"


def test_the_record_lists_the_stay_as_a_visit(ctx):
    client, pid = ctx
    enc = _log(client, pid).json()
    (visit,) = [v for v in client.get(f"/patients/{pid}/record").json()["visits"]
                if v["encounter_id"] == enc["id"]]
    assert visit["stay"]["kind"] == "admission" and len(visit["entries"]) == 1
