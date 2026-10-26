from datetime import date, timedelta

import pytest

from app.main import create_app
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    Medication,
    Tier,
)
from app.storage import Storage
from tests.conftest import api_client

TODAY = date.today()


@pytest.fixture
def ctx():
    db = Storage()
    patient = Patient(name="Asha Verma (synthetic)", blood_group="B+")
    db.save_patient(patient)
    enc = Encounter(
        patient_id=patient.id,
        status=EncounterStatus.READY,
        entries=[
            Entry(kind=EntryKind.ALLERGY, label="Penicillin", tier=Tier.EMERGENCY_CARD),
            Entry(kind=EntryKind.MEDICATION, label="Azithromycin 500 mg",
                  medication=Medication(dose="500 mg", duration_days=3, started_on=TODAY)),
            Entry(kind=EntryKind.MEDICATION, label="Old antibiotic",
                  medication=Medication(duration_days=5,
                                        started_on=TODAY - timedelta(days=400))),
            Entry(kind=EntryKind.DIAGNOSIS, label="Type 2 diabetes", tier=Tier.EMERGENCY_CARD),
            Entry(kind=EntryKind.DIAGNOSIS, label="Sore throat"),
        ],
    )
    db.save_encounter(enc)
    client = api_client(create_app(db), person=patient.id)
    yield client, patient, enc
    db.close()


def _confirm_all(client, enc):
    for e in enc.entries:
        assert client.patch(f"/entries/{e.id}", json={"action": "confirm"}).status_code == 200


def test_patients_and_encounter(ctx):
    client, patient, enc = ctx
    assert client.get("/patients").status_code == 403  # the list of everyone: admins only
    assert client.get(f"/patients/{patient.id}").json()["blood_group"] == "B+"
    assert client.get("/patients/nope").status_code == 404
    body = client.get(f"/encounters/{enc.id}").json()
    assert body["status"] == "ready" and len(body["entries"]) == 5
    assert all(e["status"] == "proposed" for e in body["entries"])


