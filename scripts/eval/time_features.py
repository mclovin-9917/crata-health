"""Time and cost of the assistant features, with the real models, as the hosted demo runs them.

Before a visit (the quick questions, then the summary) and Ask about my health (a question about
the record, and a food question that searches trusted sites) are run N times each for the demo
patient Asha on a fresh database. Prints the seconds of each call (median, min, max) and the
tokens and Token Factory cost per feature. Synthetic data only.

    backend\\.venv\\Scripts\\python scripts\\eval\\time_features.py --n 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "backend"

SYMPTOMS = [
    "My right knee has been painful and a little swollen for a week, worse when I climb stairs.",
    "I have had a dry cough and a sore throat for four days.",
    "Burning feeling in my stomach after meals for two weeks.",
    "Itchy red rash on both arms for three days, worse at night.",
    "Lower back pain for ten days after lifting a heavy box.",
]
FOODS = ["papaya", "banana", "brown rice", "coconut water", "dates"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()

    db_path = Path(tempfile.mkdtemp()) / "timing.sqlite3"
    os.environ.update({"DATABASE_PATH": str(db_path), "REASON_PROVIDER": "nebius", "PERCEIVE_PROVIDER": "nvidia"})
    sys.path.insert(0, str(BACKEND))
    sys.path.insert(0, str(HERE))
    os.chdir(BACKEND)

    from app.ai.client import AIClient
    from app.main import create_app
    from fastapi.testclient import TestClient
    from run_eval import PRICES

    ai = AIClient()
    client = TestClient(create_app(ai=ai))  # a real start: the demo people are seeded
    r = client.post("/api/auth/demo", json={"key": "asha"})
    r.raise_for_status()
    pid = client.get("/api/auth/me").json()["person_id"]

    def timed(url: str, body: dict):
        t0 = time.time()
        res = client.post(url, json=body)
        return round(time.time() - t0, 1), res

    def usage_now():
        return json.loads(json.dumps(ai.usage()))

    def cost(before, after):
        total = 0.0
        for key, t in after.items():
            b = before.get(key, {})
            if not key.startswith("nebius:") or PRICES.get(key) is None:
                continue
            p_in, p_cached, p_out = PRICES[key]
            prompt = t["prompt_tokens"] - b.get("prompt_tokens", 0)
            cached = t.get("cached_tokens", 0) - b.get("cached_tokens", 0)
            out = t["completion_tokens"] - b.get("completion_tokens", 0)
            total += ((prompt - cached) * p_in + cached * p_cached + out * p_out) / 1e6
        return total

    results: dict[str, list[float]] = {"prep_questions": [], "prep_summary": [], "ask_record": [], "ask_food": []}
    costs: dict[str, float] = {"before_a_visit": 0.0, "ask_record": 0.0, "ask_food": 0.0}
    for i in range(args.n):
        u0 = usage_now()
        s, q = timed(f"/api/patients/{pid}/visit-preps/questions", {"symptoms": SYMPTOMS[i % len(SYMPTOMS)]})
        results["prep_questions"].append(s)
        qs = q.json() if q.status_code == 200 else {"questions": []}
        answers = [{"question": x["text"], "answer": x["choices"][0]}
                   for x in qs.get("questions", []) if x.get("choices")]
        s, p = timed(f"/api/patients/{pid}/visit-preps",
                     {"symptoms": SYMPTOMS[i % len(SYMPTOMS)], "answers": answers})
        results["prep_summary"].append(s)
        u1 = usage_now()
        costs["before_a_visit"] += cost(u0, u1)
        s, a = timed(f"/api/patients/{pid}/ask", {"question": "How have my sugar levels been?", "previous": None})
        results["ask_record"].append(s)
        u2 = usage_now()
        costs["ask_record"] += cost(u1, u2)
        s, a = timed(f"/api/patients/{pid}/ask",
                     {"question": f"Is {FOODS[i % len(FOODS)]} good for me?", "previous": None})
        results["ask_food"].append(s)
        costs["ask_food"] += cost(u2, usage_now())
        print(f"round {i + 1}: questions {results['prep_questions'][-1]} s ({q.status_code}), "
              f"summary {results['prep_summary'][-1]} s ({p.status_code}), "
              f"ask record {results['ask_record'][-1]} s, ask food {results['ask_food'][-1]} s ({a.status_code})",
              flush=True)

    def stats(xs):
        xs = sorted(xs)
        n = len(xs)
        med = xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 1)
        return f"median {med} s, min {xs[0]} s, max {xs[-1]} s"

    lines = [f"Runs: {args.n} of each, demo patient Asha, reasoning on Token Factory (Nemotron 3 Super)"]
    lines += [f"{k}: {stats(v)}" for k, v in results.items()]
    lines += [f"Token Factory cost per use, {k}: ${v / args.n:.5f}" for k, v in costs.items()]
    lines.append("Web search (Tavily) for the food question is not in the cost above.")
    text = "\n".join(lines)
    out = HERE / "results" / f"features-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}.json"
    out.write_text(json.dumps({"n": args.n, "seconds": results, "cost_per_use": {k: v / args.n for k, v in costs.items()},
                               "usage": ai.usage(), "summary": text}, indent=1) + "\n", "utf-8")
    print("\n" + text + f"\n\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
