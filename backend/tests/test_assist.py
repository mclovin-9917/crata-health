"""Before a visit and Ask about my health. Nothing here reaches a real model or a real Tavily."""

import io
import wave
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from app.ai.client import AIClient, AIError, audio_part
from app.ai.search import SearchError, SearchResult
from app.assist import (
    ASKS_PER_DAY,
    DOSE,
    HEALTH_DOMAINS,
    RECORD_NONE_NOTE,
    SPECIALTIES,
    _Answer,
    _AskPoint,
    _clean_sentence,
    _FollowUp,
    _FollowUps,
    _Plan,
    _Prep,
    _RecordAnswer,
    _Summary,
    _Symptom,
    check_search_words,
    considered_conditions,
    warning_signs,
)
from app.config import Settings
from app.main import create_app
from app.schemas.assist import PrepRecord
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    Entry,
    EntryKind,
    EntryStatus,
    Medication,
    TestResult,
)
from app.storage import Storage
from tests.conftest import api_client

PID = "p-asha"
TODAY = date.today()

RESULTS = [
    SearchResult(title="Diabetes and diet - NHS", url="https://www.nhs.uk/diabetes-diet",
                 content="Rice is a starchy food that raises blood sugar. Brown rice has more "
                         "fibre than white rice. Eat smaller portions of starchy food."),
    SearchResult(title="Rice - MedlinePlus", url="https://medlineplus.gov/rice.html",
                 content="Whole grains help you feel full for longer. Take 500 mg of fibre."),
]


def _plan(words="diabetes rice", kind="general"):
    return _Plan(kind=kind, search_words=words)


def _pt(text, source, quote, category="good_to_know"):
    return _AskPoint(text=text, source=source, quote=quote, category=category)


ANSWER = _Answer(points=[
    _pt("Rice is a starchy food that raises blood sugar.", 1, "Rice is a starchy food",
        "your_conditions"),
    _pt("Brown rice has more fibre than white rice.", 1, "Brown rice has more fibre",
        "be_careful"),
    _pt("Rice is good for everyone.", 1, "good for everyone"),  # not in the source
    _pt("Made-up source.", 9, "anything"),  # no source 9
    _pt("Stop eating white rice.", 1, "Eat smaller portions of starchy food"),  # directive
    _pt("Whole grains help people feel full for longer.", 2, "feel full for longer", "wizard"),
    _pt("Fibre helps at 500 mg.", 2, "500 mg of fibre"),  # names a dose
])

PREP = _Prep(
    symptoms=[_Symptom(text="Cough for a week", quote="cough"),
              _Symptom(text="Fever", quote="a high temperature")],  # not typed
    specialty="lungs", reason="Chest doctors see lasting coughs.",
    questions=["Could this be an infection?", "Should I stop my medicine?",
               "What tests might I need?", "How long will it last?", "Is it catching?"])


class FakeAI:
    """Answers by what is asked for; counts calls."""

    def __init__(self, plan=None, answer=ANSWER, prep=PREP, fail=False, follow=None,
                 record=None, summary=None, fail_on=()):
        self.plan, self.answer, self.prep, self.fail = plan or _plan(), answer, prep, fail
        self.follow = follow or _FollowUps(questions=[
            _FollowUp(text="Where exactly is it?", choices=["Left", "Right", "Both", "Not sure",
                                                           "Other"]),
            _FollowUp(text="Is there a fever?", choices=[]),
            _FollowUp(text="Should I stop my tablets?", choices=["Yes"]),
            _FollowUp(text="Since when?", choices=["Stop now", "2 days"]),
            _FollowUp(text="Fourth?", choices=[])])
        self.record = record or _RecordAnswer(item_ids=["r1"], summary="You take one medicine.")
        self.summary, self.fail_on = summary or _Summary(summary="Rice raises blood sugar."), fail_on
        self.calls, self.used, self.kw, self.users = [], [], [], []
        self.voice, self.voice_calls = "I have a cough", []

    def transcribe(self, wav, instruction, **kw):
        self.voice_calls.append((len(wav), instruction))
        if self.fail:
            raise AIError("busy")
        self.used.append("nvidia:omni")
        return self.voice

    def reason_json(self, system, user, schema, **kw):
        self.calls.append(schema.__name__)
        self.kw.append(kw)
        self.users.append(user)
        if self.fail or schema.__name__ in self.fail_on:
            raise AIError("busy")
        self.used.append("nvidia:super")
        return {"_Plan": self.plan, "_Answer": self.answer, "_Prep": self.prep,
                "_FollowUps": self.follow, "_RecordAnswer": self.record,
                "_Summary": self.summary}[schema.__name__]


class FakeSearch:
    def __init__(self, results=RESULTS, fail=False):
        self.results, self.fail, self.available, self.queries = results, fail, True, []

    def search(self, query, domains, max_results=6):
        self.queries.append((query, domains, max_results))
        if self.fail:
            raise SearchError("Tavily answered HTTP 429")
        return self.results


def _world(**ai_kw):
    db = Storage()
    db.save_patient(Patient(id=PID, name="Asha Verma (synthetic)", birth_year=1980, sex="female"))

    def confirm(**kw):
        e = Entry(patient_id=PID, **kw)
        e.set_status(EntryStatus.CONFIRMED, by="system")
        db.save_entry(e)

    confirm(kind=EntryKind.ALLERGY, label="Peanut allergy")
    confirm(kind=EntryKind.MEDICATION, label="Metformin 500 mg",
            medication=Medication(ongoing=True))
    confirm(kind=EntryKind.MEDICATION, label="Old antibiotic", medication=Medication(
        started_on=TODAY - timedelta(days=60), duration_days=5))
    confirm(kind=EntryKind.DIAGNOSIS, label="Type 2 diabetes")
    confirm(kind=EntryKind.SURGERY, label="Appendectomy", occurred_on=str(TODAY.year))
    confirm(kind=EntryKind.SURGERY, label="Tonsillectomy", occurred_on="2001")
    confirm(kind=EntryKind.RESULT, label="HbA1c", result=TestResult(
        value=8.2, unit="%", ref_high=5.6, measured_on=TODAY))
    ai, search = FakeAI(**ai_kw), FakeSearch()
    return db, ai, search, api_client(create_app(db, ai, search), person=PID)


# ---- warning signs --------------------------------------------------------------------------
@pytest.mark.parametrize("text, label", [
    ("I have chest pain since morning", "Chest pain or pressure"),
    ("there is pressure in my chest", "Chest pain or pressure"),
    ("seene mein dard ho raha hai", "Chest pain or pressure"),
    ("I can't breathe properly", "Trouble breathing"),
    ("saans lene mein takleef hai", "Trouble breathing"),
    ("she fainted twice today", "Fainting or passing out"),
    ("his face is drooping on one side", "Face drooping"),
    ("slurred speech since last night", "Slurred speech or trouble speaking"),
    ("weakness on the left side of my body", "Weakness or numbness on one side"),
    ("my father had a seizure", "Seizure"),
    ("heavy bleeding that won't stop", "Severe bleeding"),
    ("I am coughing up blood", "Coughing or vomiting blood"),
    ("khansi mein khoon aa raha hai", "Coughing or vomiting blood"),
    ("swelling of the throat after eating", "Swelling of the face or throat"),
    ("I want to die", "Thoughts of self-harm"),
    ("a severe head injury from the fall", "Severe head injury"),
    ("I am pregnant and there is some bleeding", "Bleeding in pregnancy"),
    ("fever since two days and a stiff neck", "High fever with a stiff neck"),
])
def test_clear_warning_signs_match(text, label):
    assert label in warning_signs(text)


@pytest.mark.parametrize("text", [
    "a runny nose and a mild cough for three days",
    "I have a cold and a sore throat",
    "headache after a long day, slept badly",
    "no chest pain, only a stomach ache",
    "I do not have trouble breathing",
    "chest pain nahi hai, bas bukhar hai",
    "a stiff neck from sleeping badly",  # no fever: not this sign
])
def test_plain_complaints_and_denials_do_not_match(text):
    assert warning_signs(text) == []


@pytest.mark.parametrize("text", [
    "I can't sleep because of chest pain",
    "no fever but chest pain",
    "I have no appetite and chest pain",
    "no fever and chest pain",
    "I have never had chest pain like this before",
    "never felt so breathless",
    "I can't walk without chest pain",
    "chest pain that will not go away",
    "chest pain that does not stop",
    "I have chest pain and can not sleep",
    "heavy bleeding that will not stop",
    "she fainted and is not responding",
    "seene mein dard nahi ja raha",
    "bahut khoon nahi ruk raha",
    "not only chest pain but also sweating",
])
def test_a_negation_not_right_in_front_does_not_hide_the_sign(text):
    assert warning_signs(text)


@pytest.mark.parametrize("text", [
    "no chest pain",
    "chest pain nahi hai",
    "I do not have chest pain",
    "a cold and runny nose",
    "he denies chest pain",
    "no real chest pain",
    "not breathless",
])
def test_direct_denials_do_not_match(text):
    assert warning_signs(text) == []