def test_confirm_reject_and_conflicts(ctx):
    client, _, enc = ctx
    eid = enc.entries[0].id
    r = client.patch(f"/entries/{eid}", json={"action": "confirm", "note": "yes"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert client.patch(f"/entries/{eid}", json={"action": "confirm"}).status_code == 409
    assert client.patch(f"/entries/{eid}", json={"action": "reject"}).json()["status"] == "rejected"
    assert [h["status"] for h in r.json()["status_history"]] == ["confirmed"]
    assert client.patch(f"/entries/{eid}", json={"action": "edit", "label": "x"}).status_code == 409
    assert client.patch("/entries/nope", json={"action": "confirm"}).status_code == 404
    assert client.patch(f"/entries/{eid}", json={"action": "dance"}).status_code == 422


def test_review_hides_a_clash_once_its_medicine_is_rejected():
    from app.pipeline.crosscheck import allergy_clashes
    from tests.test_crosscheck import FamilyAI, allergy, med

    db = Storage()
    patient = Patient(name="Asha Verma (synthetic)")
    db.save_patient(patient)
    penicillin = allergy("Penicillin", status=EntryStatus.PROPOSED, manual=False)
    augmentin = med("Augmentin 625", ("amoxicillin", "clavulanic acid"))
    enc = Encounter(patient_id=patient.id, status=EncounterStatus.READY,
                    entries=[penicillin, augmentin])
    enc.discrepancies = allergy_clashes(enc.entries, [], [], FamilyAI())
    db.save_encounter(enc)
    client = api_client(create_app(db), person=patient.id)

    (clash,) = client.get(f"/encounters/{enc.id}").json()["discrepancies"]
    assert clash["severity"] == "urgent" and clash["entry_ids"] == [augmentin.id, penicillin.id]
    client.patch(f"/entries/{augmentin.id}", json={"action": "reject"})
    assert client.get(f"/encounters/{enc.id}").json()["discrepancies"] == []


def test_edit_keeps_proposed_and_records_change(ctx):
    client, _, enc = ctx
    med = enc.entries[1]
    r = client.patch(f"/entries/{med.id}", json={"action": "edit", "medication": {"dose": "250 mg"}})
    body = r.json()
    assert body["status"] == "proposed"
    assert body["medication"]["dose"] == "250 mg"
    assert body["edits"][0]["field"] == "medication.dose" and body["edits"][0]["old"] == "500 mg"
    bad = client.patch(f"/entries/{med.id}",
                       json={"action": "edit", "medication": {"duration_days": 0}})
    assert bad.status_code == 422


def test_confirming_an_entry_with_hindi_translates_it_in_the_background(ctx):
    from app.pipeline.medicines import Translation

    client, _, enc = ctx
    db = client.app.state.storage

    class Translator:
        def reason_json(self, system, user, schema, **kw):
            assert schema is Translation
            return Translation(english=["once a day" for _ in user.splitlines()])

    client.app.state.ai = Translator()
    med = db.get_entry(enc.entries[1].id)
    med.fields["frequency"] = "din me ek bar"
    db.save_entry(med)
    assert client.patch(f"/entries/{med.id}", json={"action": "confirm"}).status_code == 200
    done = db.get_entry(med.id)  # TestClient runs background tasks before returning
    assert done.fields["frequency"] == "once a day" and done.status.value == "confirmed"
    assert done.edits[-1].by == "system" and done.edits[-1].old == "din me ek bar"


def test_stop_only_for_confirmed_medicines(ctx):
    client, _, enc = ctx
    med = enc.entries[1]
    assert client.patch(f"/entries/{med.id}", json={"action": "stop"}).status_code == 409
    client.patch(f"/entries/{med.id}", json={"action": "confirm"})
    r = client.patch(f"/entries/{med.id}", json={"action": "stop"})
    assert r.json()["medication"]["stopped_on"] == TODAY.isoformat()


def test_record_shows_confirmed_only(ctx):
    client, patient, enc = ctx
    client.patch(f"/entries/{enc.entries[0].id}", json={"action": "confirm"})
    client.patch(f"/entries/{enc.entries[1].id}", json={"action": "reject"})
    record = client.get(f"/patients/{patient.id}/record").json()
    assert [f["latest"]["label"] for f in record["facts"]] == ["Penicillin"]
    (visit,) = record["visits"]
    assert [e["label"] for e in visit["entries"]] == ["Penicillin"]
    assert visit["pending"] == 3  # the other proposals still wait for review


def test_emergency_summary(ctx):
    client, patient, enc = ctx
    _confirm_all(client, enc)
    s = client.get(f"/patients/{patient.id}/emergency").json()
    assert s["patient"]["blood_group"] == "B+"
    assert [e["label"] for e in s["allergies"]] == ["Penicillin"]
    assert [(m["entry"]["label"], m["state"]) for m in s["medications"]] == [
        ("Azithromycin 500 mg", "current")  # the 400-day-old course has dropped off
    ]
    assert [e["label"] for e in s["conditions"]] == ["Type 2 diabetes"]  # not the sore throat


def test_manual_events_enter_confirmed_and_sort_on_timeline(ctx):
    client, patient, _ = ctx
    url = f"/patients/{patient.id}/events"
    for body in [
        {"kind": "emergency_visit", "label": "Asthma attack", "occurred_on": "2024-06"},
        {"kind": "surgery", "label": "Appendectomy", "occurred_on": "2015"},
        {"kind": "admission", "label": "Dengue", "occurred_on": "2025-08-14"},
    ]:
        r = client.post(url, json=body)
        assert r.status_code == 201 and r.json()["status"] == "confirmed"
        assert r.json()["unverified"] is True
    assert client.post(url, json={"kind": "allergy", "label": "Peanuts"}).status_code == 422
    assert client.post(url, json={"kind": "surgery", "label": "x",
                                  "occurred_on": "last year"}).status_code == 422

    timeline = client.get(f"/patients/{patient.id}/emergency").json()["timeline"]
    assert [e["label"] for e in timeline] == ["Appendectomy", "Dengue", "Asthma attack"]


def test_meta_names_both_steps(ctx):
    from app.schemas.account import Account, AccountRole
    from tests.conftest import sign_in_as

    client, _, _ = ctx
    assert client.get("/meta").status_code == 403  # Under the hood: admins only
    sign_in_as(client, Account(role=AccountRole.PATIENT, name="Admin (synthetic)", admin=True))
    m = client.get("/meta").json()
    assert m["perceive"]["provider"] == "nvidia" and "omni" in m["perceive"]["model"]
    assert m["reason"]["model"]


# ---- upload and processing --------------------------------------------------
def _upload_client(items):
    from tests.test_pipeline import FakeAI

    db = Storage()
    patient = Patient(name="Asha Verma (synthetic)")
    db.save_patient(patient)
    return api_client(create_app(db, FakeAI(items)), person=patient.id), patient


def test_upload_runs_the_pipeline_and_review_can_follow():
    from tests.test_pipeline import ITEMS, WAV_3S

    client, patient = _upload_client(ITEMS)
    r = client.post(
        f"/patients/{patient.id}/encounters",
        files=[("documents", ("rx.jpg", b"jpegbytes", "image/jpeg")),
               ("audio", ("consult.wav", WAV_3S, "audio/wav"))],
        data={"recording_consent": "true"},
    )
    assert r.status_code == 202
    assert r.json()["status"] == "processing"
    assert [s["filename"] for s in r.json()["sources"]] == ["rx.jpg", "consult.wav"]

    # TestClient runs background tasks before returning, so processing is already done.
    enc = client.get(f"/encounters/{r.json()['id']}").json()
    assert enc["status"] == "ready"
    assert [e["label"] for e in enc["entries"]] == ["Azithromycin 500 mg", "Penicillin",
                                                    "Appendectomy"]
    first = enc["entries"][0]["id"]
    assert client.patch(f"/entries/{first}", json={"action": "confirm"}).status_code == 200
    card = client.get(f"/patients/{patient.id}/emergency").json()
    assert [m["entry"]["label"] for m in card["medications"]] == ["Azithromycin 500 mg"]


def test_upload_with_typed_notes_only():
    from tests.test_pipeline import _item

    items = [_item(kind=EntryKind.ALLERGY, label="Sulfa drugs", source_id="notes1",
                   quote="allergic to sulfa drugs")]
    client, patient = _upload_client(items)
    r = client.post(f"/patients/{patient.id}/encounters",
                    data={"notes": "Patient is allergic to sulfa drugs."})
    assert r.status_code == 202
    assert client.get(f"/encounters/{r.json()['id']}").json()["entries"][0]["label"] == "Sulfa drugs"


def test_upload_rejects_bad_input():
    client, patient = _upload_client([])
    url = f"/patients/{patient.id}/encounters"
    assert client.post(url).status_code == 422  # nothing uploaded
    assert client.post(url, data={"notes": "   "}).status_code == 422
    wrong = client.post(url, files=[("audio", ("x.pdf", b"%PDF", "application/pdf"))],
                        data={"recording_consent": "true"})
    assert wrong.status_code == 415
    empty = client.post(url, files=[("documents", ("rx.jpg", b"", "image/jpeg"))])
    assert empty.status_code == 422
    too_many = [("documents", (f"{i}.jpg", b"x", "image/jpeg")) for i in range(7)]
    assert client.post(url, files=too_many).status_code == 413
    assert client.post("/patients/nope/encounters", data={"notes": "x"}).status_code == 404


def test_pdf_upload_uses_the_text_layer_and_reads_scanned_pages():
    from tests.test_pdf import REPORT, scanned_pdf, text_pdf
    from tests.test_pipeline import DOC, FakeAI, _item

    items = [_item(kind=EntryKind.TEST, label="Haemoglobin", source_id="doc1",
                   quote="Haemoglobin 11.2 g/dL", said_by="document")]
    ai = FakeAI(items)
    db = Storage()
    patient = Patient(name="Asha Verma (synthetic)")
    db.save_patient(patient)
    client = api_client(create_app(db, ai), person=patient.id)
    r = client.post(f"/patients/{patient.id}/encounters", files=[
        ("documents", ("report.pdf", text_pdf([REPORT]), "application/pdf")),
        ("documents", ("scan.pdf", scanned_pdf(2), "application/octet-stream")),  # by name
    ])
    assert r.status_code == 202
    assert [s["pages"] for s in r.json()["sources"]] == [1, 2]

    enc = client.get(f"/encounters/{r.json()['id']}").json()
    report, scan = enc["sources"]
    assert "Haemoglobin 11.2 g/dL" in report["text"]  # text layer, no model call
    assert scan["text"] == f"{DOC}\n\n{DOC}"  # two scanned pages, each read by the model
    assert [c[1] for c in ai.calls if c[0] == "perceive"] == ["image_url", "image_url"]
    assert "pdf text layer (no model)" in enc["models_used"]["reading_documents"]
    assert [e["label"] for e in enc["entries"]] == ["Haemoglobin"]


def test_pdf_upload_limits():
    from app.api.routes import MAX_PAGES_PER_VISIT
    from tests.test_pdf import REPORT, text_pdf

    client, patient = _upload_client([])
    url = f"/patients/{patient.id}/encounters"
    broken = client.post(url, files=[("documents", ("x.pdf", b"%PDF-1.4 nope", "application/pdf"))])
    assert broken.status_code == 422 and "could not be opened" in broken.json()["detail"]
    long = client.post(url, files=[("documents", ("x.pdf", text_pdf([REPORT] * 11), "application/pdf"))])
    assert long.status_code == 422 and "11 pages" in long.json()["detail"]
    ten = ("documents", ("x.pdf", text_pdf([REPORT] * 10), "application/pdf"))
    over = client.post(url, files=[ten, ten, ("documents", ("rx.jpg", b"x", "image/jpeg"))])
    assert over.status_code == 413 and str(MAX_PAGES_PER_VISIT) in over.json()["detail"]
    word = client.post(url, files=[("documents", ("x.docx", b"PK", "application/msword"))])
    assert word.status_code == 415


# ---- reading a visit again ----------------------------------------------------
def _read_visit(ai):
    from tests.test_pipeline import WAV_3S

    db = Storage()
    patient = Patient(name="Asha Verma (synthetic)")
    db.save_patient(patient)
    client = api_client(create_app(db, ai), person=patient.id)
    r = client.post(
        f"/patients/{patient.id}/encounters",
        files=[("documents", ("rx.jpg", b"jpegbytes", "image/jpeg")),
               ("audio", ("consult.wav", WAV_3S, "audio/wav"))],
        data={"recording_consent": "true"},
    )
    return client, db, r.json()


def _again(enc):
    from tests.test_pipeline import WAV_3S

    files = [("files", ("rx.jpg", b"jpegbytes", "image/jpeg")),
             ("files", ("consult.wav", WAV_3S, "audio/wav"))]
    return files, {"source_ids": [s["id"] for s in enc["sources"][:2]]}


def test_reread_from_the_browsers_copy():
    from tests.test_pipeline import CAREFUL_ITEMS, FakeAI

    ai = FakeAI(CAREFUL_ITEMS)
    client, _, enc = _read_visit(ai)
    files, data = _again(enc)
    r = client.post(f"/encounters/{enc['id']}/reread", files=files, data=data)
    assert r.status_code == 202
    after = client.get(f"/encounters/{enc['id']}").json()
    assert after["status"] == "ready"
    assert [s["careful"] for s in after["sources"]] == [False, False, True, True]
    assert ("image_url", True) in ai.thinking


def test_reread_rejects_files_that_do_not_belong():
    from tests.test_pipeline import ITEMS, FakeAI

    client, _, enc = _read_visit(FakeAI(ITEMS))
    url = f"/encounters/{enc['id']}/reread"
    files, data = _again(enc)
    assert client.post(url, files=files, data={"source_ids": ["nope", "nope"]}).status_code == 422
    assert client.post(url, files=files[:1], data=data).status_code == 422
    wrong_kind = [("files", ("consult.wav", b"RIFF", "audio/wav"))]
    first_is_image = {"source_ids": [enc["sources"][0]["id"]]}
    assert client.post(url, files=wrong_kind, data=first_is_image).status_code == 415
    plain = client.post(url, files=files, data={**data, "careful": "false"})
    assert plain.status_code == 409  # a visit that was read only gets a careful re-read


def test_a_failed_visit_can_be_tried_again():
    from tests.test_pipeline import ITEMS, FakeAI

    ai = FakeAI(ITEMS, fail_on="audio_url")
    client, _, enc = _read_visit(ai)
    assert client.get(f"/encounters/{enc['id']}").json()["status"] == "failed"
    ai.fail_on = None
    files, data = _again(enc)
    assert client.post(f"/encounters/{enc['id']}/reread", files=files, data=data).status_code == 202
    after = client.get(f"/encounters/{enc['id']}").json()
    assert after["status"] == "ready" and after["error"] is None
    assert len(after["sources"]) == 2 and len(after["entries"]) == 3


def test_entries_cannot_change_while_the_visit_is_read_again():
    from tests.test_pipeline import ITEMS, FakeAI

    client, db, enc = _read_visit(FakeAI(ITEMS))
    stored = db.get_encounter(enc["id"])
    stored.status = EncounterStatus.PROCESSING
    db.save_encounter(stored)
    entry = stored.entries[0].id
    assert client.patch(f"/entries/{entry}", json={"action": "confirm"}).status_code == 409


def test_a_recording_needs_the_doctors_agreement():
    from tests.test_pipeline import ITEMS, WAV_3S

    client, patient = _upload_client(ITEMS)
    url = f"/patients/{patient.id}/encounters"
    audio = [("audio", ("consult.wav", WAV_3S, "audio/wav"))]
    refused = client.post(url, files=audio)
    assert refused.status_code == 422 and "agreement" in refused.json()["detail"]
    ok = client.post(url, files=audio, data={"recording_consent": "true"})
    assert ok.status_code == 202
    assert ok.json()["sources"][0]["recording_consent"] is True
    # Photos and typed notes need no such tick.
    assert client.post(url, data={"notes": "Fever for two days."}).status_code == 202
