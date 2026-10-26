"""Brand names the table does not know, looked up on trusted sites and checked by code."""

from datetime import UTC, datetime, timedelta

from app.ai.search import SearchError, SearchResult
from app.pipeline.brands import _Read, _Reads, from_search, key_for, lookup
from app.schemas.medicine import BrandMatch
from app.schemas.record import (
    BillLine,
    Entry,
    EntryCheck,
    EntryKind,
    Medication,
    Provenance,
    SourceType,
)
from app.storage import Storage

ONDEM_PAGE = SearchResult(
    title="Ondem 4 Tablet: View Uses, Side Effects, Price | 1mg",
    url="https://www.1mg.com/drugs/ondem-4-tablet-12345",
    content="Ondem 4 Tablet is composed of Ondansetron. It is used to prevent nausea and vomiting.",
)
FORUM = SearchResult(title="Ondem question", url="https://connect.mayoclinic.org/discussion/x",
                     content="Ondem 4 Tablet is composed of Ondansetron, I think")


class FakeSearch:
    available = True

    def __init__(self, pages=None, fail=False):
        self.pages, self.fail, self.calls, self.queries = pages or {}, fail, 0, []

    def search(self, query, domains, max_results=6):
        self.queries.append(query)
        if self.fail:
            raise SearchError("Tavily answered HTTP 500")
        self.calls += 1
        return next((p for name, p in self.pages.items() if query.startswith(name)), [])


class ReadAI:
    def __init__(self, reads):
        self.reads, self.asked = reads, []

    def reason_json(self, system, user, schema, **kw):
        assert schema is _Reads
        self.asked.append(user)
        return _Reads(items=self.reads)


def _ondem_read(**kw):
    base = {"name": "Ondem 4 Tab", "found": True, "generics": ["Ondansetron"], "source": 1,
            "quote": "Ondem 4 Tablet is composed of Ondansetron"}
    return _Read(**{**base, **kw})


def test_a_match_read_off_a_trusted_page_is_kept_with_its_source():
    db, search, ai = Storage(), FakeSearch({"Ondem": [ONDEM_PAGE]}), ReadAI([_ondem_read()])
    (m,) = lookup(["Ondem 4 Tab"], ai, search, db).values()
    assert m.found and m.generic_names == ["ondansetron"]
    assert m.site == "1mg" and m.url == ONDEM_PAGE.url
    assert search.queries == ["Ondem 4 tablet salt composition"]  # only the name goes out
    assert db.get_brand_match("ondem") == m


def test_the_cache_answers_the_next_time_and_a_miss_is_retried_after_an_hour():
    db, search = Storage(), FakeSearch({"Ondem": [ONDEM_PAGE]})
    lookup(["Ondem 4 Tab"], ReadAI([_ondem_read()]), search, db)
    lookup(["ONDEM 4"], ReadAI([]), search, db)  # same key
    assert search.calls == 1

    old_miss = BrandMatch(key="xylotrim", name="Xylotrim", found=False,
                          fetched_at=datetime.now(UTC) - timedelta(hours=2))
    db.save_brand_match(old_miss)
    lookup(["Xylotrim"], ReadAI([]), search, db)
    assert search.calls == 2


def test_a_quote_not_on_the_page_or_not_naming_the_ingredient_is_refused():
    for read in (_ondem_read(quote="Ondem contains ondansetron 4 mg"),  # not on the page
                 _ondem_read(generics=["domperidone"]),  # the quote does not name it
                 _ondem_read(source=3)):  # no such result
        db = Storage()
        (m,) = lookup(["Ondem 4 Tab"], ReadAI([read]), FakeSearch({"Ondem": [ONDEM_PAGE]}), db).values()
        assert not m.found


def test_a_page_about_another_medicine_is_refused():
    other = SearchResult(title="Emeset 4 Tablet | 1mg", url="https://www.1mg.com/drugs/emeset",
                         content="Emeset 4 Tablet is composed of Ondansetron.")
    read = _ondem_read(quote="Emeset 4 Tablet is composed of Ondansetron")
    (m,) = lookup(["Ondem 4 Tab"], ReadAI([read]), FakeSearch({"Ondem": [other]}), Storage()).values()
    assert not m.found


def test_a_page_for_a_variant_of_the_brand_is_refused():
    # Ondem MD is a different product page; its ingredients are not proof for plain Ondem.
    variant = SearchResult(title="Ondem MD 4 Tablet: View Uses | 1mg",
                           url="https://www.1mg.com/drugs/ondem-md-4-tablet-201739",
                           content="Ondem MD 4 Tablet is composed of Ondansetron.")
    read = _ondem_read(quote="Ondem MD 4 Tablet is composed of Ondansetron")
    (m,) = lookup(["Ondem 4 Tab"], ReadAI([read]), FakeSearch({"Ondem": [variant]}), Storage()).values()
    assert not m.found