@pytest.mark.parametrize("text, label", [
    ("I don't want to live anymore", "Thoughts of self-harm"),
    ("there is no reason to live", "Thoughts of self-harm"),
    ("they would be better off dead", "Thoughts of self-harm"),
    ("I want to end my life", "Thoughts of self-harm"),
    ("I am not able to breathe", "Trouble breathing"),
    ("breathing difficulty since night", "Trouble breathing"),
    ("she is not breathing", "Trouble breathing"),
    ("his lips turning blue", "Trouble breathing"),
    ("my chest feels tight", "Chest pain or pressure"),
    ("my chest is hurting", "Chest pain or pressure"),
    ("I cannot stop the bleeding", "Severe bleeding"),
    ("I can not stop bleeding", "Severe bleeding"),
    ("I felt faint", "Fainting or passing out"),
    ("sudden worst headache of my life", "Sudden, very bad headache"),
    ("worst headache ever", "Sudden, very bad headache"),
])
def test_more_wordings(text, label):
    assert label in warning_signs(text)


def test_self_harm_carries_a_helpline():
    _, _, _, client = _world()
    body = client.post(f"/patients/{PID}/visit-preps",
                       json={"symptoms": "I want to end my life"}).json()
    assert body["helpline"] == "Tele-MANAS: call 14416, free, any time."
    ans = _ask(client, "I don't want to live anymore").json()
    assert ans["urgent"] and ans["helpline"].startswith("Tele-MANAS")
    assert _ask(client, "Can I eat rice?").json()["helpline"] is None


# ---- before a visit -------------------------------------------------------------------------
def test_prep_is_assembled_by_code_and_saved():
    db, ai, _, client = _world()
    r = client.post(f"/patients/{PID}/visit-preps",
                    json={"symptoms": "I have had a cough for a week", "since": "a week"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "patient_id", "created_at", "text", "since", "urgent",
                         "warning_signs", "symptoms", "specialty", "specialty_label", "reason",
                         "questions", "answers", "record", "models_used", "helpline"}
    assert body["text"] == "I have had a cough for a week" and body["since"] == "a week"
    assert body["urgent"] is False and body["warning_signs"] == []
    # The quote check keeps only what was typed.
    assert body["symptoms"] == [{"text": "Cough for a week", "quote": "cough"}]
    assert body["specialty"] == "lungs"
    assert body["specialty_label"] == SPECIALTIES["lungs"]
    assert body["reason"] == "Chest doctors see lasting coughs."
    # "Should I stop my medicine?" is dropped; at most three questions.
    assert body["questions"] == ["Could this be an infection?", "What tests might I need?",
                                 "How long will it last?"]
    assert set(body["record"]) == {"allergies", "medicines", "conditions", "events",
                                   "out_of_range", "tests_out_of_range"}
    assert body["record"]["tests_out_of_range"] == [{
        "name": "HbA1c", "value": 8.2, "unit": "%", "date": TODAY.isoformat(),
        "direction": "high", "range_text": "up to 5.6"}]
    assert body["record"]["allergies"] == ["Peanut allergy"]
    assert body["record"]["medicines"] == ["Metformin 500 mg"]  # the finished course is not
    assert body["record"]["conditions"] == ["Type 2 diabetes"]
    assert body["record"]["events"] == [f"Appendectomy ({TODAY.year})"]  # not the 2001 one
    assert len(body["record"]["out_of_range"]) == 1
    assert body["record"]["out_of_range"][0].startswith("HbA1c 8.2 %")
    assert "high" in body["record"]["out_of_range"][0]
    assert body["models_used"] == ["nvidia:super"] and body["answers"] == []
    assert ai.calls == ["_Prep"] and ai.kw[0]["thinking"] is False
    assert db.get_visit_prep(body["id"]) is not None


def test_prep_lists_gets_and_deletes():
    _db, _, _, client = _world()
    first = client.post(f"/patients/{PID}/visit-preps", json={"symptoms": "cough"}).json()
    second = client.post(f"/patients/{PID}/visit-preps", json={"symptoms": "cough again"}).json()
    listed = client.get(f"/patients/{PID}/visit-preps").json()
    assert [p["id"] for p in listed] == [second["id"], first["id"]]  # newest first
    assert client.get(f"/visit-preps/{first['id']}").json()["text"] == "cough"
    assert client.delete(f"/visit-preps/{first['id']}").status_code == 204
    assert client.get(f"/visit-preps/{first['id']}").status_code == 404
    assert [p["id"] for p in client.get(f"/patients/{PID}/visit-preps").json()] == [second["id"]]


def test_prep_input_limits():
    _, _, _, client = _world()
    url = f"/patients/{PID}/visit-preps"
    assert client.post(url, json={"symptoms": ""}).status_code == 422
    assert client.post(url, json={"symptoms": "x" * 2001}).status_code == 422
    assert client.post(url, json={"symptoms": "cough", "since": "y" * 81}).status_code == 422
    assert client.post(url, json={"symptoms": "cough", "since": None}).status_code == 200


def test_warning_signs_lead_the_prep_but_it_is_still_made():
    _, _, _, client = _world()
    body = client.post(f"/patients/{PID}/visit-preps",
                       json={"symptoms": "cough and chest pain since today"}).json()
    assert body["urgent"] is True and body["warning_signs"] == ["Chest pain or pressure"]


def test_unknown_specialty_and_a_directive_reason_are_replaced():
    prep = _Prep(specialty="wizard", reason="Please stop taking your tablets.",
                 symptoms=[], questions=[])
    _, _, _, client = _world(prep=prep)
    body = client.post(f"/patients/{PID}/visit-preps", json={"symptoms": "cough"}).json()
    assert body["specialty"] == "general_physician"
    assert body["specialty_label"] == "General physician"
    assert body["reason"] == "This kind of doctor can look into what you describe."


def test_prep_502_when_the_model_fails():
    db, _, _, client = _world(fail=True)
    r = client.post(f"/patients/{PID}/visit-preps", json={"symptoms": "cough"})
    assert r.status_code == 502
    assert db.list_visit_preps(PID) == []


def test_a_patients_preps_are_removed_with_the_demo_reset():
    db, _, _, client = _world()
    client.post(f"/patients/{PID}/visit-preps", json={"symptoms": "cough"})
    db.clear_people([PID])
    assert db.list_visit_preps(PID) == []


# ---- search words ---------------------------------------------------------------------------
def test_search_words_check():
    name = "Asha Verma (synthetic)"
    assert check_search_words("diabetes breakfast", name)
    assert not check_search_words("", name)
    assert not check_search_words("diabetes 2 breakfast", name)  # digits
    assert not check_search_words("asha diabetes", name)  # a part of the name
    assert not check_search_words("diet for Verma", name)
    assert not check_search_words("one two three four five six seven eight nine", name)  # > 8
    assert check_search_words("one two three four five six seven eight", name)
    assert not check_search_words("a " * 80, name)  # too long


@pytest.mark.parametrize("text", [
    "Do not take ibuprofen with this.", "Avoid taking aspirin.", "Take two tablets a day.",
    "Use 1 g of salt", "Change your dose slowly.", "Skip the tablet.",
])
def test_medicine_instructions_and_doses_are_dropped(text):
    assert not _clean_sentence(text)
    assert _clean_sentence("Brown rice has more fibre than white rice.")


def test_instruction_points_are_dropped_from_an_answer():
    ans = _Answer(points=[
        _pt("Do not take ibuprofen with this.", 1, "Rice is a starchy food"),
        _pt("Avoid taking aspirin.", 1, "Rice is a starchy food"),
        _pt("Take two tablets a day.", 1, "Rice is a starchy food"),
        _pt("Rice is a starchy food.", 1, "Rice is a starchy food")])
    _, _, _, client = _world(answer=ans)
    assert [p["text"] for p in _ask(client).json()["points"]] == ["Rice is a starchy food."]


def test_considered_uses_everyday_words():
    assert considered_conditions(["Hypertension"], "salt high blood pressure") == ["Hypertension"]
    assert considered_conditions(["Type 2 diabetes"], "rice blood sugar") == ["Type 2 diabetes"]
    assert considered_conditions(["Hypothyroidism"], "thyroid diet") == ["Hypothyroidism"]
    assert considered_conditions(["Hypertension"], "rice breakfast") == []


def test_prep_limit_is_20_per_account():
    _, _, _, client = _world()
    url = f"/patients/{PID}/visit-preps"
    for _i in range(20):
        assert client.post(url, json={"symptoms": "cough"}).status_code == 200
    r = client.post(url, json={"symptoms": "cough"})
    assert r.status_code == 429
    assert r.json()["detail"] == "You've made 20 summaries today. Try again tomorrow."
    # asks are counted separately
    assert _ask(client).status_code == 200


def test_a_dose_is_recognised():
    assert DOSE.search("take 500 mg") and DOSE.search("twice daily")
    assert not DOSE.search("brown rice has more fibre")


# ---- Ask ------------------------------------------------------------------------------------
def _ask(client, q="Can I eat rice with diabetes?"):
    return client.post(f"/patients/{PID}/ask", json={"question": q})


def test_ask_keeps_only_points_the_source_backs():
    _db, ai, search, client = _world()
    r = _ask(client)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"urgent", "warning_signs", "health_question", "points", "sources",
                         "allergy_notes", "considered", "note", "models_used", "helpline",
                         "kind", "record_items", "record_summary", "summary", "charts"}
    assert body["kind"] == "general" and body["record_items"] == []
    assert body["record_summary"] is None and body["summary"] == "Rice raises blood sugar."
    assert [p["text"] for p in body["points"]] == [
        "Rice is a starchy food that raises blood sugar.",
        "Brown rice has more fibre than white rice.",
        "Whole grains help people feel full for longer."]
    first = body["points"][0]
    assert set(first) == {"text", "quote", "site", "title", "url", "category", "source"}
    assert [p["category"] for p in body["points"]] == ["your_conditions", "be_careful",
                                                       "good_to_know"]  # unknown -> good_to_know
    assert [p["source"] for p in body["points"]] == [1, 1, 2]
    assert body["sources"] == [
        {"n": 1, "site": "NHS", "title": RESULTS[0].title, "url": RESULTS[0].url},
        {"n": 2, "site": "MedlinePlus", "title": RESULTS[1].title, "url": RESULTS[1].url}]
    assert first["site"] == "NHS" and first["url"] == RESULTS[0].url
    assert first["quote"] == "Rice is a starchy food"
    assert body["points"][2]["site"] == "MedlinePlus"
    assert body["urgent"] is False and body["health_question"] is True
    assert body["considered"] == ["Type 2 diabetes"]
    assert body["note"].startswith("General information from trusted health sites")
    assert body["models_used"] == ["nvidia:super", "tavily:search"]
    ((query, domains, _),) = search.queries
    assert query == "diabetes rice diet health" and domains == HEALTH_DOMAINS
    assert ai.calls == ["_Plan", "_Answer", "_Summary"]
    assert ai.kw[0]["thinking"] is False and ai.kw[0]["temperature"] == 0.0
    # The summary is written from the kept points only, never from the sources.
    assert "Brown rice has more fibre than white rice." in ai.users[2]
    assert "Rice is good for everyone" not in ai.users[2] and "nhs.uk" not in ai.users[2]


