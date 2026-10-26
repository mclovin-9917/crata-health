import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.ai.client import AIError
from app.ai.search import SearchError, SearchResult, WebSearch
from app.config import Settings
from app.main import create_app
from app.medicine_info import TRUSTED_DOMAINS, _Point, _Summary, key_for, medicine_info, summarize
from app.storage import Storage
from tests.conftest import api_client

RESULTS = [
    SearchResult(title="Amoxicillin and clavulanate - MedlinePlus",
                 url="https://medlineplus.gov/druginfo/meds/a685024.html",
                 content="Amoxicillin and clavulanic acid is used to treat certain infections caused "
                         "by bacteria. It is usually taken with a meal. Diarrhea is a common side effect."),
    SearchResult(title="Co-amoxiclav - NHS", url="https://www.nhs.uk/medicines/co-amoxiclav/",
                 content="Tell your doctor if you have ever had an allergic reaction to penicillin. "
                         "Call 999 if you have a serious allergic reaction."),
]


def _p(text, source, quote):
    return _Point(text=text, source=source, quote=quote)


SUMMARY = _Summary(
    matches=True,
    uses=[_p("It treats some bacterial infections.", 1, "used to treat certain infections caused by bacteria")],
    how_taken=[_p("It is usually taken with a meal.", 1, "usually taken with a meal"),
               _p("Take two tablets a day.", 1, "two tablets a day")],  # not in the source
    side_effects=[_p("Diarrhoea is common.", 1, "Diarrhea is a common side effect"),
                  _p("Made-up source.", 7, "anything")],  # no source 7
    warnings=[_p("People allergic to penicillin should tell their doctor.", 2,
                 "allergic reaction to penicillin")],
)


class SummaryAI:
    def __init__(self, answer=SUMMARY, fail=False):
        self.answer, self.fail, self.calls = answer, fail, []
        self.used = []

    def reason_json(self, system, user, schema, **kw):
        self.calls.append(user)
        self.thinking = kw.get("thinking")
        if self.fail:
            raise AIError("busy")
        self.used.append("nvidia:super")
        return self.answer


class FakeSearch:
    def __init__(self, results=RESULTS, fail=False, available=True):
        self.results, self.fail, self.available, self.queries = results, fail, available, []

    def search(self, query, domains, max_results=6):
        self.queries.append((query, domains))
        if self.fail:
            raise SearchError("Tavily answered HTTP 429")
        return self.results


GENERICS = ["amoxicillin", "clavulanic acid"]


def test_one_summary_per_active_ingredients():
    assert key_for("Augmentin 625", ["Clavulanic acid", "amoxicillin"]) == "amoxicillin + clavulanic acid"
    assert key_for("Clavam 625", GENERICS) == key_for("Augmentin 625 Duo", GENERICS)
    assert key_for("Zyloric  100", []) == "name:zyloric 100"


def test_points_need_their_quote_in_their_source():
    info = summarize("Augmentin 625 mg", GENERICS, RESULTS, SummaryAI())
    assert info.found
    assert [p.text for p in info.how_taken] == ["It is usually taken with a meal."]
    assert [p.text for p in info.side_effects] == ["Diarrhoea is common."]
    assert info.dropped == 2
    # Links come only from the search results, numbered in the order they are first used.
    assert [s.url for s in info.sources] == [RESULTS[0].url, RESULTS[1].url]
    assert [s.site for s in info.sources] == ["MedlinePlus", "NHS"]
    assert info.warnings[0].source == 1


def test_sources_about_something_else_give_no_summary():
    info = summarize("Augmentin", GENERICS, RESULTS, SummaryAI(_Summary(matches=False)))
    assert not info.found and info.sources == []
    assert not summarize("Augmentin", GENERICS, [], SummaryAI()).found


