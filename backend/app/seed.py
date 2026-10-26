"""TEMPORARY demo data so the screens have something to show. Synthetic people, made-up history.

The demo people are built again from scratch on every start (decided 2026-10-17), so a demo
visitor always finds them as described in docs/testing-guide.md and nothing a visitor did lasts.
People who registered with their own phone number are never touched.

Asha has a record: facts and blood pressure and weight readings she added by hand, an older
visit she confirmed, two lab reports (June and September), and the planted prescription and bill
visit waiting for review. The visits were read by the real pipeline once and saved in
`demo_visits.json` (`scripts/make_demo_visits.py` makes it again), so they appear instantly with
no model calls. Ravi is her trusted person. Meera, Kabir and Rohan start empty,
for the testing guide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.schemas.access import TrustedLink
from app.schemas.account import AccountRole
from app.schemas.patient import Patient
from app.schemas.record import (
    DatePrecision,
    Encounter,
    Entry,
    EntryKind,
    EntryStatus,
    Medication,
    Provenance,
    SourceType,
    Speaker,
    TestResult,
    Tier,
)
from app.storage import Storage

DEMO_PATIENT_ID = "demo-asha"
DEMO_TRUSTED_ID = "demo-ravi"
DEMO_VISITS = Path(__file__).with_name("demo_visits.json")


@dataclass(frozen=True)
class DemoPerson:
    """One account in the demo list. Patients have a record (`person_id`); doctors do not.
    `phone` is the testing guide's number, given to the account only while no one else has
    registered it, so "Sign in with your phone" reaches the same person."""

    key: str
    name: str
    role: AccountRole
    about: str
    person_id: str | None = None
    birth_year: int | None = None
    sex: str | None = None
    blood_group: str | None = None
    phone: str | None = None
    registry_id: str | None = None


# In the order the Sign in list shows them.
DEMO_PEOPLE = [
    DemoPerson("asha", "Asha Verma", AccountRole.PATIENT, "Patient with a record of past visits",
               DEMO_PATIENT_ID, 1991, "F", "B+"),
    DemoPerson("ravi", "Ravi Verma", AccountRole.PATIENT, "Asha's trusted person",
               DEMO_TRUSTED_ID, 1988, "M"),
    DemoPerson("dr-mehta", "Dr. A. Mehta", AccountRole.DOCTOR,
               "Doctor (registry number not verified)", registry_id="SYN-0001"),
    DemoPerson("meera", "Meera Iyer", AccountRole.PATIENT,
               "Patient with an empty record, for the testing guide", "demo-meera", 1985, "F",
               "O+", phone="9000010001"),
    DemoPerson("kabir", "Kabir Iyer", AccountRole.PATIENT,
               "Patient Meera can invite as her trusted person", "demo-kabir", 1982, "M",
               phone="9000010002"),
    DemoPerson("rohan", "Rohan Das", AccountRole.PATIENT, "Patient, for checking privacy",
               "demo-rohan", 1990, "M", "A+", phone="9000010004"),
    DemoPerson("dr-rao", "Dr. Neha Rao", AccountRole.DOCTOR,
               "Doctor (registry number not verified)", phone="9000010003",
               registry_id="SYN-2002"),
]


def seed_demo(db: Storage, visits: bool = True) -> None:
    """Build the demo people again from scratch: their records, links and logs. `visits=False`
    leaves out Asha's saved visits (`make_demo_visits.py` reads them again)."""
    patients = [p for p in DEMO_PEOPLE if p.person_id]
    db.clear_people([p.person_id for p in patients if p.person_id])
    for p in patients:
        db.save_patient(Patient(id=p.person_id, name=p.name, birth_year=p.birth_year, sex=p.sex,
                                blood_group=p.blood_group))
    db.add_trusted(TrustedLink(patient_id=DEMO_PATIENT_ID, trusted_id=DEMO_TRUSTED_ID))

    by_hand = Provenance(source_id="manual", source_type=SourceType.MANUAL)
    history = [
        Entry(kind=EntryKind.ALLERGY, label="Sulfa drugs", fields={"reaction": "hives"},
              tier=Tier.EMERGENCY_CARD),
        Entry(kind=EntryKind.DIAGNOSIS, label="Hypertension", tier=Tier.EMERGENCY_CARD),
        Entry(kind=EntryKind.MEDICATION, label="Amlodipine 5 mg", tier=Tier.EMERGENCY_CARD,
              fields={"frequency": "once a day"},
              medication=Medication(dose="5 mg", doses_per_day=1, ongoing=True,
                                    started_on=date.today() - timedelta(days=400))),
        Entry(kind=EntryKind.SURGERY, label="Appendectomy", occurred_on="2015",
              date_precision=DatePrecision.YEAR, unverified=True, tier=Tier.CLINICAL_SUMMARY),
        Entry(kind=EntryKind.EMERGENCY_VISIT, label="Dengue fever, ER visit",
              occurred_on="2024-08", date_precision=DatePrecision.MONTH, unverified=True,
              tier=Tier.CLINICAL_SUMMARY),
    ]
    history += _readings()
    for e in history:
        e.patient_id = DEMO_PATIENT_ID
        e.provenance = [by_hand]
        e.set_status(EntryStatus.CONFIRMED, by=DEMO_PATIENT_ID, note="demo seed")
        db.save_entry(e)

    for visit in demo_visits() if visits else []:
        db.save_encounter(visit)


# Readings Asha took herself (My health numbers): (day, systolic, diastolic, weight in kg).
_HOME_READINGS = [
    (date(2026, 7, 14), 146, 92, 74.0),
    (date(2026, 8, 18), 140, 90, None),
    (date(2026, 9, 20), 136, 86, 73.5),
    (date(2026, 10, 5), 134, 84, 72.8),
]


def _readings() -> list[Entry]:
    """Asha's own blood pressure and weight readings, entered by hand. Fixed dates, like her visits;
    the lab reports she also has are read from `samples/demo-asha/` (`make_demo_visits.py`)."""
    def reading(label: str, value: float, unit: str, on: date) -> Entry:
        return Entry(kind=EntryKind.RESULT, label=label, tier=Tier.FULL_RECORD, said_by=Speaker.PATIENT,
                     result=TestResult(value=value, unit=unit, measured_on=on))

    out = []
    for on, systolic, diastolic, weight in _HOME_READINGS:
        out += [reading("Blood pressure (systolic)", systolic, "mmHg", on),
                reading("Blood pressure (diastolic)", diastolic, "mmHg", on)]
        if weight is not None:
            out.append(reading("Weight", weight, "kg", on))
    return out


def demo_visits() -> list[Encounter]:
    """Asha's saved visits, oldest first. None if the file has not been made."""
    if not DEMO_VISITS.exists():
        return []
    return [Encounter.model_validate(v) for v in json.loads(DEMO_VISITS.read_text("utf-8"))]