def test_ask_notes_an_allergy_named_in_the_question():
    _, _, _, client = _world()
    body = _ask(client, "Are peanuts bad for my cholesterol?").json()
    assert body["allergy_notes"] == ["Your record lists an allergy to Peanut."]
    assert _ask(client, "Can I eat rice?").json()["allergy_notes"] == []


def test_a_saved_search_makes_no_second_tavily_call():
    _db, _, search, client = _world()
    first = _ask(client).json()
    again = _ask(client, "Is rice fine for diabetes?").json()  # same words from the model
    assert len(search.queries) == 1
    assert first["models_used"] == ["nvidia:super", "tavily:search"]
    assert again["models_used"] == ["nvidia:super"]  # a saved search adds no tavily mark
    assert again["points"]


def test_an_old_saved_search_is_made_again():
    db, _, search, client = _world()
    _ask(client)
    key = next(iter(db._conn.execute("SELECT id FROM search_cache").fetchone()))
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    db._conn.execute("UPDATE search_cache SET fetched_at = ? WHERE id = ?", (old, key))
    _ask(client)
    assert len(search.queries) == 2


def test_urgent_ask_makes_no_model_or_search_call_and_uses_no_question():
    db, ai, search, client = _world()
    body = _ask(client, "I have chest pain, what should I eat?").json()
    assert body["urgent"] is True and body["warning_signs"] == ["Chest pain or pressure"]
    assert body["points"] == [] and "112" in body["note"]
    assert ai.calls == [] and search.queries == []
    assert db.count_asks(client.app.state.storage.account_by_person(PID).id,
                         datetime.now(UTC) - timedelta(days=1)) == 0


def test_not_a_health_question_makes_no_search():
    _, ai, search, client = _world(plan=_plan(words="", kind="not_health"))
    body = _ask(client, "Who won the cricket match?").json()
    assert body["health_question"] is False and body["points"] == []
    assert body["kind"] == "not_health"
    assert search.queries == [] and ai.calls == ["_Plan"]


@pytest.mark.parametrize("words", ["asha diabetes", "diabetes 2 breakfast", ""])
def test_unsafe_search_words_make_no_search(words):
    _, ai, search, client = _world(plan=_plan(words=words))
    body = _ask(client).json()
    assert body["note"] == "Try asking in more general words." and body["points"] == []
    assert search.queries == [] and ai.calls == ["_Plan"]


def test_daily_limit_is_20_per_account():
    _db, _, _, client = _world()
    for _i in range(ASKS_PER_DAY):
        assert _ask(client).status_code == 200
    r = _ask(client)
    assert r.status_code == 429
    assert r.json()["detail"] == "You've asked 20 questions today. Try again tomorrow."
    # Another account is not affected by this one's questions.
    other = api_client(client.app, person=PID)  # a new account for the same person: own count
    assert _ask(other).status_code == 200


def test_ask_502_when_the_model_or_tavily_fails():
    assert _ask(_world(fail=True)[3]).status_code == 502
    _db, _ai, search, client = _world()
    search.fail = True
    assert _ask(client).status_code == 502


def test_no_trusted_answer_gives_a_plain_note():
    db, ai, search, client = _world()
    search.results = []
    body = _ask(client).json()
    assert body["points"] == [] and "did not give a clear answer" in body["note"]
    assert ai.calls == ["_Plan"]  # nothing to summarise
    assert db.get_search_cache("diabetes rice diet health|" + ",".join(sorted(HEALTH_DOMAINS)),
                               datetime.now(UTC) - timedelta(days=1)) is None  # not kept


def test_forum_pages_are_not_used_as_sources():
    _db, ai, search, client = _world()
    search.results = [SearchResult(title="Forum", url="https://connect.mayoclinic.org/discussion/x",
                                   content="Rice is a starchy food")]
    body = _ask(client).json()
    assert body["points"] == [] and ai.calls == ["_Plan"]


def test_ask_input_limits():
    _, _, _, client = _world()
    assert _ask(client, "").status_code == 422
    assert _ask(client, "x" * 501).status_code == 422


# ---- Ask: categories, sources, follow-up ----------------------------------------------------
def test_your_conditions_needs_a_confirmed_condition():
    db, _, _, client = _world()
    for e in db.list_entries(PID):
        if e.kind == EntryKind.DIAGNOSIS:
            e.set_status(EntryStatus.PROPOSED, by="system")
            db.save_entry(e)
    body = _ask(client).json()
    assert [p["category"] for p in body["points"]] == ["good_to_know", "be_careful",
                                                       "good_to_know"]


def test_your_conditions_point_must_name_a_condition():
    ans = _Answer(points=[
        _pt("Whole grains help people feel full for longer.", 2, "feel full for longer",
            "your_conditions"),  # names no condition
        _pt("Rice is a starchy food that raises blood sugar.", 1, "Rice is a starchy food",
            "your_conditions"),  # blood sugar: Type 2 diabetes
    ])
    _, _, _, client = _world(answer=ans)
    body = _ask(client).json()
    assert [p["category"] for p in body["points"]] == ["good_to_know", "your_conditions"]


def test_sources_are_unique_by_url_in_first_use_order():
    ans = _Answer(points=[_pt("Whole grains help people feel full for longer.", 2,
                              "feel full for longer"),
                          _pt("Brown rice has more fibre than white rice.", 1,
                              "Brown rice has more fibre"),
                          _pt("Rice is a starchy food that raises blood sugar.", 1,
                              "Rice is a starchy food")])
    _, _, _, client = _world(answer=ans)
    body = _ask(client).json()
    assert [(s["n"], s["url"]) for s in body["sources"]] == [(1, RESULTS[1].url),
                                                             (2, RESULTS[0].url)]
    assert [p["source"] for p in body["points"]] == [1, 2, 2]


def test_a_follow_up_sends_the_previous_question_as_context_only():
    _db, ai, _, client = _world()
    prev = {"question": "Can I eat rice with diabetes?", "points": ["Rice raises blood sugar."]}
    r = client.post(f"/patients/{PID}/ask", json={"question": "what about mango?",
                                                  "previous": prev})
    assert r.status_code == 200
    assert "Previous question (context only): Can I eat rice with diabetes?" in ai.users[0]
    assert "- Rice raises blood sugar." in ai.users[0]
    assert "Question: what about mango?" in ai.users[0]
    assert "Previous question (context only)" in ai.users[1]
    _db, ai, _, client = _world()  # without it, no previous question in the prompts
    _ask(client)
    assert all("Previous question" not in u for u in ai.users)


def test_a_follow_up_still_checks_warning_signs_and_the_limit():
    _db, ai, search, client = _world()
    prev = {"question": "Can I eat rice?", "points": []}
    body = client.post(f"/patients/{PID}/ask", json={"question": "what about chest pain?",
                                                     "previous": prev}).json()
    assert body["urgent"] is True and ai.calls == [] and search.queries == []
    for _i in range(ASKS_PER_DAY):
        assert client.post(f"/patients/{PID}/ask", json={"question": "and mango?",
                                                         "previous": prev}).status_code == 200
    assert client.post(f"/patients/{PID}/ask", json={"question": "and mango?",
                                                     "previous": prev}).status_code == 429


@pytest.mark.parametrize("previous", [
    {"question": "q" * 501, "points": []},
    {"question": "q", "points": ["p"] * 6},
    {"question": "q", "points": ["p" * 401]},
])
def test_previous_limits(previous):
    _, _, _, client = _world()
    r = client.post(f"/patients/{PID}/ask", json={"question": "mango?", "previous": previous})
    assert r.status_code == 422
    assert client.post(f"/patients/{PID}/ask", json={"question": "mango?",
                                                     "previous": None}).status_code == 200