def test_only_the_medicine_name_is_searched_on_trusted_sites():
    db, ai, search = Storage(), SummaryAI(), FakeSearch()
    medicine_info("Augmentin 625 mg", GENERICS, db, ai, search)
    ((query, domains),) = search.queries
    assert query == "amoxicillin and clavulanic acid medicine uses side effects warnings"
    assert domains == TRUSTED_DOMAINS
    assert "medlineplus.gov" in domains and "nhs.uk" in domains


def test_summaries_are_kept_for_30_days():
    db, ai, search = Storage(), SummaryAI(), FakeSearch()
    first = medicine_info("Augmentin 625 mg", GENERICS, db, ai, search)
    assert first.models_used == ["nvidia:super", "tavily:search"]
    again = medicine_info("Clavam 625", GENERICS, db, ai, search)  # same ingredients
    assert again.name == "Augmentin 625 mg" and len(search.queries) == 1

    first.fetched_at = datetime.now(UTC) - timedelta(days=31)
    db.save_medicine_info(first)
    medicine_info("Clavam 625", GENERICS, db, ai, search)
    assert len(search.queries) == 2


def test_a_miss_is_retried_after_an_hour():
    db, ai, search = Storage(), SummaryAI(_Summary(matches=False)), FakeSearch()
    miss = medicine_info("Zyloric 100", [], db, ai, search)
    assert miss.models_used == ["nvidia:super", "tavily:search"]  # no ingredients: one search
    medicine_info("Zyloric 100", [], db, ai, search)
    assert len(search.queries) == 1
    miss.fetched_at = datetime.now(UTC) - timedelta(minutes=70)
    db.save_medicine_info(miss)
    medicine_info("Zyloric 100", [], db, ai, search)
    assert len(search.queries) == 2


def test_nothing_by_ingredients_then_the_brand_name():
    class ByBrandOnly(SummaryAI):
        """The first pages (by ingredients) don't match; the brand's own page does."""

        def reason_json(self, system, user, schema, **kw):
            answer = super().reason_json(system, user, schema, **kw)
            return answer if len(self.calls) > 1 else _Summary(matches=False)

    db, search = Storage(), FakeSearch()
    info = medicine_info("Augmentin 625", GENERICS, db, ByBrandOnly(), search)
    assert info.found
    assert [q for q, _ in search.queries] == [
        "amoxicillin and clavulanic acid medicine uses side effects warnings",
        "Augmentin 625 medicine uses side effects warnings"]
    assert info.models_used.count("tavily:search") == 2


def test_old_entries_get_their_ingredients_from_the_brand_table():
    db, ai, search = Storage(), SummaryAI(), FakeSearch()
    info = medicine_info("Ascoril LS", [], db, ai, search)  # saved before generic names existed
    assert info.key == "ambroxol + guaifenesin + levosalbutamol"
    assert search.queries[0][0].startswith("ambroxol and levosalbutamol and guaifenesin")


def test_forums_and_comparisons_are_skipped():
    noise = [SearchResult(title=t, url=u, content="used to treat certain infections caused by bacteria")
             for t, u in [("Forum", "https://connect.mayoclinic.org/discussion/x"),
                          ("Compare", "https://www.drugs.com/compare/a-vs-b"),
                          ("Interactions", "https://www.drugs.com/drug-interactions/a-with-b.html")]]
    ai = SummaryAI()
    summarize("Augmentin", GENERICS, [*noise, *RESULTS], ai)
    assert "connect.mayoclinic" not in ai.calls[0] and "/compare/" not in ai.calls[0]
    assert "drug-interactions" not in ai.calls[0]


