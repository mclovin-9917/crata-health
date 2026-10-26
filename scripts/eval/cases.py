"""The eval's held-out cases. Synthetic people and documents.

No prompt is tuned on these: they only measure. Each case is one patient's record (an allergy or a
current medicine, or nothing) plus one visit's prescription and pharmacy bill, drawn as phone
photos by `make_cases.py`. The answers say what the app should find:
- `clash`: "urgent" (same ingredient or family: red), "review" (related family: amber) or None.
- `billed`: the bill lines that should be flagged as billed but not prescribed, as (item, active
  ingredient); a flag naming either counts.
- `meds`: the prescription's medicines as (brand on the page, active ingredient), to score
  whether each was read.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    id: str
    group: str  # "allergy" or "billed"
    patient: str
    allergy: str | None  # in the patient's record, confirmed
    current: tuple[tuple[str, str], ...]  # current medicines in the record: (label, ingredient)
    rx: tuple[str, ...]  # prescription lines as printed
    bill: tuple[tuple[str, int, float], ...]  # (item, quantity, rate)
    meds: tuple[tuple[str, str], ...]  # (brand on the prescription, active ingredient)
    clash: str | None = None
    billed: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _line(n: int, text: str) -> str:
    return f"{n}. {text}"


CASES: list[Case] = [
    # ---- allergy: 5 that should be flagged, 3 that should not --------------------------------
    Case("A1", "allergy", "Kiran Rao", "Penicillin", (),
         (_line(1, "Cap Mox 500 mg            1-0-1  x 5 days"),
          _line(2, "Tab Dolo 650              SOS if fever")),
         (("Mox 500 Cap", 10, 7.20), ("Dolo 650 Tab", 10, 2.04)),
         (("Mox", "amoxicillin"), ("Dolo", "paracetamol")), clash="urgent"),
    Case("A2", "allergy", "Leela Nair", "Penicillin", (),
         (_line(1, "Tab Augmentin 625 mg      1-0-1  x 5 days  (after food)"),
          _line(2, "Tab Pan 40                1-0-0  before breakfast x 5 days")),
         (("Augmentin 625 Duo Tab", 10, 22.40), ("Pan 40 Tab", 5, 11.80)),
         (("Augmentin", "amoxicillin"), ("Pan", "pantoprazole")), clash="urgent"),
    Case("A3", "allergy", "Farhan Sheikh", "Sulfa drugs", (),
         (_line(1, "Tab Bactrim DS            1-0-1  x 3 days"),
          _line(2, "Tab Dolo 650              1-1-1  x 3 days")),
         (("Bactrim DS Tab", 6, 4.50), ("Dolo 650 Tab", 9, 2.04)),
         (("Bactrim", "sulfamethoxazole"), ("Dolo", "paracetamol")), clash="urgent"),
    Case("A4", "allergy", "Priya Menon", "Ibuprofen", (),
         (_line(1, "Tab Brufen 400            1-0-1  after food x 3 days"),
          _line(2, "Tab Pan 40                1-0-0  x 3 days")),
         (("Brufen 400 Tab", 6, 3.10), ("Pan 40 Tab", 3, 11.80)),
         (("Brufen", "ibuprofen"), ("Pan", "pantoprazole")), clash="urgent"),
    Case("A5", "allergy", "Rahul Bose", "Penicillin", (),
         (_line(1, "Tab Taxim-O 200           1-0-1  x 5 days"),
          _line(2, "Tab Dolo 650              SOS if fever")),
         (("Taxim-O 200 Tab", 10, 11.50), ("Dolo 650 Tab", 10, 2.04)),
         (("Taxim-O", "cefixime"), ("Dolo", "paracetamol")), clash="review"),
    Case("A6", "allergy", "Sunita Iyer", "Penicillin", (),
         (_line(1, "Tab Azithral 500          1-0-0  x 3 days"),
          _line(2, "Tab Dolo 650              SOS if fever")),
         (("Azithral 500 Tab", 3, 23.70), ("Dolo 650 Tab", 10, 2.04)),
         (("Azithral", "azithromycin"), ("Dolo", "paracetamol"))),
    Case("A7", "allergy", "Vikram Joshi", "Sulfa drugs", (),
         (_line(1, "Tab Dolo 650              1-1-1  x 3 days"),
          _line(2, "Tab Allegra 120           0-0-1  x 5 days")),
         (("Dolo 650 Tab", 9, 2.04), ("Allegra 120 Tab", 5, 19.60)),
         (("Dolo", "paracetamol"), ("Allegra", "fexofenadine"))),
    Case("A8", "allergy", "Neha Kulkarni", None, (),
         (_line(1, "Cap Mox 500 mg            1-0-1  x 5 days"),
          _line(2, "Tab Pan 40                1-0-0  x 5 days")),
         (("Mox 500 Cap", 10, 7.20), ("Pan 40 Tab", 5, 11.80)),
         (("Mox", "amoxicillin"), ("Pan", "pantoprazole"))),
    # ---- billed but not prescribed: 4 that should be flagged, 2 that should not ---------------
    Case("B1", "billed", "Arjun Pillai", None, (),
         (_line(1, "Tab Azithral 500          1-0-0  x 3 days"),
          _line(2, "Tab Dolo 650              SOS if fever")),
         (("Azithral 500 Tab", 3, 23.70), ("Dolo 650 Tab", 10, 2.04), ("Pan 40 Tab", 10, 11.80)),
         (("Azithral", "azithromycin"), ("Dolo", "paracetamol")), billed=(("Pan 40", "pantoprazole"),)),
    Case("B2", "billed", "Meghna Das", None, (),
         (_line(1, "Tab Augmentin 625 mg      1-0-1  x 5 days"),
          _line(2, "Tab Montair LC            0-0-1  x 7 days")),
         (("Augmentin 625 Duo Tab", 10, 22.40), ("Montair LC Tab", 7, 16.90),
          ("Allegra 120 Tab", 10, 19.60)),
         (("Augmentin", "amoxicillin"), ("Montair LC", "montelukast")), billed=(("Allegra", "fexofenadine"),)),
    Case("B3", "billed", "Suresh Reddy", None, (),
         (_line(1, "Tab Glycomet 500           1-0-1  after food, continue"),),
         (("Glycomet 500 Tab", 60, 1.90), ("Telma 40 Tab", 30, 7.40)),
         (("Glycomet", "metformin"),), billed=(("Telma", "telmisartan"),)),
    Case("B4", "billed", "Anita George", None, (),
         (_line(1, "Tab Dolo 650              1-1-1  x 3 days"),),
         (("Dolo 650 Tab", 9, 2.04), ("Brufen 400 Tab", 10, 3.10)),
         (("Dolo", "paracetamol"),), billed=(("Brufen", "ibuprofen"),)),
    Case("B5", "billed", "Imran Qureshi", None, (),
         (_line(1, "Cap Mox 500 mg            1-0-1  x 5 days"),
          _line(2, "Tab Dolo 650              SOS if fever")),
         (("Mox 500 Cap", 10, 7.20), ("Dolo 650 Tab", 10, 2.04)),
         (("Mox", "amoxicillin"), ("Dolo", "paracetamol"))),
    Case("B6", "billed", "Kavya Shetty", None, (("Amlong 5 mg", "amlodipine"),),
         (_line(1, "Tab Azithral 500          1-0-0  x 3 days"),),
         (("Azithral 500 Tab", 3, 23.70), ("Amlong 5 Tab", 30, 3.20)),
         (("Azithral", "azithromycin"),)),
]