def test_an_older_saved_prep_without_the_new_field_loads_with_none():
    rec = PrepRecord.model_validate({"allergies": [], "out_of_range": ["HbA1c 8.2 % high"]})
    assert rec.tests_out_of_range == [] and rec.out_of_range == ["HbA1c 8.2 % high"]


# ---- voice input ----------------------------------------------------------------------------
def _wav(seconds, rate=16_000, level=0.2):
    t = np.arange(int(seconds * rate)) / rate
    pcm = (np.sin(2 * np.pi * 300 * t) * level * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _voice(client, data=None, mime="audio/wav"):
    data = _wav(2) if data is None else data
    return client.post(f"/patients/{PID}/voice", files={"audio": ("v.wav", data, mime)})


def test_voice_returns_what_was_said_and_stores_nothing():
    db, ai, _, client = _world()
    r = _voice(client)
    assert r.status_code == 200
    assert r.json() == {"text": "I have a cough", "models_used": ["nvidia:omni"]}
    assert "exactly what the speaker says" in ai.voice_calls[0][1]
    account = client.app.state.storage.account_by_person(PID)
    assert db.count_asks(f"voice:{account.id}", datetime.now(UTC) - timedelta(days=1)) == 1


def test_voice_chunks_are_joined_and_silence_gives_empty_text():
    _, ai, _, client = _world()
    assert _voice(client, _wav(70)).json()["text"] == "I have a cough I have a cough"
    assert len(ai.voice_calls) == 2
    ai.voice = ""
    assert _voice(client).json()["text"] == ""


def test_voice_limits_422_413_415():
    _, ai, _, client = _world()
    too_long = _voice(client, _wav(100, rate=8000))  # 100 s, under 3 MB
    assert too_long.status_code == 422 and "95 s" in too_long.json()["detail"]
    assert _voice(client, b"not audio").status_code == 422
    big = _voice(client, b"\0" * (3 * 1024 * 1024 + 1))
    assert big.status_code == 413 and "3 MB" in big.json()["detail"]
    assert _voice(client, _wav(1), mime="text/plain").status_code == 415
    assert ai.voice_calls == []  # nothing reached a model


def test_voice_upload_over_1_mb_never_reaches_the_disk(monkeypatch):
    import tempfile

    rolled = []
    real = tempfile.SpooledTemporaryFile.rollover
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover",
                        lambda self: (rolled.append(1), real(self))[1])
    _, _, _, client = _world()
    data = _wav(48)  # about 1.5 MB, over Starlette's default 1 MB spool size
    assert len(data) > 1024 * 1024
    r = _voice(client, data)
    assert r.status_code == 200
    assert rolled == []  # no temporary file was made


def test_every_upload_limit_fits_in_the_in_memory_spool():
    from starlette.formparsers import MultiPartParser

    from app.api.assist import VOICE_MAX_BYTES
    from app.api.routes import MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, MAX_PDF_BYTES

    assert MultiPartParser.spool_max_size > max(
        VOICE_MAX_BYTES, MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, MAX_PDF_BYTES)


def test_a_too_long_recording_is_refused_without_a_full_decode(monkeypatch):
    from app.media import audio as audio_mod

    # Container length known: refused before any frame is decoded.
    decoded = []
    real = audio_mod.av.AudioResampler.resample
    monkeypatch.setattr(audio_mod.av.AudioResampler, "resample",
                        lambda self, f: (decoded.append(1), real(self, f))[1])
    with pytest.raises(audio_mod.AudioError, match="95 s"):
        audio_mod.decode(_wav(300, rate=8000), max_seconds=95)
    assert decoded == []
    # No usable length (forced here): decoding stops soon after passing the limit.
    monkeypatch.setattr(audio_mod.av, "time_base", 10**12)
    with pytest.raises(audio_mod.AudioError, match="too long"):
        audio_mod.decode(_wav(300, rate=8000), max_seconds=95)
    assert 0 < len(decoded) < 300 * 8000 / 1024


def test_voice_is_counted_before_decoding():
    _, _, _, client = _world()
    for _i in range(30):
        assert _voice(client, b"not audio").status_code == 422  # bad files count too
    r = _voice(client)
    assert r.status_code == 429


def test_voice_limit_is_30_a_day_per_account():
    _, _, _, client = _world()
    for _i in range(30):
        assert _voice(client).status_code == 200
    r = _voice(client)
    assert r.status_code == 429
    assert r.json()["detail"] == "You've used voice 30 times today. Please type instead."
    assert _ask(client).status_code == 200  # asks are counted separately


def test_voice_502_when_the_model_fails():
    _, _, _, client = _world(fail=True)
    r = _voice(client)
    assert r.status_code == 502
    assert r.json()["detail"] == "Couldn't hear that. Please type it instead."


# ---- AIClient.transcribe: Nano Omni only ----------------------------------------------------
class _Chat:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = self.completions = self

    def create(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        msg = type("M", (), {"content": reply})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


def test_transcribe_uses_omni_with_thinking_off_and_no_backup():
    keys = {"nvidia_api_key": "nv", "nebius_api_key": "nb"}
    primary, backup = _Chat([RuntimeError("hung")]), _Chat(["never"])
    ai = AIClient(Settings(_env_file=None, **keys), client=primary, backup_client=backup)
    with pytest.raises(AIError):
        ai.transcribe(b"wav", "write it")
    assert backup.calls == []
    primary.replies = ["hello", ""]
    assert ai.transcribe(b"wav", "write it") == "hello"
    call = primary.calls[-1]
    assert call["temperature"] == 0.0
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert call["messages"][0]["content"][0] == audio_part(b"wav")
    assert ai.transcribe(b"wav", "write it") == ""  # silence is allowed


# ---- one box, follow-up questions -----------------------------------------------------------
def _questions(client, text="my stomach hurts"):
    return client.post(f"/patients/{PID}/visit-preps/questions", json={"symptoms": text})


def test_follow_up_questions_are_filtered_and_numbered():
    db, ai, _, client = _world()
    r = _questions(client)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"urgent", "warning_signs", "helpline", "questions"}
    assert body["urgent"] is False and body["warning_signs"] == [] and body["helpline"] is None
    # "Should I stop my tablets?" is dropped; at most 3; choices capped at 4 and filtered.
    assert body["questions"] == [
        {"id": "q1", "text": "Where exactly is it?", "choices": ["Left", "Right", "Both",
                                                                   "Not sure"]},
        {"id": "q2", "text": "Is there a fever?", "choices": []},
        {"id": "q3", "text": "Since when?", "choices": ["2 days"]}]  # "Stop now" dropped
    assert ai.calls == ["_FollowUps"]
    account = client.app.state.storage.account_by_person(PID)
    assert db.count_asks(f"prepq:{account.id}", datetime.now(UTC) - timedelta(days=1)) == 1


def test_follow_up_questions_urgent_makes_no_model_call_and_uses_no_limit():
    db, ai, _, client = _world()
    body = _questions(client, "I have chest pain").json()
    assert body["urgent"] is True and body["warning_signs"] == ["Chest pain or pressure"]
    assert body["questions"] == [] and ai.calls == []
    account = client.app.state.storage.account_by_person(PID)
    assert db.count_asks(f"prepq:{account.id}", datetime.now(UTC) - timedelta(days=1)) == 0
    assert _questions(client, "I want to end my life").json()["helpline"].startswith("Tele-MANAS")


def test_follow_up_questions_model_failure_gives_none_not_an_error():
    _, _, _, client = _world(fail=True)
    r = _questions(client)
    assert r.status_code == 200 and r.json()["questions"] == []


def test_follow_up_questions_limit_is_40_per_account():
    _, _, _, client = _world()
    for _i in range(40):
        assert _questions(client).status_code == 200
    r = _questions(client)
    assert r.status_code == 429
    assert r.json()["detail"] == "You've asked for questions 40 times today. Try again tomorrow."
    assert client.post(f"/patients/{PID}/visit-preps", json={"symptoms": "cough"}).status_code == 200


def test_follow_up_questions_input_limits():
    _, _, _, client = _world()
    assert _questions(client, "").status_code == 422
    assert _questions(client, "x" * 2001).status_code == 422


def test_prep_needs_no_since_and_reads_it_from_the_text():
    prep = _Prep(symptoms=[], specialty="lungs", reason="Chest doctors see lasting coughs.",
                 since="about a week", since_quote="for a week")
    _, ai, _, client = _world(prep=prep)
    body = client.post(f"/patients/{PID}/visit-preps",
                       json={"symptoms": "I have had a cough for a week"}).json()
    assert body["since"] == "about a week"
    assert "Since when" not in ai.users[0]
    # A quote that is not in the typed words: the model's "since" is dropped.
    prep = _Prep(symptoms=[], since="3 days", since_quote="for three days")
    _, _, _, client = _world(prep=prep)
    assert client.post(f"/patients/{PID}/visit-preps",
                       json={"symptoms": "cough"}).json()["since"] is None
    # An old client's own "since" is kept as before.
    assert client.post(f"/patients/{PID}/visit-preps",
                       json={"symptoms": "cough", "since": "a week"}).json()["since"] == "a week"


