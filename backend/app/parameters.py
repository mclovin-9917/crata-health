"""The measures a patient's numbers are grouped by (My health numbers).

A lab calls one measure many things ("Hb", "Hemoglobin", "Glycosylated haemoglobin (HbA1c)"), so a
curated table maps the names to one measure; plain code, no model. A name the table does not know
becomes its own measure, so no number is ever left out. Nothing here converts units or says what
a number means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class Measure:
    key: str
    name: str  # as shown, and what a reading added by hand is stored under
    unit: str | None  # the usual unit: what Add a reading fills in
    aliases: tuple[str, ...] = ()
    group: str = ""  # measures that share a card (blood pressure's two numbers)
    group_name: str = ""

    @property
    def card(self) -> str:
        return self.group or self.key

    @property
    def card_name(self) -> str:
        return self.group_name or self.name


def _m(key: str, name: str, unit: str | None, *aliases: str, group: str = "", group_name: str = "") -> Measure:
    return Measure(key, name, unit, aliases, group, group_name)


MEASURES: tuple[Measure, ...] = (
    _m("bp_systolic", "Blood pressure (systolic)", "mmHg", "bp systolic", "systolic bp",
       "blood pressure systolic", "systolic blood pressure", "systolic",
       group="blood_pressure", group_name="Blood pressure"),
    _m("bp_diastolic", "Blood pressure (diastolic)", "mmHg", "bp diastolic", "diastolic bp",
       "blood pressure diastolic", "diastolic blood pressure", "diastolic",
       group="blood_pressure", group_name="Blood pressure"),
    _m("glucose_fasting", "Blood sugar (fasting)", "mg/dL", "fasting blood sugar", "fasting glucose",
       "fasting plasma glucose", "fasting blood glucose", "blood sugar fasting", "glucose fasting",
       "fbs", "fbg", "fpg"),
    _m("glucose_after_meals", "Blood sugar (after meals)", "mg/dL", "postprandial blood sugar",
       "post prandial blood sugar", "postprandial glucose", "post prandial glucose", "ppbs",
       "pp blood sugar", "blood sugar after meals", "blood sugar post meal", "2 hour glucose",
       "glucose pp", "pp glucose", "blood sugar pp", "glucose post lunch", "post lunch blood sugar",
       "blood sugar post lunch", "blood glucose pp"),
    _m("glucose_random", "Blood sugar (random)", "mg/dL", "random blood sugar", "random glucose",
       "blood sugar random", "rbs", "glucose random", "blood glucose random"),
    _m("glucose", "Blood sugar", "mg/dL", "blood sugar", "blood glucose", "glucose"),
    _m("hba1c", "HbA1c", "%", "hba1c", "a1c", "hemoglobin a1c", "haemoglobin a1c",
       "glycated hemoglobin", "glycated haemoglobin", "glycosylated hemoglobin",
       "glycosylated haemoglobin"),
    _m("hemoglobin", "Hemoglobin", "g/dL", "hemoglobin", "haemoglobin", "hb", "hgb"),
    _m("cholesterol", "Total cholesterol", "mg/dL", "total cholesterol", "cholesterol total",
       "serum cholesterol", "cholesterol"),
    _m("ldl", "LDL cholesterol", "mg/dL", "ldl", "ldl cholesterol", "ldl c",
       "low density lipoprotein"),
    _m("hdl", "HDL cholesterol", "mg/dL", "hdl", "hdl cholesterol", "hdl c",
       "high density lipoprotein"),
    _m("triglycerides", "Triglycerides", "mg/dL", "triglycerides", "triglyceride", "tg"),
    _m("creatinine", "Creatinine", "mg/dL", "creatinine", "serum creatinine"),
    _m("urea", "Urea", "mg/dL", "urea", "blood urea"),
    # BUN is not urea (urea is about 2.14 times BUN), so the two never share a line.
    _m("bun", "Blood urea nitrogen (BUN)", "mg/dL", "blood urea nitrogen", "bun"),
    _m("uric_acid", "Uric acid", "mg/dL", "uric acid", "serum uric acid"),
    _m("tsh", "TSH", "uIU/mL", "tsh", "thyroid stimulating hormone"),
    _m("t3", "T3", "ng/dL", "t3", "total t3", "triiodothyronine"),
    _m("t4", "T4", "ug/dL", "t4", "total t4", "thyroxine"),
    _m("platelets", "Platelet count", None, "platelet count", "platelets", "plt"),
    _m("wbc", "White blood cell count", None, "white blood cell count", "wbc", "wbc count",
       "total wbc count", "total leucocyte count", "total leukocyte count", "tlc"),
    _m("rbc", "Red blood cell count", None, "red blood cell count", "rbc", "rbc count"),
    _m("alt", "ALT (SGPT)", "U/L", "alt", "sgpt", "alanine aminotransferase", "alanine transaminase"),
    _m("ast", "AST (SGOT)", "U/L", "ast", "sgot", "aspartate aminotransferase"),
    _m("vitamin_d", "Vitamin D", "ng/mL", "vitamin d", "vit d", "25 oh vitamin d",
       "25 hydroxy vitamin d"),
    _m("vitamin_b12", "Vitamin B12", "pg/mL", "vitamin b12", "vit b12", "b12", "cobalamin"),
    _m("sodium", "Sodium", "mmol/L", "sodium", "serum sodium"),
    _m("potassium", "Potassium", "mmol/L", "potassium", "serum potassium"),
    _m("weight", "Weight", "kg", "weight", "body weight", "wt"),
    _m("height", "Height", "cm", "height"),
    _m("bmi", "BMI", "kg/m2", "bmi", "body mass index"),
    _m("pulse", "Pulse", "/min", "pulse", "pulse rate", "heart rate"),
    _m("temperature", "Temperature", "°C", "temperature", "body temperature", "temp"),
    _m("spo2", "Oxygen level (SpO2)", "%", "spo2", "sp o2", "oxygen saturation", "oxygen level",
       "o2 saturation", "pulse oximetry", "spo2 pulse oximetry"),
)


def _norm(text: str) -> str:
    """Case, punctuation and spacing ignored: 'Hb (g/dL)' and 'hb' read alike."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.casefold()).split())


