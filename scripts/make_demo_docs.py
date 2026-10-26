"""Render synthetic documents to test uploads. All people, clinics and numbers are made up.

Run from the repo root:

    backend\\.venv\\Scripts\\python scripts\\make_demo_docs.py

Writes two sets (see docs/testing-guide.md for what each one should show):

samples/demo-asha/ (the demo patient, who has a sulfa allergy in her record):
    prescription.png (a slightly skewed "phone photo"), bill.png, and documents.pdf (both
    pages). Planted: Septran DS is co-trimoxazole, a sulfa drug (allergy clash, red); Pantocid
    40 is billed but not prescribed; syringes are a non-drug bill line.
    lab-report-june.png and lab-report-september.png: two lab reports (HbA1c, lipids, fasting
    sugar, creatinine, with printed ranges) that give My health numbers a trend.

samples/test-meera/ (a patient you register yourself, who first adds a penicillin allergy):
    visit2-prescription.png, visit2-bill.png, visit2.pdf (both pages): Cefixime is a
    cephalosporin, related to penicillins (amber); Montair LC prescribed but plain Montair
    billed (different mix); Ondem 4 billed, never prescribed (a brand the table doesn't know, so
    the AI names it); Thyronorm is a refill of a medicine already in her record (no flag);
    cotton is a non-drug line.
    visit3-bill-only.png: a bill on its own. Its medicines become suggestions; Amoxicillin on
    it clashes with her penicillin allergy (red) until she says it was not hers.
    stay-discharge-summary.png, stay-hospital-bill.png: documents for a hospital stay she logs
    (10 to 13 Oct 2026). Home medicines become suggestions, those given in hospital notes; the
    discharge date is suggested; ceftriaxone on the bill is related to penicillin (amber).
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
W, H = 1240, 1754  # A4 at 150 dpi


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in (("arialbd.ttf" if bold else "arial.ttf"), "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def write(d: ImageDraw.ImageDraw, xy, text: str, font, fill=(25, 25, 25)) -> None:
    """Pillow draws text white on RGB images unless told otherwise, so always pass ink."""
    d.text(xy, text, font=font, fill=fill)


def page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), "white")
    return img, ImageDraw.Draw(img)


def prescription(clinic: str, address: str, doctor: str, signer: str,
                 rows: list[tuple[str, str]], meds: list[str], advice: list[str]) -> Image.Image:
    img, d = page()
    d.rectangle([0, 0, W, 170], fill=(225, 238, 250))
    write(d, (60, 40), clinic, font=font(48, True), fill=(20, 60, 120))
    write(d, (60, 105), address, font=font(24), fill=(60, 60, 60))
    write(d, (60, 210), doctor, font=font(26, True))
    d.line([60, 260, W - 60, 260], fill="black", width=2)

    f = font(28)
    y = 290
    for left, right in rows:
        write(d, (60, y), left, font=f)
        if right:
            write(d, (760, y), right, font=f)
        y += 50

    write(d, (60, y + 20), "Rx", font=font(64, True))
    y += 110
    for m in meds:
        write(d, (80, y), m, font=font(30))
        y += 64

    y += 30
    for line in advice:
        write(d, (60, y), line, font=f)
        y += 50
    write(d, (W - 380, H - 220), signer, font=font(30, True))
    write(d, (W - 380, H - 180), "(signature, synthetic)", font=font(22), fill=(90, 90, 90))
    return as_phone_photo(img)


def bill(shop: str, address: str, header: list[str],
         items: list[tuple[str, int, float]]) -> Image.Image:
    img, d = page()
    write(d, (60, 50), shop, font=font(46, True))
    write(d, (60, 110), address, font=font(24))
    for i, line in enumerate(header):
        write(d, (60, 170 + 40 * i), line, font=font(26))
    d.line([60, 260, W - 60, 260], fill="black", width=2)

    cols = [60, 620, 800, 980]
    head = ["Item", "Qty", "Rate", "Amount"]
    for x, h in zip(cols, head, strict=True):
        write(d, (x, 280), h, font=font(28, True))
    d.line([60, 325, W - 60, 325], fill="black", width=1)

    y, total = 345, 0.0
    for name, qty, rate in items:
        amount = qty * rate
        total += amount
        for x, text in zip(cols, [name, str(qty), f"{rate:.2f}", f"{amount:.2f}"], strict=True):
            write(d, (x, y), text, font=font(28))
        y += 56
    d.line([60, y + 10, W - 60, y + 10], fill="black", width=1)
    write(d, (cols[2], y + 30), "Total", font=font(30, True))
    write(d, (cols[3], y + 30), f"Rs. {total:.2f}", font=font(30, True))
    write(d, (60, H - 160), "Goods once sold will not be taken back.  (synthetic bill)", font=font(22),
          fill=(90, 90, 90))
    return img


def as_phone_photo(img: Image.Image) -> Image.Image:
    """A little skew, warm paper tone and blur, like a phone photo of a printout."""
    rng = random.Random(7)
    tinted = Image.blend(img, Image.new("RGB", img.size, (245, 238, 220)), 0.25)
    rotated = tinted.rotate(rng.uniform(-2.5, 2.5), expand=True, fillcolor=(90, 90, 95))
    return rotated.filter(ImageFilter.GaussianBlur(0.8))


# ---- Asha (demo patient) -----------------------------------------------------------------------
def asha() -> dict[str, Image.Image]:
    rx = prescription(
        "Sunrise Family Clinic", "12 Lake Road, Pune  |  Ph: 020-0000-0000  (synthetic)",
        "Dr. A. Mehta, MBBS, MD (Medicine)   Reg. No. SYN-0001", "Dr. A. Mehta",
        rows=[("Date: 12/10/2026", ""), ("Patient: Asha Verma", "Age/Sex: 35 / F"),
              ("Complaints: Fever and cough for 4 days", ""),
              ("Diagnosis: Acute bronchitis", ""), ("Known allergy: Sulfa drugs", "")],
        meds=["1. Tab Augmentin 625 mg        1-0-1  x 5 days   (after food)",
              "2. Tab Septran DS              1-0-1  x 5 days",
              "3. Syp Ascoril LS  10 ml       1-1-1  x 5 days",
              "4. Tab Dolo 650                SOS if fever > 100 F"],
        advice=["Advice: Plenty of fluids. Steam inhalation twice a day.",
                "Review after 5 days if not better."],
    )
    b = bill(
        "Wellness Pharmacy", "Shop 4, Lake Road, Pune  |  DL No. SYN-DL-123  (synthetic)",
        ["Bill No: SYN-4471     Date: 12/10/2026", "Patient: Asha Verma     Doctor: Dr. A. Mehta"],
        [("Augmentin 625 Duo Tab", 10, 22.40), ("Septran DS Tab", 10, 3.10),
         ("Ascoril LS Syrup 100 ml", 1, 118.00), ("Dolo 650 Tab", 15, 2.04),
         ("Pantocid 40 Tab", 10, 14.50), ("Syringe 5 ml", 2, 8.00)],
    )
    return {"prescription.png": rx, "bill.png": b}


# ---- Meera (a patient you register) ------------------------------------------------------------
def meera() -> dict[str, Image.Image]:
    rx = prescription(
        "Greenleaf Clinic", "8 Park Street, Bengaluru  |  Ph: 080-0000-0000  (synthetic)",
        "Dr. N. Rao, MBBS, DNB (Family Medicine)   Reg. No. SYN-2002", "Dr. N. Rao",
        rows=[("Date: 14/10/2026", ""), ("Patient: Meera Iyer", "Age/Sex: 41 / F"),
              ("Complaints: Sore throat and fever for 3 days", ""),
              ("Diagnosis: Acute pharyngitis", ""), ("Known allergy: Penicillin", "")],
        meds=["1. Tab Cefixime 200 mg         1-0-1  x 5 days   (after food)",
              "2. Tab Montair LC              0-0-1  x 10 days",
              "3. Tab Dolo 650                SOS if fever > 100 F"],
        advice=["Advice: Warm salt water gargles. Rest your voice.",
                "Review after 5 days if not better."],
    )
    b = bill(
        "Greenleaf Pharmacy", "8 Park Street, Bengaluru  |  DL No. SYN-DL-456  (synthetic)",
        ["Bill No: SYN-8802     Date: 14/10/2026", "Patient: Meera Iyer     Doctor: Dr. N. Rao"],
        [("Cefixime 200 Tab", 10, 9.80), ("Montair 10 Tab", 10, 16.20),
         ("Dolo 650 Tab", 10, 2.04), ("Thyronorm 50 mcg Tab", 30, 1.90),
         ("Ondem 4 Tab", 6, 5.50), ("Cotton Roll 50 g", 1, 35.00)],
    )
    bill_only = bill(
        "City Medicals", "21 Market Road, Bengaluru  |  DL No. SYN-DL-789  (synthetic)",
        ["Bill No: SYN-1190     Date: 15/10/2026", "Customer: Meera Iyer"],
        [("Calpol 500 Tab", 15, 1.10), ("Amoxicillin 500 Cap", 15, 6.40),
         ("ORS Sachet", 5, 21.00), ("Gloves 1 pair", 1, 15.00)],
    )
    return {"visit2-prescription.png": rx, "visit2-bill.png": b,
            "visit3-bill-only.png": bill_only, **meera_stay(), **meera_tests()}


def text_page(header: str, address: str, lines: list[str]) -> Image.Image:
    """A plain printed page: a hospital letterhead and lines of text (bold when they end in ':')."""
    img, d = page()
    d.rectangle([0, 0, W, 150], fill=(232, 240, 236))
    write(d, (60, 35), header, font=font(44, True), fill=(30, 80, 60))
    write(d, (60, 95), address, font=font(24), fill=(60, 60, 60))
    y = 200
    for line in lines:
        bold = line.endswith(":")
        write(d, (60 if bold or not line.startswith(" ") else 90, y), line.strip(),
              font=font(28, bold))
        y += 46 if line else 24
    return img


def meera_stay() -> dict[str, Image.Image]:
    """A hospital stay to add after logging it (testing guide, section 6b): a discharge summary
    and the hospital's bill. Ceftriaxone given in hospital is related to her penicillin allergy."""
    summary = text_page(
        "City Hospital", "Ring Road, Bengaluru  |  Ph: 080-1111-0000  (synthetic)",
        ["DISCHARGE SUMMARY", "",
         "Patient: Meera Iyer     Age/Sex: 41 / F     IP No: SYN-IP-3307",
         "Admitted on 10/10/2026     Discharged on 13/10/2026", "",
         "Diagnosis:", "  Community acquired pneumonia (right lower lobe)", "",
         "Investigations:", "  Chest X-ray: right lower lobe consolidation",
         "  CBC: total leucocyte count 14,200 per cu mm", "",
         "Treatment given in hospital:", "  Inj Ceftriaxone 1 g IV twice daily",
         "  Inj Pantoprazole 40 mg IV once daily", "  Nebulisation with Duolin thrice daily", "",
         "Condition at discharge: stable, afebrile", "",
         "Medicines to continue at home:", "  1. Tab Azithral 500 mg once daily for 3 days",
         "  2. Tab Dolo 650 if fever above 100 F", "  3. Syp Ascoril LS 10 ml thrice daily for 5 days",
         "",
         "Follow-up:", "  Review in OPD after 1 week with a chest X-ray.", "",
         "Dr. S. Kulkarni, MD (Medicine)   Reg. No. SYN-3003  (synthetic)"],
    )
    hospital_bill = bill(
        "City Hospital - Pharmacy", "Ring Road, Bengaluru  |  DL No. SYN-DL-990  (synthetic)",
        ["Bill No: SYN-IPB-5521     Date: 13/10/2026", "Patient: Meera Iyer     IP No: SYN-IP-3307"],
        [("Inj Ceftriaxone 1 g", 6, 58.00), ("Inj Pantoprazole 40 mg", 3, 49.00),
         ("Duolin Respules", 9, 22.50), ("IV Set", 2, 85.00), ("Bed charges (3 days)", 1, 4500.00)],
    )
    return {"stay-discharge-summary.png": summary, "stay-hospital-bill.png": hospital_bill}