def test_prep_answers_go_to_the_prompt_are_saved_and_checked_for_warning_signs():
    db, ai, _, client = _world()
    answers = [{"question": "Where exactly?", "answer": "Left side"},
               {"question": "Fever?", "answer": "  "}]  # an empty answer is dropped
    body = client.post(f"/patients/{PID}/visit-preps",
                       json={"symptoms": "my stomach hurts", "answers": answers}).json()
    assert body["answers"] == [{"question": "Where exactly?", "answer": "Left side"}]
    assert "- Where exactly?: Left side" in ai.users[0] and "Fever?" not in ai.users[0]
    assert db.get_visit_prep(body["id"]).answers[0].answer == "Left side"
    assert body["urgent"] is False
    # A warning sign only in an answer still leads the summary.
    body = client.post(f"/patients/{PID}/visit-preps", json={
        "symptoms": "my stomach hurts",
        "answers": [{"question": "Anything else?", "answer": "also some chest pain"}]}).json()
    assert body["urgent"] is True and body["warning_signs"] == ["Chest pain or pressure"]


def test_prep_answer_limits():
    _, _, _, client = _world()
    url = f"/patients/{PID}/visit-preps"
    ok = {"question": "q", "answer": "a"}
    assert client.post(url, json={"symptoms": "cough", "answers": [ok] * 3}).status_code == 200
    assert client.post(url, json={"symptoms": "cough", "answers": [ok] * 4}).status_code == 422
    assert client.post(url, json={"symptoms": "cough", "answers": [
        {"question": "q" * 201, "answer": "a"}]}).status_code == 422
    assert client.post(url, json={"symptoms": "cough", "answers": [
        {"question": "q", "answer": "a" * 201}]}).status_code == 422


def test_an_older_saved_prep_without_answers_loads():
    from app.schemas.assist import VisitPrep

    prep = VisitPrep.model_validate({"patient_id": PID, "text": "cough"})
    assert prep.answers == []


# ---- Ask: from the record, with a summary -----------------------------------------------------
def test_a_record_question_uses_the_record_only_and_never_searches():
    rec = _RecordAnswer(item_ids=["r1", "r999", "r1"], summary="You take metformin for diabetes.")
    _, ai, search, client = _world(plan=_plan(words="", kind="record"), record=rec)
    body = _ask(client, "what tablets am I taking for BP?").json()
    assert body["kind"] == "record" and body["health_question"] is True
    assert search.queries == [] and ai.calls == ["_Plan", "_RecordAnswer"]
    # r1 is the current medicine; the invented id and the repeat are dropped.
    assert body["record_items"] == [{"id": "r1", "kind": "medicine", "label": "Metformin 500 mg",
                                     "detail": "", "link": "/record",
                                     "link_label": "Open My record", "numbers_link": "",
                                     "group_key": ""}]
    assert body["charts"] == []  # a medicine brings no chart
    assert body["record_summary"] == "You take metformin for diabetes."
    assert body["points"] == [] and body["sources"] == [] and body["summary"] is None
    assert body["models_used"] == ["nvidia:super"]  # no tavily
    assert "r1. [medicine] Metformin 500 mg" in ai.users[1]
    assert "Old antibiotic" not in ai.users[1]  # a finished course is not in the list
    assert "HbA1c" in ai.users[1] and "up to 5.6" in ai.users[1] and "high" in ai.users[1]


def test_record_items_come_from_confirmed_entries_with_dose_and_frequency():
    db, _, _, client = _world(plan=_plan(words="", kind="record"),
                              record=_RecordAnswer(item_ids=["r1", "r2", "r3", "r4"]))
    e = Entry(patient_id=PID, kind=EntryKind.MEDICATION, label="Amlodipine",
              medication=Medication(dose="5 mg", doses_per_day=1, ongoing=True))
    e.set_status(EntryStatus.CONFIRMED, by="system")
    db.save_entry(e)
    proposed = Entry(patient_id=PID, kind=EntryKind.DIAGNOSIS, label="Secret proposal")
    db.save_entry(proposed)  # not confirmed
    items = _ask(client, "what do I take?").json()["record_items"]
    assert {"id": "r2", "kind": "medicine", "label": "Amlodipine",
            "detail": "5 mg, once a day", "link": "/record", "link_label": "Open My record",
            "numbers_link": "", "group_key": ""} in items
    assert all("Secret" not in i["label"] for i in items)
    assert {i["kind"] for i in items} <= {"medicine", "allergy", "condition", "event", "test"}


def test_record_question_with_an_empty_record_makes_no_record_model_call():
    db = Storage()
    db.save_patient(Patient(id=PID, name="Asha Verma (synthetic)"))
    ai = FakeAI(plan=_plan(words="", kind="record"))
    client = api_client(create_app(db, ai, FakeSearch()), person=PID)
    body = _ask(client, "what tablets do I take?").json()
    assert body["record_items"] == [] and body["record_summary"] is None
    assert body["note"] == RECORD_NONE_NOTE and body["charts"] == [] and ai.calls == ["_Plan"]
    assert body["note"].startswith("This hasn't been added to your record yet.")


@pytest.mark.parametrize("summary", ["Please stop taking metformin.", "Take 500 mg daily.",
                                     "Change the dose.", ""])
def test_a_record_summary_with_a_directive_or_dose_is_dropped(summary):
    rec = _RecordAnswer(item_ids=["r1"], summary=summary)
    _, _, _, client = _world(plan=_plan(words="", kind="record"), record=rec)
    body = _ask(client, "what do I take?").json()
    assert body["record_summary"] is None and len(body["record_items"]) == 1


def test_a_both_question_gives_record_items_and_points():
    _, ai, search, client = _world(plan=_plan(kind="both"))
    body = _ask(client, "can I eat rice with my tablets for diabetes?").json()
    assert body["kind"] == "both"
    assert [i["id"] for i in body["record_items"]] == ["r1"]
    assert body["record_summary"] == "You take one medicine."
    assert len(body["points"]) == 3 and body["summary"] == "Rice raises blood sugar."
    assert len(search.queries) == 1
    assert ai.calls == ["_Plan", "_RecordAnswer", "_Answer", "_Summary"]
    assert body["models_used"] == ["nvidia:super", "tavily:search"]
    # Nothing from the record went to the search.
    assert "Metformin" not in search.queries[0][0]


def test_a_both_question_with_unsafe_search_words_still_gives_the_record_part():
    _, _, search, client = _world(plan=_plan(words="asha rice", kind="both"))
    body = _ask(client, "can I eat rice with my tablets for diabetes?").json()
    assert search.queries == [] and body["record_items"] and body["points"] == []


@pytest.mark.parametrize("text", ["Rice is fine, so stop taking tablets.", "Eat 500 mg of rice.",
                                  "You should skip it."])
def test_a_summary_with_a_directive_or_dose_is_dropped(text):
    _, _ai, _, client = _world(summary=_Summary(summary=text))
    body = _ask(client).json()
    assert body["summary"] is None and len(body["points"]) == 3


def test_summary_is_null_with_no_points_and_makes_no_summary_call():
    _, ai, _, client = _world(answer=_Answer(points=[_pt("Not in source.", 1, "nope nope")]))
    body = _ask(client).json()
    assert body["points"] == [] and body["summary"] is None
    assert ai.calls == ["_Plan", "_Answer"]


def test_a_failed_summary_call_leaves_the_points():
    _, _, _, client = _world(fail_on=("_Summary",))
    body = _ask(client).json()
    assert len(body["points"]) == 3 and body["summary"] is None


def test_a_record_question_still_checks_warning_signs_and_the_limit():
    _, ai, _, client = _world(plan=_plan(words="", kind="record"))
    assert _ask(client, "what is my chest pain medicine?").json()["urgent"] is True
    assert ai.calls == []
    for _i in range(ASKS_PER_DAY):
        assert _ask(client, "what do I take?").status_code == 200
    assert _ask(client, "what do I take?").status_code == 429


# ---- review fixes: warning signs in answers, summaries, follow-up filters ---------------------
def _prep_signs(client, text, answers):
    body = client.post(f"/patients/{PID}/visit-preps",
                       json={"symptoms": text, "answers": answers}).json()
    return body["urgent"], body["warning_signs"]


def test_a_yes_to_a_question_that_names_a_sign_is_urgent():
    _, _, _, client = _world()
    urgent, signs = _prep_signs(client, "I feel unwell",
                                [{"question": "Do you have trouble breathing?", "answer": "Yes"}])
    assert urgent and signs == ["Trouble breathing"]
    urgent, signs = _prep_signs(client, "I feel unwell",
                                [{"question": "Any chest pain?", "answer": "haan"}])
    assert urgent and signs == ["Chest pain or pressure"]


def test_a_stiff_neck_and_a_yes_to_fever_is_urgent():
    _, _, _, client = _world()
    for fever_question in ("Is there a fever?", "Do you have a high fever?"):
        urgent, signs = _prep_signs(client, "headache and a stiff neck",
                                    [{"question": fever_question, "answer": "Yes it is"}])
        assert urgent and signs == ["High fever with a stiff neck"]
    urgent, _ = _prep_signs(client, "headache and a stiff neck",
                            [{"question": "Is there a fever?", "answer": "No"}])
    assert urgent is False


@pytest.mark.parametrize("answer", ["No", "nahi", "not really", "Left side"])
def test_a_no_to_a_sign_question_is_not_urgent(answer):
    _, _, _, client = _world()
    if answer == "Left side":  # not a yes or a no, and no sign in it: still calm
        question = "Which side is the pain?"
    else:
        question = "Any chest pain?"
    urgent, signs = _prep_signs(client, "my stomach hurts",
                                [{"question": question, "answer": answer}])
    assert urgent is False and signs == []


