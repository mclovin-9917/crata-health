"""FastAPI app factory.

Run locally from `backend/`:  .venv\\Scripts\\python -m uvicorn app.main:create_app --factory --reload

The API lives under /api. If the frontend has been built (frontend/dist), it is served from /,
so the whole app is one process at one URL. The frontend uses hash routes (/#/review/...), so
no server-side fallback is needed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser
from starlette.responses import Response
from starlette.types import Scope

from app.ai.client import AIClient
from app.ai.search import WebSearch
from app.api.access import router as access_router
from app.api.assist import VOICE_MAX_BYTES
from app.api.assist import router as assist_router
from app.api.auth import router as auth_router
from app.api.auth import seed_demo_accounts
from app.api.routes import MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, MAX_PDF_BYTES, router
from app.config import get_settings
from app.pipeline.run import recover_interrupted_reads
from app.seed import seed_demo
from app.sms import SmsSender, sms_sender
from app.storage import Storage

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

# Starlette keeps an uploaded file in memory only up to 1 MB and then spills it to a temporary
# file on disk. Media must never touch the server's disk, so the limit is raised above the
# largest upload any route accepts (plus a margin): every upload stays in memory.
MultiPartParser.spool_max_size = (
    max(MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, MAX_PDF_BYTES, VOICE_MAX_BYTES) + 4 * 1024 * 1024
)


class FrontendFiles(StaticFiles):
    """The built frontend. Browsers check index.html on every load, so a new build shows up
    without a hard refresh; files in assets/ have a content hash in their name and never change,
    so they are cached for good."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if Path(path).parts[:1] == ("assets",):  # Starlette uses backslashes here on Windows
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    storage: Storage | None = None,
    ai: AIClient | None = None,
    search: WebSearch | None = None,
    sms: SmsSender | None = None,
) -> FastAPI:
    """Tests pass their own storage and get an empty app; a real start seeds the demo data."""
    app = FastAPI(title="Crata Health prototype", version="0.1.0")
    seeded = storage is None
    if storage is None:
        storage = Storage(get_settings().database_path)
        seed_demo(storage)
        seed_demo_accounts(storage)
        recover_interrupted_reads(storage)
    app.state.storage = storage
    app.state.ai = ai or AIClient()
    # Like the seed, real searches only on a real start: tests never call Tavily by accident.
    app.state.search = search or (WebSearch() if seeded else WebSearch.off())
    app.state.sms = sms or sms_sender(get_settings().sms_provider)
    app.include_router(router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(access_router, prefix="/api")
    app.include_router(assist_router, prefix="/api")
    if FRONTEND_DIST.is_dir():
        app.mount("/", FrontendFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app
