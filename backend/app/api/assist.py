"""Before a visit, and Ask about my health: the routes.

Every route is the patient's own: anyone else gets 404, as for the rest of the record. A summary
for the doctor is saved in the record; an answer to Ask is not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.ai.client import AIError
from app.ai.search import SearchError
from app.api.routes import AI, DB, Search, _read_upload, own_patient
from app.assist import DailyLimit, ask, prepare_visit, reserve_prep
from app.auth import Me
from app.media import audio as audio_prep
from app.pipeline import prompts
from app.schemas.assist import AskAnswer, AskRequest, PrepRequest, VisitPrep, VoiceText

router = APIRouter()

_TRY_AGAIN = "Could not do this right now. Try again in a minute."

# Voice input (2026-10-27): short, in place of typing. Audio is never stored.
VOICE_MAX_SECONDS = 95  # the screen stops at 85 s; the rest is slack for the upload
VOICE_MAX_BYTES = 3 * 1024 * 1024
VOICES_PER_DAY = 30
VOICE_LIMIT_MESSAGE = f"You've used voice {VOICES_PER_DAY} times today. Please type instead."
VOICE_FAILED = "Couldn't hear that. Please type it instead."


def _prep(db: DB, me: Me, prep_id: str) -> VisitPrep:
    prep = db.get_visit_prep(prep_id)
    if prep is None:
        raise HTTPException(404, "Not found")
    own_patient(db, me, prep.patient_id)  # someone else's: the same 404
    return prep


@router.post("/patients/{patient_id}/visit-preps")
def create_visit_prep(patient_id: str, body: PrepRequest, db: DB, ai: AI, me: Me) -> VisitPrep:
    patient = own_patient(db, me, patient_id)
    try:
        reserve_prep(db, me.id)
    except DailyLimit as exc:
        raise HTTPException(429, str(exc)) from exc
    try:
        prep = prepare_visit(patient, db.list_entries(patient_id), db.list_encounters(patient_id),
                             body.symptoms, body.since or None, ai)
    except AIError as exc:
        raise HTTPException(502, _TRY_AGAIN) from exc
    db.save_visit_prep(prep)
    return prep


@router.get("/patients/{patient_id}/visit-preps")
def list_visit_preps(patient_id: str, db: DB, me: Me) -> list[VisitPrep]:
    own_patient(db, me, patient_id)
    return db.list_visit_preps(patient_id)


@router.get("/visit-preps/{prep_id}")
def get_visit_prep(prep_id: str, db: DB, me: Me) -> VisitPrep:
    return _prep(db, me, prep_id)


@router.delete("/visit-preps/{prep_id}", status_code=204)
def delete_visit_prep(prep_id: str, db: DB, me: Me) -> None:
    db.delete_visit_prep(_prep(db, me, prep_id).id)


@router.post("/patients/{patient_id}/ask")
def ask_about_health(patient_id: str, body: AskRequest, db: DB, ai: AI, search: Search,
                     me: Me) -> AskAnswer:
    patient = own_patient(db, me, patient_id)
    try:
        return ask(patient, db.list_entries(patient_id), db.list_encounters(patient_id),
                   body.question, me.id, db, ai, search, previous=body.previous)
    except DailyLimit as exc:
        raise HTTPException(429, str(exc)) from exc
    except (AIError, SearchError) as exc:
        raise HTTPException(502, _TRY_AGAIN) from exc


@router.post("/patients/{patient_id}/voice")
def voice_to_text(patient_id: str, audio: Annotated[UploadFile, File(description="A short recording")],
                  db: DB, ai: AI, me: Me) -> VoiceText:
    own_patient(db, me, patient_id)
    data = _read_upload(audio, "audio", VOICE_MAX_BYTES)  # in memory only, never stored
    now = datetime.now(UTC)
    key = f"voice:{me.id}"
    # Counted before decoding, so a flood of bad files cannot be used to burn CPU for free.
    if db.count_asks(key, now - timedelta(days=1)) >= VOICES_PER_DAY:
        raise HTTPException(429, VOICE_LIMIT_MESSAGE)
    db.record_ask(key, now)
    try:
        chunks = audio_prep.prepare(data, max_seconds=VOICE_MAX_SECONDS)
    except audio_prep.AudioError as exc:
        raise HTTPException(422, str(exc)) from exc
    used = getattr(ai, "used", [])
    used.clear()
    try:
        parts = [ai.transcribe(c.wav, prompts.VOICE_INPUT) for c in chunks]
    except AIError as exc:
        raise HTTPException(502, VOICE_FAILED) from exc
    text = " ".join(t.strip() for t in parts if t.strip())
    return VoiceText(text=text, models_used=sorted(set(used)))