def test_the_questions_prompt_is_v2_and_never_asks_about_emergency_signs():
    from app.pipeline import prompts
    assert "PREPARE_QUESTIONS v2" in Path(prompts.__file__).read_text(encoding="utf-8")
    assert "emergency warning signs" in prompts.PREPARE_QUESTIONS


def test_a_record_summary_may_read_back_the_record_dose():
    rec = _RecordAnswer(item_ids=["r1"], summary="You are taking Metformin 500 mg once daily.")
    _, _, _, client = _world(plan=_plan(words="", kind="record"), record=rec)
    body = _ask(client, "what do I take?").json()
    assert body["record_summary"] == "You are taking Metformin 500 mg once daily."


def test_a_record_summary_with_a_number_not_in_the_record_is_dropped():
    rec = _RecordAnswer(item_ids=["r1"], summary="You are taking Metformin 1000 mg once daily.")
    _, _, _, client = _world(plan=_plan(words="", kind="record"), record=rec)
    assert _ask(client, "what do I take?").json()["record_summary"] is None


def test_a_record_answer_with_no_kept_ids_has_no_summary():
    rec = _RecordAnswer(item_ids=["r999"], summary="You take one medicine.")
    _, _, _, client = _world(plan=_plan(words="", kind="record"), record=rec)
    body = _ask(client, "what do I take?").json()
    assert body["record_items"] == [] and body["record_summary"] is None
    assert body["charts"] == [] and body["note"] == RECORD_NONE_NOTE


_LONG = "Rice has fibre. " + "It is a grain that people eat in many places. " * 8


@pytest.mark.parametrize("text", [
    "One. Two. Three.",  # three sentences
    _LONG,  # over 300 characters
    "Rice is a grain. You should eat less of it.",
    "You must watch your sugar.",
    "Keep taking the tablets daily.",
    "Rice is a grain. Continue taking it with meals.",
    "Take metformin with food.",
    "Increase your fibre.",
    "Switch to brown rice.",
    "Change your medicine soon.",
    "Avoid metformin at night.",
    "Have you tried brown rice?",
])
def test_ai_summaries_must_be_short_and_free_of_advice(text):
    _, _, _, client = _world(summary=_Summary(summary=text),
                             plan=_plan(kind="both"),
                             record=_RecordAnswer(item_ids=["r1"], summary=text))
    body = _ask(client, "can I eat rice with my tablets for diabetes?").json()
    assert body["summary"] is None and body["record_summary"] is None
    assert len(body["points"]) == 3 and len(body["record_items"]) == 1


def test_two_short_sentences_pass():
    ok = "Rice raises blood sugar. Brown rice has more fibre."
    _, _, _, client = _world(summary=_Summary(summary=ok), plan=_plan(kind="both"))
    assert _ask(client, "can I eat rice with my tablets for diabetes?").json()["summary"] == ok


def test_follow_up_questions_and_choices_get_the_wider_filter_and_length_caps():
    follow = _FollowUps(questions=[
        _FollowUp(text="Have you tried a warm drink?", choices=[]),
        _FollowUp(text="Should you switch to another tablet?", choices=[]),
        _FollowUp(text="Is it " + "very " * 50 + "bad?", choices=[]),
        _FollowUp(text="Where is it?", choices=["Keep taking it", "Left", "x" * 41, "Right"])])
    _, _, _, client = _world(follow=follow)
    body = _questions(client).json()
    assert body["questions"] == [{"id": "q1", "text": "Where is it?",
                                  "choices": ["Left", "Right"]}]


def test_both_with_no_general_points_uses_the_record_note():
    from app.assist import RECORD_NOTE
    # Unsafe search words.
    _, _, _, client = _world(plan=_plan(words="asha rice", kind="both"))
    assert _ask(client, "can I eat rice with my tablets for diabetes?").json()["note"] == RECORD_NOTE
    # Every point filtered out.
    _, _, _, client = _world(plan=_plan(kind="both"),
                             answer=_Answer(points=[_pt("Not in source.", 1, "nope nope")]))
    assert _ask(client, "can I eat rice with my tablets for diabetes?").json()["note"] == RECORD_NOTE


def test_rejected_and_proposed_entries_stay_out_of_the_record_items():
    from app.assist import record_items_for
    db, _, _, _ = _world()
    rejected = Entry(patient_id=PID, kind=EntryKind.DIAGNOSIS, label="Rejected condition")
    rejected.set_status(EntryStatus.REJECTED, by="patient")
    db.save_entry(rejected)
    db.save_entry(Entry(patient_id=PID, kind=EntryKind.ALLERGY, label="Proposed allergy"))
    entries = db.list_entries(PID)
    labels = [i.label for i in record_items_for(entries, db.list_encounters(PID))]
    assert "Metformin 500 mg" in labels
    assert "Rejected condition" not in labels and "Proposed allergy" not in labels


# ---- Ask: links to the source, charts for number questions, no repeats -------------------------
def _confirmed(db, **kw):
    e = Entry(patient_id=kw.pop("patient_id", PID), **kw)
    e.set_status(EntryStatus.CONFIRMED, by="system")
    db.save_entry(e)
    return e


def _visit(db):
    enc = Encounter(patient_id=PID, visit_date=TODAY)
    db.save_encounter(enc)
    return enc


def _reading(db, label, value, days_ago, unit="%", enc=None, test=None, **rng):
    return _confirmed(
        db, kind=EntryKind.RESULT, label=label, encounter_id=enc.id if enc else None,
        links=[test.id] if test else [],
        result=TestResult(value=value, unit=unit, measured_on=TODAY - timedelta(days=days_ago),
                          **rng))


def _items_by_label(db):
    from app.assist import record_items_for
    return {i.label: i for i in record_items_for(db.list_entries(PID), db.list_encounters(PID))}


def test_record_items_link_to_the_visit_the_report_or_my_record():
    db, _, _, _ = _world()  # Metformin, the allergy, HbA1c: none of them has a visit
    enc = _visit(db)
    _confirmed(db, kind=EntryKind.MEDICATION, label="Amlodipine", encounter_id=enc.id,
               medication=Medication(ongoing=True))
    _confirmed(db, kind=EntryKind.DIAGNOSIS, label="Hypertension", encounter_id=enc.id)
    _confirmed(db, kind=EntryKind.SURGERY, label="Hernia repair", encounter_id=enc.id,
               occurred_on=str(TODAY.year))
    test = _confirmed(db, kind=EntryKind.TEST, label="Lipid profile", encounter_id=enc.id)
    _reading(db, "LDL cholesterol", 130, 3, "mg/dL", enc, test, ref_high=100)
    _reading(db, "Blood sugar (fasting)", 99, 2, "mg/dL", enc)  # a value with no report entry
    _reading(db, "Weight", 62.5, 1, "kg")  # typed by the patient: no visit, no report
    items = _items_by_label(db)
    assert (items["Amlodipine"].link, items["Amlodipine"].link_label) == (
        f"/encounters/{enc.id}", "Open visit")
    assert items["Hypertension"].link == f"/encounters/{enc.id}"
    assert items["Hernia repair"].link == f"/encounters/{enc.id}"
    assert (items["Metformin 500 mg"].link, items["Metformin 500 mg"].link_label) == (
        "/record", "Open My record")
    assert items["Peanut allergy"].link == "/record"
    ldl = items["LDL cholesterol"]
    assert (ldl.link, ldl.link_label) == (f"/reports/{test.id}", "See report")
    assert ldl.numbers_link == f"/numbers/{ldl.group_key}" and ldl.group_key
    assert items["Blood sugar (fasting)"].link == f"/encounters/{enc.id}"  # no report: the visit
    weight = items["Weight"]
    assert weight.link == "" and weight.numbers_link.startswith("/numbers/")
    assert items["HbA1c"].link == "" and items["HbA1c"].numbers_link.startswith("/numbers/")


def test_a_group_key_with_a_colon_or_slash_is_encoded_in_the_numbers_link():
    from app.assist import _numbers_link
    assert _numbers_link("hba1c:%") == "/numbers/hba1c%3A%25"
    assert _numbers_link("tsh:uiu/ml") == "/numbers/tsh%3Auiu%2Fml"


def test_a_number_question_returns_the_whole_series_of_the_picked_test_only():
    db, ai, _, client = _world(plan=_plan(words="", kind="record"))
    enc = _visit(db)
    test = _confirmed(db, kind=EntryKind.TEST, label="HbA1c report", encounter_id=enc.id)
    _reading(db, "HbA1c", 7.1, 200, "%", enc, test, ref_high=5.6)
    _reading(db, "HbA1c", 7.9, 100, "%", enc, test, ref_high=5.6)
    gone = _reading(db, "HbA1c", 9.9, 50, "%", enc, test, ref_high=5.6)
    gone.set_status(EntryStatus.REJECTED, by="patient")
    db.save_entry(gone)  # the patient removed it: not drawn
    _reading(db, "LDL cholesterol", 130, 3, "mg/dL")
    items = _items_by_label(db)
    ai.record = _RecordAnswer(item_ids=[items["HbA1c"].id], summary="It has gone up.")
    body = _ask(client, "how has my sugar been?").json()
    assert [c["name"] for c in body["charts"]] == ["HbA1c"]  # the LDL was not picked
    chart = body["charts"][0]
    (series,) = chart["series"]
    assert [p["value"] for p in series["points"]] == [7.1, 7.9, 8.2]  # oldest first, all dates
    assert all(p["on"] and p["unit"] == "%" and p["range"]["high"] == 5.6
               for p in series["points"][:2])
    assert chart["numbers_link"] == items["HbA1c"].numbers_link
    assert chart["report_link"] == ""  # the newest reading (today's) was typed in, no report
    assert "history" not in body["record_items"][0]
    # The model saw how many readings there are, so its summary can say "up" in words.
    assert "history: 3 readings since" in ai.users[1]