# Longest alias first, so "glycosylated haemoglobin" is HbA1c, not hemoglobin, and "total
# cholesterol" is not just "cholesterol".
_ALIASES = sorted(
    ((_norm(a), m) for m in MEASURES for a in (m.name, *m.aliases)),
    key=lambda pair: (-len(pair[0].split()), -len(pair[0])),
)


# A name with one of these words is a different quantity from the measure it mentions ("Non-HDL
# cholesterol", "Total cholesterol/HDL ratio", "Urine creatinine", "MCHC (mean corpuscular
# hemoglobin concentration)", "Free T4", "Estimated average glucose"): it is never filed under that
# measure, so it cannot put a different number on another chart. It becomes its own measure.
_DIFFERENT = frozenset({
    "ratio", "urine", "urinary", "non", "vldl", "mean", "corpuscular", "mchc", "mch", "mcv",
    "estimated", "free", "reverse", "clearance", "microalbumin", "pus", "epithelial", "index",
})


def measure_of(label: str) -> Measure:
    """The measure a printed or typed name belongs to. An unknown name, or one that names a
    different quantity from the measure it mentions, is its own measure."""
    n = _norm(label)
    padded = f" {n} "
    words = set(n.split())
    for alias, measure in _ALIASES:
        if f" {alias} " in padded and not (words & _DIFFERENT - set(alias.split())):
            return measure
    name = " ".join(label.split())
    return Measure(key=f"other:{n}", name=name, unit=None)


def is_known(label: str) -> bool:
    return not measure_of(label).key.startswith("other:")


def unit_key(unit: str | None) -> str:
    """Units compared as written, apart from case, spaces and a leading 'x' (x10^3/uL)."""
    if not unit:
        return ""
    # casefold() turns the micro sign (U+00B5) into a Greek mu (U+03BC): both mean "u".
    folded = unit.casefold().replace("μ", "u").replace("µ", "u")
    squeezed = re.sub(r"\s+", "", folded).lstrip("x")
    return _UNIT_SPELLINGS.get(squeezed, squeezed)


# The same unit written differently (prescriptions: "mm of Hg", "mm/Hg"; "bpm").
_UNIT_SPELLINGS = {
    "mmofhg": "mmhg", "mm/hg": "mmhg", "mmhg.": "mmhg", "bpm": "/min", "beats/min": "/min",
    "/minute": "/min", "perminute": "/min", "kgs": "kg", "kilograms": "kg", "degc": "°c",
    "celsius": "°c", "degf": "°f", "fahrenheit": "°f",
}


class Preset(BaseModel):
    """A common measure Add a reading offers. A blood pressure is two numbers (`parts`)."""

    name: str
    unit: str | None
    parts: list[str] = []


def _preset(key: str) -> Preset:
    m = next(m for m in MEASURES if m.key == key)
    return Preset(name=m.name, unit=m.unit)


PRESETS: tuple[Preset, ...] = (
    Preset(name="Blood pressure", unit="mmHg",
           parts=["Blood pressure (systolic)", "Blood pressure (diastolic)"]),
    _preset("glucose_fasting"),
    _preset("glucose_after_meals"),
    _preset("glucose_random"),
    _preset("weight"),
    _preset("pulse"),
    _preset("temperature"),
    _preset("spo2"),
)
