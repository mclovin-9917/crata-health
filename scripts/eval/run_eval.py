"""Run the eval: every case in `cases.py` through the real app, scored against its answers.

Each case is a fresh patient (their allergy or current medicine confirmed in the record) who
uploads the case's prescription and bill (`samples/eval/<id>/`, drawn by `make_cases.py`). The app
reads them exactly as it does for a user: the documents on the perceive model, the facts on the
reason model, the checks in code. Nothing is tuned on these cases.

Settings (`--config`):
- main: the app as deployed for judging: Nano Omni on NVIDIA build reads, Nemotron 3 Super on
  Token Factory reasons, the backups on (which model answered is recorded per case).
- ultra: the same, with Nemotron 3 Ultra on Token Factory reasoning.
- gemma: the same as main, with Gemma 3 27B on Token Factory reading the documents.
- omni: Nano Omni reading with the backups off, so every read is Omni's (against gemma).

Runs in memory (nothing touches your database) and makes real model calls and Tavily searches,
so it needs the keys in backend/.env. Writes `scripts/eval/results/<config>-<time>.json` and prints
a summary. From the repo root:
    backend\\.venv\\Scripts\\python scripts\\eval\\run_eval.py --config main --runs 3
    backend\\.venv\\Scripts\\python scripts\\eval\\run_eval.py --config main --cases A1,B1 --runs 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "backend"
CASE_FILES = ROOT / "samples" / "eval"
RESULTS = HERE / "results"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"  # Token Factory model list, 2026-10-26
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"  # Token Factory model list, 2026-10-30

def _config(perceive: str, reason: str, model_perceive: str = "", model_reason: str = "",
            backups: bool = False) -> dict[str, str]:
    """Every setting that picks a model, so nothing in backend/.env can change a run unseen."""
    return {"PERCEIVE_PROVIDER": perceive, "REASON_PROVIDER": reason,
            "MODEL_PERCEIVE": model_perceive, "MODEL_REASON": model_reason,
            "FALLBACK": "true" if backups else "false"}


CONFIGS: dict[str, dict[str, str]] = {
    # As deployed: backups on. Which model answered each step is counted.
    "main": _config("nvidia", "nebius", backups=True),
    # The comparisons have no backups, so every answer comes from the model named. A hung call
    # fails the case (counted as a miss).
    "ultra": _config("nvidia", "nebius", model_reason=ULTRA),
    "nano": _config("nvidia", "nebius", model_reason=NANO),
    "omni": _config("nvidia", "nebius"),
    "gemma": _config("nebius", "nebius", model_perceive="google/gemma-3-27b-it"),
}

# USD per million tokens (input, cached input, output) on Token Factory, eu-north1, 2026-10-26:
# Super and Gemma worked out from the billing page (cost / usage), Ultra from the price list.
# NVIDIA build is free. A model with no price here is named in the cost line, never left out.
PRICES: dict[str, tuple[float, float, float] | None] = {
    "nebius:nvidia/nemotron-3-super-120b-a12b": (0.30, 0.06, 0.90),
    f"nebius:{ULTRA}": (1.00, 0.12, 3.00),
    "nebius:google/gemma-3-27b-it": (0.10, 0.10, 0.30),  # no cached line on the bill
    # Nano: Nebius's rate as listed on OpenRouter, 2026-10-30 (no cached rate found; charged as input).
    f"nebius:{NANO}": (0.06, 0.06, 0.24),
}


def setup(config: str) -> None:
    """Settings come from the environment before the app is imported (they are read once)."""
    os.environ.update(CONFIGS[config])
    sys.path.insert(0, str(BACKEND))
    sys.path.insert(0, str(HERE))
    os.chdir(BACKEND)  # settings read backend/.env


def run_case(client, db, case, run: int, photos: bool = False) -> dict:
    """One patient, one visit. Returns what the app found, raw, for scoring."""
    from app.schemas.account import Account, AccountRole
    from app.schemas.patient import Patient
    from app.schemas.record import (
        Entry,
        EntryKind,
        EntryStatus,
        Medication,
        Provenance,
        SourceType,
        Tier,
    )
    from tests.conftest import sign_in_as

    pid = f"eval-{case.id}-{run}"
    db.save_patient(Patient(id=pid, name=f"{case.patient} (synthetic)"))
    facts: list[Entry] = []
    if case.allergy:
        facts.append(Entry(kind=EntryKind.ALLERGY, label=case.allergy, tier=Tier.EMERGENCY_CARD))
    for label, ingredient in case.current:
        facts.append(Entry(kind=EntryKind.MEDICATION, label=label, tier=Tier.EMERGENCY_CARD,
                           medication=Medication(ongoing=True, started_on=date(2026, 1, 1),
                                                 generic_names=[ingredient])))
    for e in facts:
        e.patient_id = pid
        e.provenance = [Provenance(source_id="manual", source_type=SourceType.MANUAL)]
        e.set_status(EntryStatus.CONFIRMED, by=pid, note="eval")
        db.save_entry(e)
    sign_in_as(client, Account(role=AccountRole.PATIENT, name=case.patient, person_id=pid))

    if photos:  # the same pages as phone photos (photos.py), seeded per case and file
        from photos import phone_photo
        files = [("documents", (n[:-4] + ".jpg", phone_photo((CASE_FILES / case.id / n).read_bytes(), case.id, n),
                                "image/jpeg")) for n in ("prescription.png", "bill.png")]
    else:
        files = [("documents", (n, (CASE_FILES / case.id / n).read_bytes(), "image/png"))
                 for n in ("prescription.png", "bill.png")]
    t0 = time.time()
    r = client.post(f"/api/patients/{pid}/encounters", files=files)  # read before it returns
    seconds = time.time() - t0
    r.raise_for_status()
    view = client.get(f"/api/encounters/{r.json()['id']}").json()  # checks as Review shows them
    return {
        "case": case.id, "run": run, "seconds": round(seconds, 1), "status": view["status"],
        "error": view.get("error"), "models_used": view.get("models_used", {}),
        "step_seconds": view.get("step_seconds", {}),
        "discrepancies": [{"check": d["check"], "severity": d["severity"], "message": d["message"]}
                          for d in view.get("discrepancies", [])],
        "medicines": [{"label": e["label"], "status": e["status"],
                       "generic_names": (e.get("medication") or {}).get("generic_names", [])}
                      for e in view.get("entries", []) if e["kind"] == "medication"],
        "quote_failures": view.get("quote_failures", []),
    }


def score(case, got: dict) -> dict:
    """Compare one run with the case's answers. Plain code, no model. A visit that failed scores
    as finding nothing, so it counts as a miss, never as left out."""
    clashes = [d for d in got["discrepancies"] if d["check"] == "allergy_clash"]
    found = ("urgent" if any(d["severity"] == "urgent" for d in clashes)
             else "review" if clashes else None)
    # In every case that should clash, the medicine at fault is the prescription's first.
    culprit = [w.lower() for w in case.meds[0]] if case.clash else []
    names_culprit = any(w in d["message"].lower() for d in clashes for w in culprit)

    def mentions(msg: str, item: tuple[str, str]) -> bool:  # the bill line by brand or ingredient
        return item[0].split()[0].lower() in msg or item[1] in msg
    billed_msgs = [d["message"].lower() for d in got["discrepancies"]
                   if d["check"] == "billed_not_prescribed"]
    billed_caught = [b[0] for b in case.billed if any(mentions(m, b) for m in billed_msgs)]
    billed_false = [m for m in billed_msgs if not any(mentions(m, b) for b in case.billed)]

    meds = [m for m in got["medicines"] if m["status"] != "rejected"]
    def is_(m: dict, brand: str, ingredient: str) -> bool:
        return brand.lower() in m["label"].lower() or ingredient in [g.lower() for g in m["generic_names"]]
    meds_found = [b for b, ing in case.meds if any(is_(m, b, ing) for m in meds)]
    meds_extra = [m["label"] for m in meds if not any(is_(m, b, ing) for b, ing in case.meds)]
    return {
        "group": case.group, "allergy_recorded": case.allergy is not None,
        "clash_expected": case.clash, "clash_found": found, "clash_names_culprit": names_culprit,
        "billed_expected": [b[0] for b in case.billed], "billed_caught": billed_caught,
        "billed_false": billed_false,
        "meds_expected": len(case.meds), "meds_found": len(meds_found), "meds_extra": meds_extra,
        "quotes_rejected": len(got["quote_failures"]),
        # Steps a backup answered ("reading_documents", "extracting"...): reading apart from reasoning.
        "backup_steps": sorted(step for step, ms in got["models_used"].items()
                               if any("(backup)" in m for m in ms)),
    }


def summary(rows: list[dict], usage: dict) -> str:
    """Raw counts with rates, as the plan asks ("7 of 8")."""
    def pct(a: int, b: int) -> str:
        return f"{a} of {b} ({round(100 * a / b)}%)" if b else "none"
    ok = [r for r in rows if r["status"] == "ready"]
    s = [r["score"] for r in rows]  # every visit, a failed one as a miss
    pos = [x for x in s if x["clash_expected"]]
    # False alarms only where one could happen: an allergy in the record and nothing to flag.
    neg = [x for x in s if x["group"] == "allergy" and x["allergy_recorded"] and not x["clash_expected"]]
    no_allergy = [x for x in s if not x["allergy_recorded"]]
    exp_billed = sum(len(x["billed_expected"]) for x in s)
    lines = [
        f"Visits read: {pct(len(ok), len(rows))} (a visit not read counts as a miss below)",
        f"Allergy clash caught: {pct(sum(bool(x['clash_found']) for x in pos), len(pos))}",
        ("  with the right colour (red or amber): "
         f"{pct(sum(x['clash_found'] == x['clash_expected'] for x in pos), len(pos))}"),
        f"  naming the medicine at fault: {pct(sum(x['clash_names_culprit'] for x in pos), len(pos))}",
        ("Allergy false alarms, allergy recorded and nothing to flag (A6, A7): "
         f"{pct(sum(bool(x['clash_found']) for x in neg), len(neg))}"),
        f"Allergy false alarms, no allergy recorded: {pct(sum(bool(x['clash_found']) for x in no_allergy), len(no_allergy))}",
        f"Billed but not prescribed caught: {pct(sum(len(x['billed_caught']) for x in s), exp_billed)}",
        f"Billed false alarms: {sum(len(x['billed_false']) for x in s)} across {len(s)} visits",
        f"Medicines read: {pct(sum(x['meds_found'] for x in s), sum(x['meds_expected'] for x in s))}",
        f"Medicines not on the prescription: {sum(len(x['meds_extra']) for x in s)}",
        f"Quotes rejected by the code check: {sum(x['quotes_rejected'] for x in s)}",
    ]
    steps = sorted({st for x in s for st in x["backup_steps"]})
    lines += [f"Backup answered {st}: {pct(sum(st in x['backup_steps'] for x in s), len(s))}" for st in steps]
    if not steps:
        lines.append("Backup answered: never")
    secs = sorted(r["seconds"] for r in ok)
    if secs:
        n = len(secs)
        median = secs[n // 2] if n % 2 else round((secs[n // 2 - 1] + secs[n // 2]) / 2, 1)
        lines.append(f"Seconds per visit read: median {median}, min {secs[0]}, max {secs[-1]}")
    cost, unpriced = 0.0, []
    for key, t in usage.items():
        cached = t.get("cached_tokens", 0)
        lines.append(f"Tokens {key}: {t['calls']} calls, {t['prompt_tokens']} in ({cached} cached), "
                     f"{t['completion_tokens']} out")
        if key.startswith("nebius:"):
            price = PRICES.get(key)
            if price is None:
                unpriced.append(key)
            else:
                cost += ((t["prompt_tokens"] - cached) * price[0] + cached * price[1]
                         + t["completion_tokens"] * price[2]) / 1e6
    lines.append(f"Token Factory cost: ${cost:.4f}"
                 + (f" (not counted, no price: {', '.join(unpriced)})" if unpriced else ""))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=sorted(CONFIGS), default="main")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--cases", default="", help="comma-separated ids; all by default")
    ap.add_argument("--photos", action="store_true", help="read phone-photo versions (photos.py)")
    args = ap.parse_args()
    setup(args.config)

    from cases import CASES
    from fastapi.testclient import TestClient

    from app.ai.client import AIClient
    from app.ai.search import WebSearch
    from app.main import create_app
    from app.storage import Storage

    chosen = [c for c in CASES if not args.cases or c.id in args.cases.split(",")]
    ai = AIClient()
    print(f"config {args.config}{' (phone photos)' if args.photos else ''}: perceive {ai.settings.perceive.provider}:{ai.settings.perceive.model}, "
          f"reason {ai.settings.reason.provider}:{ai.settings.reason.model}, "
          f"backups {'on' if ai.settings.fallback else 'off'}")
    db = Storage()  # one in-memory database for the whole run (the brand-name cache is shared)
    client = TestClient(create_app(storage=db, ai=ai, search=WebSearch()))
    rows: list[dict] = []
    for run in range(1, args.runs + 1):
        for case in chosen:
            try:
                got = run_case(client, db, case, run, photos=args.photos)
            except Exception as exc:  # noqa: BLE001  (a failed call is a result too, not a reason to stop)
                got = {"case": case.id, "run": run, "seconds": 0, "status": "error", "error": str(exc),
                       "models_used": {}, "step_seconds": {}, "discrepancies": [], "medicines": [],
                       "quote_failures": []}
            got["score"] = score(case, got)
            rows.append(got)
            sc = got["score"]
            print(f"run {run} {case.id}: {got['status']} {got['seconds']} s, clash {sc['clash_found']} "
                  f"(want {sc['clash_expected']}), billed {sc['billed_caught']}/{sc['billed_expected']} "
                  f"false {len(sc['billed_false'])}, meds {sc['meds_found']}/{sc['meds_expected']}"
                  f"{' backup: ' + ', '.join(sc['backup_steps']) if sc['backup_steps'] else ''}", flush=True)

    usage = ai.usage()
    text = summary(rows, usage)
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    out = RESULTS / f"{args.config}{'-photos' if args.photos else ''}-{stamp}.json"
    out.write_text(json.dumps({"config": args.config, "runs": args.runs, "photos": args.photos,
                               "settings": CONFIGS[args.config], "usage": usage, "summary": text,
                               "rows": rows}, indent=1) + "\n", "utf-8")
    print("\n" + text + f"\n\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
