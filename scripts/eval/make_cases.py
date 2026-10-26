"""Draw the eval cases' documents: a prescription and a pharmacy bill per case, as phone photos.

Writes `samples/eval/<case id>/prescription.png` and `bill.png` from `cases.py`, with the same
drawing as Asha's demo documents (`scripts/make_demo_docs.py`). Synthetic people, clinics and
shops. From the repo root:
    backend\\.venv\\Scripts\\python scripts\\eval\\make_cases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # make_demo_docs
sys.path.insert(0, str(HERE))  # cases

from cases import CASES, Case
from make_demo_docs import SAMPLES, as_phone_photo, bill, prescription

OUT = SAMPLES / "eval"
DAY = "20/10/2026"


def draw(c: Case) -> None:
    folder = OUT / c.id
    folder.mkdir(parents=True, exist_ok=True)
    rx = prescription(
        "Greenleaf Clinic", "8 Station Road, Nashik  |  Ph: 0253-000-0000  (synthetic)",
        "Dr. S. Kale, MBBS   Reg. No. SYN-0042", "Dr. S. Kale",
        rows=[(f"Date: {DAY}", ""), (f"Patient: {c.patient}", "")],
        meds=list(c.rx), advice=["Review if not better."],
    )
    b = as_phone_photo(bill(  # the demo's bill is a clean scan; a patient's would be a photo too
        "Care Pharmacy", "2 Station Road, Nashik  |  DL No. SYN-DL-042  (synthetic)",
        [f"Bill No: SYN-{c.id}     Date: {DAY}", f"Patient: {c.patient}     Doctor: Dr. S. Kale"],
        list(c.bill),
    ))
    rx.save(folder / "prescription.png", optimize=True)
    b.save(folder / "bill.png", optimize=True)


def main() -> None:
    for c in CASES:
        draw(c)
    print(f"Drew {len(CASES)} cases in {OUT}")


if __name__ == "__main__":
    main()