def asha_labs() -> dict[str, Image.Image]:
    """Two lab reports for the demo patient, three months apart, for My health numbers: HbA1c,
    a lipid profile, fasting sugar and creatinine, each with the range the lab prints (some as
    "up to 200"). Most values are a little over their range and come down a little by September,
    so the screen shows a trend and the amber markers."""
    def report(printed: str, rows: dict[str, str]) -> Image.Image:
        lines = ["LABORATORY REPORT", "",
                 "Patient: Asha Verma     Age/Sex: 35 / F     Lab No: SYN-LAB-" + printed[-2:] + "20",
                 f"Collected on {printed}     Reported on {printed}", ""]
        for panel, row in rows.items():
            lines += [panel + ":", f"  {row}", ""]
        lines.append("Dr. R. Kulkarni, MD (Pathology)   Reg. No. SYN-5005  (synthetic)")
        return text_page("Lakeside Diagnostics", "14 Lake Road, Pune  |  Ph: 020-2222-0000  (synthetic)", lines)

    june = report("15/06/2026", {
        "Glycosylated haemoglobin": "HbA1c 6.4 % (4.0 - 5.6)",
        "Lipid profile": "Total cholesterol 228 mg/dL (up to 200)",
        "LDL": "LDL cholesterol 148 mg/dL (up to 100)",
        "HDL": "HDL cholesterol 41 mg/dL (40 - 60)",
        "Triglycerides": "Triglycerides 190 mg/dL (up to 150)",
        "Blood sugar": "Fasting blood sugar 118 mg/dL (70 - 99)",
        "Kidney function": "Creatinine 0.9 mg/dL (0.6 - 1.2)",
    })
    september = report("14/09/2026", {
        "Glycosylated haemoglobin": "HbA1c 6.1 % (4.0 - 5.6)",
        "Lipid profile": "Total cholesterol 214 mg/dL (up to 200)",
        "LDL": "LDL cholesterol 136 mg/dL (up to 100)",
        "HDL": "HDL cholesterol 43 mg/dL (40 - 60)",
        "Triglycerides": "Triglycerides 172 mg/dL (up to 150)",
        "Blood sugar": "Fasting blood sugar 108 mg/dL (70 - 99)",
        "Kidney function": "Creatinine 0.9 mg/dL (0.6 - 1.2)",
    })
    return {"lab-report-june.png": june, "lab-report-september.png": september}