# ---- the Tavily client ---------------------------------------------------------------------
def test_tavily_request_and_errors():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [
            {"title": "t", "url": "https://nhs.uk/x", "content": "c"},
            {"title": "no content", "url": "https://nhs.uk/y", "content": ""},
        ]})

    s = Settings(_env_file=None, TAVILY_API_KEY="tvly-test")
    results = WebSearch(s, transport=httpx.MockTransport(handler)).search("q", ["nhs.uk"])
    assert [r.url for r in results] == ["https://nhs.uk/x"]
    assert seen["auth"] == "Bearer tvly-test"
    assert seen["body"]["include_domains"] == ["nhs.uk"] and seen["body"]["search_depth"] == "basic"

    busy = WebSearch(s, transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(SearchError, match="429"):
        busy.search("q", [])
    assert not WebSearch(Settings(_env_file=None)).available


# ---- the route -----------------------------------------------------------------------------
def _client(search=None, ai=None, signed_in=True):
    app = create_app(Storage(), ai or SummaryAI(), search or FakeSearch())
    return api_client(app, person="someone" if signed_in else None)


def test_route_returns_the_summary():
    r = _client().get("/medicine-info", params={"name": "Augmentin 625 mg", "generics": GENERICS})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] and body["key"] == "amoxicillin + clavulanic acid"
    assert body["uses"][0]["text"] == "It treats some bacterial infections."


def test_route_errors():
    params = {"name": "Augmentin"}
    assert _client(FakeSearch(available=False)).get("/medicine-info", params=params).status_code == 503
    assert _client(FakeSearch(fail=True)).get("/medicine-info", params=params).status_code == 502
    assert _client(ai=SummaryAI(fail=True)).get("/medicine-info", params=params).status_code == 502
    assert _client().get("/medicine-info", params={"name": ""}).status_code == 422
    assert _client().get("/medicine-info", params={"name": "x" * 81}).status_code == 422
    # Signed in only: each lookup costs a Tavily credit and a model call.
    assert _client(signed_in=False).get("/medicine-info", params=params).status_code == 401


def test_points_that_tell_the_reader_to_change_a_medicine_are_dropped():
    answer = _Summary(matches=True, warnings=[
        _p("You should stop the medicine if a rash appears.", 2, "allergic reaction to penicillin"),
        _p("Do not skip doses.", 2, "allergic reaction to penicillin"),
        _p("Get medical help right away for a serious allergic reaction.", 2,
           "serious allergic reaction"),
    ])
    ai = SummaryAI(answer)
    info = summarize("Augmentin", GENERICS, RESULTS, ai)
    assert [p.text for p in info.warnings] == [
        "Get medical help right away for a serious allergic reaction."]
    assert info.dropped == 2
    assert ai.thinking is False  # a summary needs no reasoning: much faster


def test_pages_for_clinicians_are_skipped():
    pro = SearchResult(title="Package insert", url="https://www.drugs.com/pro/x-injection.html",
                       content="used to treat certain infections caused by bacteria")
    ai = SummaryAI()
    summarize("Augmentin", GENERICS, [pro, *RESULTS], ai)
    assert "drugs.com/pro" not in ai.calls[0]
    assert not summarize("Augmentin", GENERICS, [pro], SummaryAI()).found


def test_confirming_a_medicine_fetches_its_summary_in_the_background():
    from app.schemas.patient import Patient
    from app.schemas.record import Entry, EntryKind, Medication

    db, search = Storage(), FakeSearch()
    patient = Patient(name="Asha Verma (synthetic)")
    db.save_patient(patient)
    med = Entry(patient_id=patient.id, kind=EntryKind.MEDICATION, label="Augmentin 625 mg",
                medication=Medication(generic_names=GENERICS))
    note = Entry(patient_id=patient.id, kind=EntryKind.NOTE, label="Plenty of fluids")
    db.save_entry(med)
    db.save_entry(note)
    client = api_client(create_app(db, SummaryAI(), search), person=patient.id)
    assert client.patch(f"/entries/{note.id}", json={"action": "confirm"}).status_code == 200
    assert search.queries == []  # not a medicine
    assert client.patch(f"/entries/{med.id}", json={"action": "confirm"}).status_code == 200
    assert len(search.queries) == 1  # TestClient runs background tasks before returning
    assert db.get_medicine_info("amoxicillin + clavulanic acid").found


def test_apps_built_for_tests_never_search():
    from tests.test_pipeline import FakeAI

    app = create_app(Storage(), FakeAI([]))
    assert not app.state.search.available
