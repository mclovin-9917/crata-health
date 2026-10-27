"""Before a visit and Ask about my health. Nothing here reaches a real model or a real Tavily."""

import io
import wave
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from app.ai.client import AIClient, AIError, audio_part
from app.ai.search import SearchError, SearchResult
from app.assist import (
    ASKS_PER_DAY,
    DOSE,
    HEALTH_DOMAINS,
    SPECIALTIES,
    _Answer,
    _AskPoint,
    _clean_sentence,
    _Plan,
    _Prep,
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


def _plan(words="diabetes rice", health=True):
    return _Plan(health_question=health, search_words=words)


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

    def __init__(self, plan=None, answer=ANSWER, prep=PREP, fail=False):
        self.plan, self.answer, self.prep, self.fail = plan or _plan(), answer, prep, fail
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
        if self.fail:
            raise AIError("busy")
        self.used.append("nvidia:super")
        return {"_Plan": self.plan, "_Answer": self.answer, "_Prep": self.prep}[schema.__name__]


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
                         "questions", "record", "models_used", "helpline"}
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
    assert body["models_used"] == ["nvidia:super"]
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
                         "allergy_notes", "considered", "note", "models_used", "helpline"}
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
    assert ai.calls == ["_Plan", "_Answer"]
    assert ai.kw[0]["thinking"] is False and ai.kw[0]["temperature"] == 0.0


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
    _, ai, search, client = _world(plan=_plan(words="", health=False))
    body = _ask(client, "Who won the cricket match?").json()
    assert body["health_question"] is False and body["points"] == []
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