def test_a_brand_page_is_known_by_its_address_or_its_title():
    from app.pipeline.brands import _about

    page = SearchResult(title="Buy Ondem 4 Tablet Online: View Uses, Side Effects | 1mg",
                        url="https://www.1mg.com/drugs/ondem-4-tablet-24555", content="x")
    assert _about("Ondem 4 Tab", page)
    assert _about("Ondem 4 Tab", page.model_copy(update={"url": "https://www.1mg.com/x"}))  # title
    cv = SearchResult(title="Taxim-O CV 200 Tablet | 1mg",
                      url="https://www.1mg.com/drugs/taxim-o-cv-200-tablet-113489", content="x")
    assert not _about("Taxim-O 200 Tab", cv)
    assert _about("Taxim-O 200 Tab", cv.model_copy(update={
        "title": "Taxim-O 200 Tablet | 1mg", "url": "https://www.1mg.com/drugs/taxim-o-200-tablet-52001"}))
    generic = SearchResult(title="Cefuroxime: MedlinePlus", url="https://medlineplus.gov/druginfo/meds/a601206.html",
                           content="x")
    assert _about("Cefuroxime 500 mg", generic)  # by its title


def test_the_query_spells_out_the_form():
    from app.pipeline.brands import query_for

    assert query_for("Taxim-O 200 Tab") == "Taxim-O 200 tablet salt composition"
    assert query_for("Ascoril LS Syp") == "Ascoril LS syrup salt composition"


def test_forums_are_left_out_and_a_failed_search_leaves_the_name_alone():
    ai = ReadAI([_ondem_read()])
    (m,) = lookup(["Ondem 4 Tab"], ai, FakeSearch({"Ondem": [FORUM]}), Storage()).values()
    assert not m.found and ai.asked == []  # nothing left to read
    assert lookup(["Ondem 4 Tab"], ai, FakeSearch(fail=True), Storage()) == {}


def _med(label, generics=(), generic_from="model", checks=()):
    return Entry(kind=EntryKind.MEDICATION, label=label, checks=list(checks),
                 medication=Medication(generic_names=list(generics),
                                       generic_from=generic_from if generics else None))


def test_medicines_and_bill_lines_matched_only_by_the_model_get_the_sites_answer():
    db = Storage()
    search = FakeSearch({"Ondem": [ONDEM_PAGE]})
    rx = _med("Ondem 4 Tab", ["ondansetron hcl"])
    table = _med("Dolo 650", ["paracetamol"], generic_from="table")
    vague = _med("BP tablets", checks=[EntryCheck.MEDICINE_UNNAMED])
    line = BillLine(name="Ondem 4 Tab", is_drug=True,
                    provenance=Provenance(source_id="b", source_type=SourceType.IMAGE, quote="x"))
    assert from_search([rx, table, vague], [line], ReadAI([_ondem_read()]), search, db) == 2
    assert rx.medication.generic_names == ["ondansetron"] and rx.medication.generic_from == "search"
    assert rx.medication.generic_source == ONDEM_PAGE.url
    assert line.generic_from == "search"
    assert table.medication.generic_from == "table" and vague.medication.generic_from is None
    assert search.queries == ["Ondem 4 tablet salt composition"]  # one search for both


def test_without_tavily_nothing_changes():
    rx = _med("Ondem 4 Tab", ["ondansetron"])
    assert from_search([rx], [], ReadAI([]), None, Storage()) == 0
    assert rx.medication.generic_from == "model"


def test_a_visit_looks_up_unknown_brands_and_says_tavily_answered():
    from app.pipeline.run import process_encounter
    from app.schemas.record import Encounter
    from tests.test_pipeline import FakeAI, _item

    class VisitAI(FakeAI):
        def reason_json(self, system, user, schema, **kw):
            if schema is _Reads:
                return _Reads(items=[_ondem_read()])
            return super().reason_json(system, user, schema, **kw)

    item = _item(kind=EntryKind.MEDICATION, label="Ondem 4 Tab", source_id="notes1",
                 quote="Ondem 4 Tab if vomiting")
    db = Storage()
    enc = Encounter(patient_id="p1")
    db.save_encounter(enc)
    process_encounter(db, VisitAI([item]), enc.id, [], notes="Ondem 4 Tab if vomiting.",
                      search=FakeSearch({"Ondem": [ONDEM_PAGE]}))
    done = db.get_encounter(enc.id)
    (med,) = done.entries
    assert med.medication.generic_from == "search" and med.medication.generic_names == ["ondansetron"]
    assert "tavily:search" in done.models_used["checking_medicines"]


def test_keys_ignore_strength_and_form():
    assert key_for("Ondem 4 Tab") == key_for("ONDEM 4") == "ondem"
