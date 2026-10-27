"""Web search through Tavily. The rest of the app talks to this class, never to Tavily directly.

Only search words leave the app (a medicine's name; for Ask, a few general words checked by
`assist.check_search_words`), never the patient's record.
API checked against docs.tavily.com (POST /search, Bearer key, include_domains) on 2026-10-12.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from app.config import Settings, get_settings

TAVILY_URL = "https://api.tavily.com/search"


class SearchError(RuntimeError):
    """The search could not run (no key, network, rate limit, Tavily error)."""


class SearchResult(BaseModel):
    title: str
    url: str
    content: str  # the snippets Tavily pulled from the page


class WebSearch:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self._transport = transport  # tests pass a fake transport
        self.calls = 0  # credits are counted per call (basic depth: 1 credit)

    @classmethod
    def off(cls) -> WebSearch:
        """A client that never searches (tests that bring their own storage)."""
        return cls(Settings(_env_file=None, TAVILY_API_KEY=""))

    @property
    def available(self) -> bool:
        return bool(self.settings.tavily_api_key)

    def search(self, query: str, domains: list[str], max_results: int = 6) -> list[SearchResult]:
        if not self.available:
            raise SearchError("No Tavily key is set")
        body = {
            "query": query,
            "search_depth": "basic",
            "chunks_per_source": 3,
            "max_results": max_results,
            "include_domains": domains,
            "include_answer": False,
            "include_raw_content": False,
        }
        headers = {"Authorization": f"Bearer {self.settings.tavily_api_key}"}
        try:
            with httpx.Client(timeout=self.settings.tavily_timeout_s,
                              transport=self._transport) as http:
                r = http.post(TAVILY_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise SearchError(f"Tavily did not answer: {exc.__class__.__name__}") from exc
        self.calls += 1
        if r.status_code != 200:
            raise SearchError(f"Tavily answered HTTP {r.status_code}")
        return [SearchResult.model_validate(x) for x in r.json().get("results", [])
                if x.get("url") and x.get("content")]