def meera_tests() -> dict[str, Image.Image]:
    """Test values (testing guide, section 6c): a prescription that orders three tests and has
    vitals written on it, and the lab report to add on a test afterwards. Planted: a number
    with a thousands comma (14,200), a value above its printed range (HbA1c 6.9, for the health
    numbers screen later), a unit with a power ten, and results in words (urine sugar, the
    impression)."""
    rx = prescription(
        "Greenleaf Clinic", "8 Park Street, Bengaluru  |  Ph: 080-0000-0000  (synthetic)",
        "Dr. N. Rao, MBBS, DNB (Family Medicine)   Reg. No. SYN-2002", "Dr. N. Rao",
        rows=[("Date: 16/10/2026", ""), ("Patient: Meera Iyer", "Age/Sex: 41 / F"),
              ("Complaints: Tiredness and thirst for 2 weeks", ""),
              ("BP: 138/88 mmHg", "Weight: 62 kg"), ("Pulse: 78 / min", ""),
              ("Diagnosis: ? Diabetes, to be confirmed", "")],
        meds=["1. Tab Dolo 650                SOS if fever > 100 F"],
        advice=["Tests advised: CBC, HbA1c, TSH", "Review with the reports."],
    )
    report = text_page(
        "Greenleaf Diagnostics", "8 Park Street, Bengaluru  |  Ph: 080-2222-0000  (synthetic)",
        ["LABORATORY REPORT", "",
         "Patient: Meera Iyer     Age/Sex: 41 / F     Lab No: SYN-LAB-7741",
         "Collected on 17/10/2026     Reported on 17/10/2026", "",
         "Complete blood count (CBC):",
         "  Hemoglobin 13.2 g/dL (13.0 - 17.0)",
         "  Total leucocyte count 14,200 /cu mm (4,000 - 11,000)",
         "  Platelet count 250 x10^3/uL (150 - 450)", "",
         "Glycosylated haemoglobin:",
         "  HbA1c 6.9 % (4.0 - 5.6)", "",
         "Thyroid profile:", "  TSH 2.8 uIU/mL (0.4 - 4.0)", "",
         "Urine routine:", "  Urine sugar: Nil", "",
         "Impression:", "  Mild leucocytosis. HbA1c above the reference range.", "",
         "Dr. P. Menon, MD (Pathology)   Reg. No. SYN-4004  (synthetic)"],
    )
    return {"visit4-prescription-tests.png": rx, "visit4-lab-report.png": report}


def save(folder: str, images: dict[str, Image.Image], pdf: str, pdf_pages: list[str]) -> None:
    out = SAMPLES / folder
    out.mkdir(parents=True, exist_ok=True)
    for name, img in images.items():
        img.save(out / name)
    first, *rest = [images[n] for n in pdf_pages]
    first.save(out / pdf, save_all=True, append_images=rest, resolution=150)
    for p in sorted(out.iterdir()):
        print(f"{p}  ({p.stat().st_size // 1024} KB)")


def main() -> None:
    save("demo-asha", {**asha(), **asha_labs()}, "documents.pdf", ["prescription.png", "bill.png"])
    save("test-meera", meera(), "visit2.pdf", ["visit2-prescription.png", "visit2-bill.png"])


if __name__ == "__main__":
    main()
