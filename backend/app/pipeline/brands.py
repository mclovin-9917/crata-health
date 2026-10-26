"""Brand names the curated table does not know, looked up on trusted sites (decided 2026-10-12).

The order for a medicine's active ingredients:

1. The curated brand table (`medicines.BRANDS`): no call at all.
2. The model's own match (`medicines.ask_generics`), which also spots vague names ("BP
   tablets"); those are never searched.
3. This module, for names still matched only by the model, or not at all: one Tavily search per
   name on the trusted sites (1mg covers Indian brands), then one model call that reads the
   results for every name at once. Code keeps a match only if its quote is in that result and
   names each ingredient. A kept match replaces the model's own answer and records the page
   (`generic_from = "search"`, `generic_source`), so Review can say where it came from.

If Tavily is not set up or fails, the model's own match stays. Only the medicine's name leaves the
app. Matches are kept 30 days per name (a miss for one hour), shared by everyone.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from app.ai.client import AIClient
from app.ai.search import SearchError, SearchResult, WebSearch
from app.config import get_settings
from app.medicine_info import NOT_FOR_PATIENTS, TRUSTED_DOMAINS, site_name
from app.pipeline import prompts
from app.pipeline.medicines import _similar, _words
from app.pipeline.quotes import quote_in_source
from app.schemas.medicine import BrandMatch
from app.schemas.record import BillLine, Entry, EntryCheck, EntryKind
from app.storage import Storage

MISS_HOURS = 1


class _Read(BaseModel):
    name: str
    found: bool
    generics: list[str] = []
    source: int = 0
    quote: str = ""


class _Reads(BaseModel):
    items: list[_Read]


def key_for(name: str) -> str:
    """'Ondem 4 Tab' and 'ONDEM 4' share 'ondem': strength and dosage form do not change what
    is in a brand often enough to look it up twice."""
    return " ".join(_words(name)) or name.casefold().strip()


def _fresh(m: BrandMatch, now: datetime) -> bool:
    keep = timedelta(days=get_settings().medicine_info_days) if m.found else timedelta(hours=MISS_HOURS)
    return now - m.fetched_at < keep


def _names_each(quote: str, generics: list[str]) -> bool:
    """Every ingredient is named in the quote (loosely: 'Ondansetron' or a slight misspelling)."""
    words = _words(quote)
    return all(any(_similar(g, w) for w in words for g in _words(generic) if len(g) >= 4)
               for generic in generics)


_SHOP_WORDS = {"buy", "online", "order", "price", "uses"}


def _about(name: str, result: SearchResult) -> bool:
    """The page is this medicine's own page: the last part of its address
    ('/drugs/ondem-4-tablet-24555') or its title up to the first ':' or '|' ('Buy Ondem 4
    Tablet Online: ...') names exactly this medicine, strength and form aside. 'Ondem MD' or
    'Taxim-O CV', which hold other ingredients, are not 'Ondem' or 'Taxim-O'."""
    wanted = _words(name)
    slug = re.sub(r"\.html?$", "", result.url.rstrip("/").rsplit("/", 1)[-1])
    title = [w for w in _words(re.split(r"[:|]", result.title, maxsplit=1)[0]) if w not in _SHOP_WORDS]
    return bool(wanted) and wanted in (_words(slug), title)


_FORMS = {"tab": "tablet", "tabs": "tablet", "cap": "capsule", "caps": "capsule",
          "syp": "syrup", "inj": "injection", "susp": "suspension"}


def query_for(name: str) -> str:
    """'Ondem 4 Tab' -> 'Ondem 4 tablet salt composition': 1mg's brand pages say "Salt
    Composition", and this wording found the brand's own page where 'composition active
    ingredients' found only the generic's (build log, 2026-10-17)."""
    words = [_FORMS.get(w.casefold().strip("."), w) for w in name.split()]
    return f"{' '.join(words)} salt composition"


def lookup(names: list[str], ai: AIClient, search: WebSearch, db: Storage) -> dict[str, BrandMatch]:
    """Matches for these names, keyed by `key_for(name)`: from the cache, else searched. Misses
    are included (`found` False). Raises nothing: a failed search leaves the name out."""
    now = datetime.now(UTC)
    out: dict[str, BrandMatch] = {}
    todo: dict[str, tuple[str, list[SearchResult]]] = {}
    for name in names:
        key = key_for(name)
        if key in out or key in todo:
            continue
        saved = db.get_brand_match(key)
        if saved and _fresh(saved, now):
            out[key] = saved
            continue
        try:
            results = search.search(query_for(name), TRUSTED_DOMAINS, max_results=4)
        except SearchError:
            continue  # the model's own match stays
        # Only the medicine's own page goes to the model: given 'Ondem MD' and 'Ondem syrup'
        # pages too, it sometimes read a variant (build log, 2026-10-17).
        results = [r for r in results if not NOT_FOR_PATIENTS.search(r.url) and _about(name, r)]
        todo[key] = (name, results)

    # Results are numbered across the whole request, so a number names one page of one medicine
    # (numbering each medicine from 1 let the model mix them up: build log, 2026-10-17).
    pages: dict[int, tuple[str, SearchResult]] = {}
    blocks = []
    for key, (name, results) in todo.items():
        if not results:
            continue
        lines = []
        for r in results:
            pages[len(pages) + 1] = (key, r)
            lines.append(f"[{len(pages)}] {r.title} ({r.url})\n{r.content}")
        blocks.append(f"=== Medicine: {name} ===\n" + "\n\n".join(lines))
    reads: dict[str, _Read] = {}
    if blocks:
        try:
            answer = ai.reason_json(prompts.BRAND_INGREDIENTS, "\n\n".join(blocks), _Reads,
                                    thinking=False)
            reads = {key_for(r.name): r for r in answer.items}
        except Exception:  # noqa: BLE001 - the model's own match stays
            return out

    for key, (name, _results) in todo.items():
        match = BrandMatch(key=key, name=name, found=False)
        r = reads.get(key)
        if r and r.found and r.source in pages and pages[r.source][0] == key:
            page = pages[r.source][1]
            generics = [g.casefold().strip() for g in r.generics if g.strip()]
            if (generics and quote_in_source(r.quote, page.content)
                    and _names_each(r.quote, generics) and _about(name, page)):
                match = BrandMatch(key=key, name=name, found=True, generic_names=generics,
                                   url=page.url, site=site_name(page.url), quote=r.quote)
        db.save_brand_match(match)
        out[key] = match
    return out


def from_search(entries: list[Entry], bill_lines: list[BillLine], ai: AIClient,
                search: WebSearch | None, db: Storage | None) -> int:
    """Look up the medicines and drug bill lines that only the model matched (or nothing did),
    and use what a trusted site says instead. Returns how many were matched from a site."""
    if search is None or db is None or not search.available:
        return 0
    meds = [e for e in entries if e.kind == EntryKind.MEDICATION and e.medication
            and e.medication.generic_from in (None, "model")
            and EntryCheck.MEDICINE_UNNAMED not in e.checks]
    lines = [b for b in bill_lines if b.is_drug and b.generic_from in (None, "model")]
    if not meds and not lines:
        return 0
    found = lookup([e.label for e in meds] + [b.name for b in lines], ai, search, db)
    matched = 0
    for target, name in [(e.medication, e.label) for e in meds] + [(b, b.name) for b in lines]:
        m = found.get(key_for(name))
        if m and m.found:
            target.generic_names, target.generic_from, target.generic_source = (
                list(m.generic_names), "search", m.url)
            matched += 1
    return matched