def test_the_chart_links_to_the_newest_readings_report():
    db, ai, _, client = _world(plan=_plan(words="", kind="record"))
    enc = _visit(db)
    test = _confirmed(db, kind=EntryKind.TEST, label="Sugar report", encounter_id=enc.id)
    _reading(db, "Blood sugar (fasting)", 120, 10, "mg/dL", enc, test)
    _reading(db, "Blood sugar (fasting)", 104, 1, "mg/dL", enc, test)
    items = _items_by_label(db)
    ai.record = _RecordAnswer(item_ids=[items["Blood sugar (fasting)"].id])
    chart = _ask(client, "how is my fasting sugar?").json()["charts"][0]
    assert chart["report_link"] == f"/reports/{test.id}"


def test_no_picked_test_means_no_chart_and_a_chart_set_is_capped_at_three():
    db, ai, _, client = _world(plan=_plan(words="", kind="record"))
    for label in ("LDL cholesterol", "Blood sugar (fasting)", "TSH", "Haemoglobin"):
        _reading(db, label, 5, 30, "x")
        _reading(db, label, 6, 3, "x")
    items = _items_by_label(db)
    ai.record = _RecordAnswer(item_ids=[items["Peanut allergy"].id, items["Metformin 500 mg"].id])
    assert _ask(client, "what do I have?").json()["charts"] == []
    picked = [i.id for i in items.values() if i.kind == "test"]
    assert len(picked) == 5  # the four above and HbA1c
    ai.record = _RecordAnswer(item_ids=picked)
    assert len(_ask(client, "show my tests").json()["charts"]) == 3


def test_measures_that_share_a_card_come_as_one_chart():
    db, ai, _, client = _world(plan=_plan(words="", kind="record"))
    _reading(db, "Blood pressure (systolic)", 150, 20, "mmHg")
    _reading(db, "Blood pressure (diastolic)", 95, 20, "mmHg")
    _reading(db, "Blood pressure (systolic)", 138, 2, "mmHg")
    _reading(db, "Blood pressure (diastolic)", 88, 2, "mmHg")
    items = _items_by_label(db)
    bp = [i.id for i in items.values() if "Blood pressure" in i.label]
    assert len(bp) == 2
    ai.record = _RecordAnswer(item_ids=[bp[0]])  # only one line picked: the card comes whole
    charts = _ask(client, "how is my blood pressure?").json()["charts"]
    assert len(charts) == 1 and len(charts[0]["series"]) == 2


def test_another_patients_numbers_never_appear_in_an_answer():
    db, ai, _, client = _world(plan=_plan(words="", kind="record"))
    db.save_patient(Patient(id="p-other", name="Other Person (synthetic)"))
    _confirmed(db, patient_id="p-other", kind=EntryKind.RESULT, label="HbA1c", result=TestResult(
        value=12.4, unit="%", measured_on=TODAY))
    _confirmed(db, patient_id="p-other", kind=EntryKind.MEDICATION, label="Insulin",
               medication=Medication(ongoing=True))
    items = _items_by_label(db)
    ai.record = _RecordAnswer(item_ids=[items["HbA1c"].id])
    text = _ask(client, "how is my sugar?").text
    assert "12.4" not in text and "Insulin" not in text and "8.2" in text


def test_the_detail_line_drops_what_the_label_already_says():
    db, _, _, _ = _world()
    _confirmed(db, kind=EntryKind.MEDICATION, label="Amlodipine 5 mg",
               medication=Medication(dose="5 mg", doses_per_day=1, ongoing=True))
    _confirmed(db, kind=EntryKind.MEDICATION, label="Telmisartan 40mg",
               medication=Medication(dose="40 mg", doses_per_day=2, ongoing=True))
    _confirmed(db, kind=EntryKind.MEDICATION, label="Atorvastatin",
               medication=Medication(dose="10 mg", doses_per_day=1, ongoing=True))
    _confirmed(db, kind=EntryKind.MEDICATION, label="Vitamin D 60000 IU",
               medication=Medication(dose="60000 IU", ongoing=True))
    items = _items_by_label(db)
    assert items["Amlodipine 5 mg"].detail == "once a day"
    assert items["Telmisartan 40mg"].detail == "twice a day"  # "40 mg" is "40mg" in the label
    assert items["Atorvastatin"].detail == "10 mg, once a day"  # the label does not say it
    assert items["Vitamin D 60000 IU"].detail == ""  # nothing left to say


def test_a_record_summary_may_use_numbers_from_the_history_but_no_others():
    db, ai, _, client = _world(plan=_plan(words="", kind="record"))
    _reading(db, "HbA1c", 7.1, 200, "%")
    _reading(db, "HbA1c", 7.9, 100, "%")
    items = _items_by_label(db)
    ai.record = _RecordAnswer(item_ids=[items["HbA1c"].id],
                              summary="It was 7.1 at first and is now 8.2, above its printed range.")
    assert _ask(client, "how is my sugar?").json()["record_summary"].startswith("It was 7.1")
    ai.record = _RecordAnswer(item_ids=[items["HbA1c"].id], summary="It was 6.0 at first.")
    assert _ask(client, "how is my sugar?").json()["record_summary"] is None


def test_the_record_prompt_is_v3_and_asks_for_more_than_a_reading_back():
    from app.pipeline import prompts
    assert "ASK_RECORD v3 (2026-10-28" in Path(prompts.__file__).read_text(encoding="utf-8")
    assert "do not read them back" in prompts.ASK_RECORD
    assert "every test" in prompts.ASK_RECORD
    assert '"printed range", never the "normal' in prompts.ASK_RECORD


def _sugar_world():
    """Fasting sugar 118 -> 108 (lower, above its printed range); HbA1c 6.1 -> 6.4 (higher)."""
    db, ai, _, client = _world(plan=_plan(words="", kind="record"))
    _reading(db, "Blood sugar (fasting)", 118, 100, "mg/dL", ref_high=100)
    _reading(db, "Blood sugar (fasting)", 108, 10, "mg/dL", ref_high=100)
    _reading(db, "Thyroid test TSH", 2.0, 100, "uIU/mL", ref_high=4.0)
    _reading(db, "Thyroid test TSH", 2.5, 10, "uIU/mL", ref_high=4.0)
    items = _items_by_label(db)
    return db, ai, client, items


def test_code_works_out_direction_and_range_position_and_tells_the_model():
    _, ai, client, items = _sugar_world()
    fasting = items["Blood sugar (fasting)"]
    assert (fasting.direction, fasting.position) == ("lower", "above")
    assert "the latest is lower than the one before" in fasting.history
    assert "the latest is above the printed range" in fasting.history
    tsh = items["TSH"]
    assert (tsh.direction, tsh.position) == ("higher", "within")
    # A single reading has a range position but no direction.
    assert (items["HbA1c"].direction, items["HbA1c"].position) == ("", "above")
    ai.record = _RecordAnswer(item_ids=[fasting.id])
    _ask(client, "how is my sugar?")
    assert "the latest is lower than the one before" in ai.users[1]


@pytest.mark.parametrize("summary, ok", [
    ("Your fasting blood sugar is lower than before and above its printed range.", True),
    ("Your fasting blood sugar is higher than before.", False),  # code says lower
    ("Your fasting blood sugar is below its printed range.", False),  # code says above
    ("Your TSH is higher than before and within its printed range.", True),
    ("Your TSH is higher than before but above its printed range.", False),
    ("Fasting blood sugar is lower, but TSH is higher.", True),
    ("Fasting blood sugar is higher, but TSH is lower.", False),  # swapped
    ("Fasting blood sugar is lower and TSH is higher, and both are above their ranges.", True),
    ("Your HbA1c is higher than before.", False),  # one reading: no direction to speak of
    ("Your HbA1c is above its printed range.", True),
    ("You have 3 test values listed.", False),  # 3 is in no item: the number check
    ("You have two tests listed.", True),
])
def test_a_direction_or_range_word_must_match_the_codes_word_for_that_measure(summary, ok):
    _, ai, client, items = _sugar_world()
    ids = [items[k].id for k in ("Blood sugar (fasting)", "TSH", "HbA1c")]
    ai.record = _RecordAnswer(item_ids=ids, summary=summary)
    got = _ask(client, "how are my tests?").json()["record_summary"]
    assert (got == summary) is ok


@pytest.mark.parametrize("word", ["better", "worse", "Good", "bad", "improved", "improving",
                                  "worsened", "normal", "abnormal", "controlled", "Uncontrolled",
                                  "healthy", "concerning"])
def test_a_record_summary_never_judges(word):
    _, ai, client, items = _sugar_world()
    ai.record = _RecordAnswer(item_ids=[items["Peanut allergy"].id],
                              summary=f"It is {word} here.")
    assert _ask(client, "what allergies?").json()["record_summary"] is None
    ai.record = _RecordAnswer(item_ids=[items["Peanut allergy"].id],
                              summary="You have one allergy listed.")
    assert _ask(client, "what allergies?").json()["record_summary"] is not None


