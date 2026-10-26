"""Medicine summaries: Tavily finds trusted pages, Nemotron Super sums them up, code checks.

- Only the medicine's name goes to Tavily (its active ingredients when known), never anything
  about the patient.
- Tavily searches a fixed list of trusted sites (decided 2026-10-12).
- Every point carries its source and a quote; code keeps a point only if the quote is in that
  source's text, the same rule as for record entries. Links come from Tavily's results only, so
  the model cannot invent one. Pages written for clinicians, forums and comparisons are skipped.
- A point that tells the reader to stop, start or skip a medicine is dropped (code, not the
  prompt alone): the app never tells a patient to change a medicine.
- Search by active ingredients first, then by the brand name if that found nothing usable.
- A summary is kept for 30 days per medicine (a miss for one hour), shared by everyone. It is
  fetched in the background when a medicine is confirmed, so tapping the name is usually instant.
- The screen adds a fixed note: general information, not advice; ask a doctor or pharmacist.
"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from app.ai.client import AIClient
from app.ai.search import SearchResult, WebSearch
from app.config import get_settings
from app.pipeline import prompts
from app.pipeline.medicines import from_table
from app.pipeline.quotes import quote_in_source
from app.schemas.medicine import InfoPoint, InfoSource, MedicineInfo
from app.storage import Storage

# The trusted sites and the names shown next to each point (decided 2026-10-12). Points keep the
# site's own words, so the app shows them as what that site says.
SITES = {"medlineplus.gov": "MedlinePlus", "nhs.uk": "NHS", "drugs.com": "Drugs.com",
         "mayoclinic.org": "Mayo Clinic", "1mg.com": "1mg"}
TRUSTED_DOMAINS = list(SITES)
SECTIONS = ("uses", "how_taken", "side_effects", "warnings")
MAX_POINTS = 4
MISS_HOURS = 1  # nothing found: a bad answer should not stick for long
# Pages that are not patient information about this medicine: prescribing information for
# clinicians, patient forums, comments, comparisons and interaction checkers (other medicines).
NOT_FOR_PATIENTS = re.compile(
    r"connect\.mayoclinic\.org|drugs\.com/(pro|monograph|ppa|dosage|comments|answers|"
    r"drug-interactions|compare)/"
)
# Instructions to change a medicine. Allowed: "get medical help", "tell your doctor".
DIRECTIVE = re.compile(r"\b(stop|stopping|start|starting|skip|skipping|discontinue)\b", re.IGNORECASE)


class _Point(BaseModel):
    text: str
    source: int
    quote: str


class _Summary(BaseModel):
    matches: bool
    uses: list[_Point] = Field(default_factory=list)
    how_taken: list[_Point] = Field(default_factory=list)
    side_effects: list[_Point] = Field(default_factory=list)
    warnings: list[_Point] = Field(default_factory=list)


def key_for(name: str, generic_names: list[str]) -> str:
    """One summary per active ingredient set: 'Dolo 650' and 'Crocin' share 'paracetamol'."""
    generics = sorted({g.strip().casefold() for g in generic_names if g.strip()})
    if generics:
        return " + ".join(generics)
    return "name:" + re.sub(r"\s+", " ", name.casefold()).strip()


def query_for(name: str, generic_names: list[str]) -> str:
    what = " and ".join(generic_names) if generic_names else name
    return f"{what} medicine uses side effects warnings"


def queries_for(name: str, generic_names: list[str]) -> list[str]:
    """By active ingredients first (shared by every brand), then by the name as written: Indian
    combination brands ('Ascoril LS') are often only described under their brand (1mg)."""
    queries = [query_for(name, generic_names)]
    if generic_names:
        queries.append(query_for(name, []))
    return queries


def _fresh(info: MedicineInfo, now: datetime) -> bool:
    age = now - info.fetched_at
    if info.found:
        return age < timedelta(days=get_settings().medicine_info_days)
    return age < timedelta(hours=MISS_HOURS)


def site_name(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/")[0].casefold()
    return next((name for domain, name in SITES.items()
                 if host == domain or host.endswith("." + domain)), host)


def _sources_block(results: list[SearchResult]) -> str:
    return "\n\n".join(f"[{i}] {r.title} ({r.url})\n{r.content}" for i, r in enumerate(results, 1))


def summarize(name: str, generic_names: list[str], results: list[SearchResult],
              ai: AIClient) -> MedicineInfo:
    """The model's summary, keeping only points whose quote is in their source."""
    info = MedicineInfo(key=key_for(name, generic_names), name=name,
                        generic_names=generic_names, found=False)
    results = [r for r in results if not NOT_FOR_PATIENTS.search(r.url)]
    if not results:
        return info
    what = f"{name} (active ingredients: {', '.join(generic_names)})" if generic_names else name
    answer = ai.reason_json(prompts.MEDICINE_SUMMARY,
                            f"Medicine: {what}\n\nSources:\n{_sources_block(results)}", _Summary,
                            max_tokens=8192, thinking=False)  # see the build log, 2026-10-12
    if not answer.matches:
        return info
    used: dict[int, int] = {}  # source number -> index in info.sources
    for section in SECTIONS:
        kept: list[InfoPoint] = []
        for p in getattr(answer, section):
            if not (1 <= p.source <= len(results)) or not p.text.strip() or DIRECTIVE.search(p.text):
                info.dropped += 1
                continue
            if not quote_in_source(p.quote, results[p.source - 1].content):
                info.dropped += 1
                continue
            if p.source not in used:
                r = results[p.source - 1]
                used[p.source] = len(info.sources)
                info.sources.append(InfoSource(title=r.title, url=r.url, site=site_name(r.url)))
            if any(k.text.casefold() == p.text.strip().casefold() for k in kept):
                continue  # the same point twice
            if len(kept) < MAX_POINTS:
                kept.append(InfoPoint(text=p.text.strip(), source=used[p.source], quote=p.quote))
        setattr(info, section, kept)
    info.found = any(getattr(info, s) for s in SECTIONS)
    return info


def medicine_info(name: str, generic_names: list[str], db: Storage, ai: AIClient,
                  search: WebSearch) -> MedicineInfo:
    """The saved summary if it is fresh, else a new lookup (which is then saved).

    Medicines saved before generic names existed send none: the brand table fills them in."""
    if not generic_names:
        generic_names = list(from_table(name) or [])
    key = key_for(name, generic_names)
    now = datetime.now(UTC)
    saved = db.get_medicine_info(key)
    if saved and _fresh(saved, now):
        return saved
    used = getattr(ai, "used", [])  # which model answered (a backup is marked)
    used.clear()
    searches = 0
    for query in queries_for(name, generic_names):
        searches += 1
        info = summarize(name, generic_names, search.search(query, TRUSTED_DOMAINS), ai)
        if info.found:
            break
    info.models_used = [*sorted(set(used)), *["tavily:search"] * searches]
    db.save_medicine_info(info)
    return info


def prewarm(name: str, generic_names: list[str], db: Storage, ai: AIClient,
            search: WebSearch) -> None:
    """Background job after a medicine is confirmed: fetch its summary now, quietly."""
    if not search.available:
        return
    # A failed warm-up only means the tap will look it up itself.
    with contextlib.suppress(Exception):
        medicine_info(name, generic_names, db, ai, search)
