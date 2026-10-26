"""Read Asha's demo visits with the real pipeline and save them for the demo seed.

Makes `backend/app/demo_visits.json`, which `app/seed.py` loads on every start, so the demo
shows read visits instantly with no model calls:
1. An older blood pressure check-up from typed notes, dated 14 Jul 2026, all confirmed.
2. The planted prescription and bill (`samples/demo-asha/`), left waiting for review: the
   sulfa allergy clash (Septran DS) and Pantocid on the bill but not prescribed.
3. Two lab reports (15 Jun and 14 Sep 2026) for My health numbers. Their values are read as
   printed and checked here against what the documents say; a run that gets one wrong is not saved.

Runs in memory (nothing touches your database) and makes real model calls and Tavily searches,
so it needs the keys in backend/.env. Run it again when a prompt or the record shape changes,
and note the run. From the repo root:
    backend\\.venv\\Scripts\\python scripts\\make_demo_visits.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # settings read backend/.env

from fastapi.testclient import TestClient

from app.ai.client import AIClient
from app.ai.search import WebSearch
from app.api.auth import seed_demo_accounts
from app.main import create_app
from app.parameters import measure_of
from app.schemas.record import Encounter, EncounterStatus, EntryKind, EntryStatus, VisitDateSource
from app.seed import DEMO_PATIENT_ID, DEMO_VISITS, seed_demo
from app.storage import Storage

CHECKUP_NOTES = (
    "Blood pressure check-up with Dr. Kapoor at City Clinic. Doctor said my BP is 146/92, a "
    "little high. Continue Amlodipine 5 mg once a day in the morning. Cut down on salt and walk "
    "30 minutes a day. Get a lipid profile and kidney function test done. Review after 3 months."
)
CHECKUP_DAY = date(2026, 7, 14)
SAMPLES = ROOT / "samples" / "demo-asha"


# The two lab reports (`make_demo_docs.py`): their date, and the numbers printed on them by measure.
LABS: dict[str, tuple[date, dict[str, float]]] = {
    "lab-report-june.png": (date(2026, 6, 15), {
        "hba1c": 6.4, "cholesterol": 228, "ldl": 148, "hdl": 41, "triglycerides": 190,
        "glucose_fasting": 118, "creatinine": 0.9}),
    "lab-report-september.png": (date(2026, 9, 14), {
        "hba1c": 6.1, "cholesterol": 214, "ldl": 136, "hdl": 43, "triglycerides": 172,
        "glucose_fasting": 108, "creatinine": 0.9}),
}


def check_values(enc: Encounter, expected: dict[str, float]) -> None:
    """Every printed number must have been read, with the right value and its printed range; a
    run that missed or misread one is not saved (run it again)."""
    got = {measure_of(e.label).key: e for e in enc.entries
           if e.kind == EntryKind.RESULT and e.result and e.result.value is not None}
    problems = [f"{k}: missing" if k not in got else f"{k}: read {got[k].result.value}, printed {v}"
                for k, v in expected.items() if k not in got or got[k].result.value != v]
    problems += [f"{k}: no printed range kept" for k in expected
                 if k in got and got[k].result.ref_low is None and got[k].result.ref_high is None]
    print(f"  values: {len(got)} read, {len(expected)} expected, {len(problems)} problem(s); "
          f"left out: {enc.quote_failures}")
    if problems:
        raise SystemExit("A lab report was not read right, nothing saved:\n  " + "\n  ".join(problems))


def read_visit(client: TestClient, db: Storage, **upload: object) -> str:
    """Upload as Asha; the background job runs before the response returns."""
    t0 = time.time()
    r = client.post(f"/api/patients/{DEMO_PATIENT_ID}/encounters", **upload)
    r.raise_for_status()
    enc = db.get_encounter(r.json()["id"])
    assert enc is not None
    print(f"  {enc.status.value} in {time.time() - t0:.0f} s, {len(enc.entries)} entries, "
          f"{len(enc.discrepancies)} flags; models: {enc.models_used}")
    if enc.status != EncounterStatus.READY:
        raise SystemExit(f"The visit was not read: {enc.error}")
    return enc.id


def main() -> int:
    db = Storage()
    seed_demo(db, visits=False)
    seed_demo_accounts(db)
    client = TestClient(create_app(storage=db, ai=AIClient(), search=WebSearch()))
    client.post("/api/auth/demo", json={"key": "asha"}).raise_for_status()

    print("1. Check-up from typed notes")
    checkup_id = read_visit(client, db, data={"notes": CHECKUP_NOTES})
    checkup = db.get_encounter(checkup_id)
    assert checkup is not None
    checkup.visit_date, checkup.visit_date_source = CHECKUP_DAY, VisitDateSource.PATIENT
    checkup.created_at = datetime(2026, 7, 14, 6, 30, tzinfo=UTC)
    for e in checkup.entries:
        if e.status == EntryStatus.PROPOSED:
            e.set_status(EntryStatus.CONFIRMED, by=DEMO_PATIENT_ID)
    db.save_encounter(checkup)

    print("2. Planted prescription and bill")
    files = [("documents", (n, (SAMPLES / n).read_bytes(), "image/png"))
             for n in ("prescription.png", "bill.png")]
    planted_id = read_visit(client, db, files=files)

    lab_ids = []
    for n, (name, (day, expected)) in enumerate(LABS.items(), start=3):
        print(f"{n}. Lab report {day:%d %b %Y}")
        lab_id = read_visit(client, db, files=[("documents", (name, (SAMPLES / name).read_bytes(), "image/png"))])
        lab = db.get_encounter(lab_id)
        assert lab is not None
        check_values(lab, expected)
        lab.visit_date, lab.visit_date_source = day, VisitDateSource.DOCUMENT
        lab.created_at = datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)
        db.save_encounter(lab)
        lab_ids.append(lab_id)

    visits = sorted(
        (db.get_encounter(i) for i in [checkup_id, planted_id, *lab_ids]),
        key=lambda v: v.visit_date if v and v.visit_date else date.max,
    )
    data = [v.model_dump(mode="json") for v in visits if v is not None]
    DEMO_VISITS.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", "utf-8")
    for v in visits:
        assert v is not None
        print(f"\n{v.visit_date}:")
        for e in v.entries:
            print(f"  [{e.status.value}] {e.kind.value}: {e.label}")
        for d in v.discrepancies:
            print(f"  FLAG {d.check}: {d.message}")
    print(f"\nSaved {DEMO_VISITS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