def test_normal_range_wording_is_dropped_but_printed_range_stays():
    _, ai, client, items = _sugar_world()
    fasting = items["Blood sugar (fasting)"].id
    ai.record = _RecordAnswer(item_ids=[fasting], summary="Fasting blood sugar is above its normal range.")
    assert _ask(client, "sugar?").json()["record_summary"] is None
    ai.record = _RecordAnswer(item_ids=[fasting], summary="Fasting blood sugar is above its printed range.")
    assert _ask(client, "sugar?").json()["record_summary"] is not None


def test_a_search_word_from_the_record_needs_to_be_in_the_question():
    # The model must not carry a medicine or a condition of the record into a search on its own.
    labels = ["Metformin 500 mg", "Type 2 diabetes", "Hypertension"]
    ok = check_search_words
    assert not ok("diabetes rice", "Asha", labels, "can I eat rice with my tablets?")
    assert not ok("metformin food", "Asha", labels, "what can I eat?")
    assert not ok("Hypertension salt", "Asha", labels, "is salt bad?")
    assert ok("diabetes rice", "Asha", labels, "Can I eat rice with Diabetes?")  # typed by them
    assert ok("rice breakfast", "Asha", labels, "can I eat rice?")
    assert ok("diabetes rice", "Asha")  # no record given: as before


def test_ask_does_not_search_with_a_condition_the_patient_did_not_type():
    _, _, search, client = _world(plan=_plan(words="diabetes rice", kind="both"))
    body = _ask(client, "can I eat rice with my tablets?").json()
    assert search.queries == [] and body["points"] == []
    _, _, search, client = _world(plan=_plan(words="diabetes rice", kind="both"))
    _ask(client, "can I eat rice with my diabetes?")
    assert len(search.queries) == 1


def test_a_follow_up_may_search_with_a_condition_typed_in_the_question_it_follows():
    _, _, search, client = _world(plan=_plan(words="diabetes mango", kind="general"))
    prev = {"question": "Can I eat rice with diabetes?", "points": ["Rice raises blood sugar."]}
    r = client.post(f"/patients/{PID}/ask", json={"question": "what about mango?",
                                                  "previous": prev})
    assert r.status_code == 200 and len(search.queries) == 1
    _, _, search, client = _world(plan=_plan(words="diabetes mango", kind="general"))
    client.post(f"/patients/{PID}/ask", json={"question": "what about mango?"})
    assert search.queries == []  # nothing typed named it


def _follow(client, question="check again", previous=None):
    previous = previous or {"question": "How have my sugar levels been?", "points": []}
    return client.post(f"/patients/{PID}/ask", json={"question": question, "previous": previous})


def test_check_again_after_a_record_answer_is_a_record_question_with_the_previous_one():
    plan = _Plan(kind="record", full_question="How have my sugar levels been?")
    _, ai, search, client = _world(plan=plan)
    body = _follow(client).json()
    assert body["kind"] == "record" and body["health_question"] is True
    assert len(body["record_items"]) == 1 and search.queries == []
    assert ai.calls == ["_Plan", "_RecordAnswer"]
    assert "Previous question (context only): How have my sugar levels been?" in ai.users[0]
    assert "Question: check again" in ai.users[0]
    assert "Question: How have my sugar levels been?" in ai.users[1]  # the record part
    assert "check again" not in ai.users[1].split("Question:")[1]
    assert "full_question" not in body


def test_the_full_question_also_goes_to_the_web_answer_and_the_allergy_note():
    plan = _Plan(kind="general", search_words="rice breakfast",
                 full_question="Can I eat peanut butter with rice?")
    _, ai, search, client = _world(plan=plan)
    body = _follow(client, "try again").json()
    assert len(search.queries) == 1
    assert "Question: Can I eat peanut butter with rice?" in ai.users[1]
    assert body["allergy_notes"] == ["Your record lists an allergy to Peanut."]


def test_an_allergen_typed_by_the_patient_is_noted_even_if_the_model_rewords_the_question():
    plan = _Plan(kind="general", search_words="rice breakfast",
                 full_question="What about nut butters with rice?")
    _, _, _, client = _world(plan=plan)
    body = _follow(client, "what about peanut butter?").json()
    assert body["allergy_notes"] == ["Your record lists an allergy to Peanut."]
    plan = _Plan(kind="record", full_question="Do I have a peanut allergy?")  # only the model's
    _, _, _, client = _world(plan=plan)
    assert _follow(client, "check again").json()["allergy_notes"] == [
        "Your record lists an allergy to Peanut."]


def test_a_follow_up_is_read_with_the_question_before_it_for_warning_signs():
    _, ai, search, client = _world()
    prev = {"question": "I have a high fever, what can I eat?", "points": []}
    body = client.post(f"/patients/{PID}/ask", json={
        "question": "and my neck is stiff, is that ok?", "previous": prev}).json()
    assert body["urgent"] is True and "High fever with a stiff neck" in body["warning_signs"]
    assert ai.calls == [] and search.queries == []
    _, ai, _, client = _world()  # an ordinary follow-up to an ordinary question
    prev = {"question": "Can I eat rice with diabetes?", "points": []}
    body = client.post(f"/patients/{PID}/ask", json={"question": "and at night?",
                                                     "previous": prev}).json()
    assert body["urgent"] is False and ai.calls


def test_the_models_full_question_is_checked_for_warning_signs_too():
    plan = _Plan(kind="general", search_words="rice",
                 full_question="Can I eat rice if I want to die?")
    _, ai, search, client = _world(plan=plan)
    body = _follow(client, "and rice?").json()
    assert body["urgent"] is True and body["helpline"] and search.queries == []
    assert ai.calls == ["_Plan"]


def test_a_full_question_is_ignored_without_a_previous_question():
    plan = _Plan(kind="record", full_question="How have my sugar levels been?")
    _, ai, _, client = _world(plan=plan)
    assert client.post(f"/patients/{PID}/ask", json={"question": "check again"}).status_code == 200
    assert "Question: check again" in ai.users[1]
    assert "sugar levels" not in ai.users[1].split("The patient's confirmed record")[0]
    _, ai, _, client = _world(plan=plan)  # an empty previous question counts as none
    _follow(client, previous={"question": "  ", "points": []})
    assert "Question: check again" in ai.users[1]


def test_a_full_question_is_one_line_and_at_most_200_characters():
    messy = "How  have\n my   sugar\tlevels been? " + "x" * 300
    plan = _Plan(kind="record", full_question=messy)
    _, ai, _, client = _world(plan=plan)
    _follow(client)
    asked = ai.users[1].split("Question: ")[1].split("\n\nThe patient's")[0]
    assert asked.startswith("How have my sugar levels been? xxx")
    assert "\n" not in asked and len(asked) <= 200
    _, ai, _, client = _world(plan=_Plan(kind="record", full_question="   "))
    _follow(client)  # blank: the typed question is used
    assert "Question: check again" in ai.users[1]


def test_a_follow_up_search_word_from_the_record_still_needs_to_be_typed():
    # "metformin" is in the record but in neither the typed question nor the previous one.
    plan = _Plan(kind="general", search_words="metformin food",
                 full_question="What can I eat with metformin?")  # the model's words never count
    _, _, search, client = _world(plan=plan)
    body = _follow(client, "check again",
                   {"question": "What can I eat?", "points": ["Eat fruit."]}).json()
    assert search.queries == [] and body["note"] == "Try asking in more general words."
    # Typed in the previous question: allowed.
    plan = _Plan(kind="general", search_words="metformin food")
    _, _, search, client = _world(plan=plan)
    _follow(client, "check again", {"question": "Can I eat rice with metformin?", "points": []})
    assert len(search.queries) == 1


def test_the_search_prompt_is_v4_and_reads_a_short_follow_up_with_the_previous_question():
    from app.pipeline.prompts import ASK_SEARCH
    flat = " ".join(ASK_SEARCH.split())
    assert "`full_question`" in flat
    assert '"check again" or "try again" means the previous question again' in flat
    assert "never `not_health` just because it is short" in flat


def test_a_dose_is_only_dropped_from_the_detail_when_the_whole_token_is_in_the_label():
    db, _, _, _ = _world()
    _confirmed(db, kind=EntryKind.MEDICATION, label="Thyroxine 125mcg",
               medication=Medication(dose="25mcg", doses_per_day=1, ongoing=True))
    assert _items_by_label(db)["Thyroxine 125mcg"].detail == "25mcg, once a day"


def test_a_report_link_skips_a_proposed_test():
    from app.assist import _Links
    db, _, _, _ = _world()
    proposed = Entry(patient_id=PID, kind=EntryKind.TEST, label="Proposed panel")
    db.save_entry(proposed)
    shown = _confirmed(db, kind=EntryKind.TEST, label="Shown panel")
    value = _reading(db, "Creatinine", 0.9, 1, "mg/dL")
    value.links = [proposed.id]
    db.save_entry(value)
    links = _Links(db.list_entries(PID), db.list_encounters(PID))
    assert links.report(value.id) == ""
    value.links = [proposed.id, shown.id]
    db.save_entry(value)
    links = _Links(db.list_entries(PID), db.list_encounters(PID))
    assert links.report(value.id) == f"/reports/{shown.id}"
