import pytest

from app.schemas.access import AccessLogEntry, AccessReason, Alert, Role, TrustedLink, verify_chain
from app.schemas.patient import Patient
from app.schemas.record import (
    Encounter,
    Entry,
    EntryKind,
    EntryStatus,
    Medication,
    SourceItem,
    SourceType,
    Tier,
)
from app.storage import Storage


@pytest.fixture
def db():
    s = Storage()  # in memory
    yield s
    s.close()


def _log_entry(patient_id: str) -> AccessLogEntry:
    return AccessLogEntry(
        patient_id=patient_id,
        actor_id="dr-a",
        actor_role=Role.DOCTOR,
        actor_label="Dr. A (synthetic)",
        reason=AccessReason.EMERGENCY,
        tiers=[Tier.EMERGENCY_CARD, Tier.CLINICAL_SUMMARY],
    )


def test_patient_round_trip_and_sandbox_filter(db):
    p1 = Patient(name="Asha Verma (synthetic)", blood_group="B+")
    p2 = Patient(name="Ravi Rao (synthetic)", sandbox_id="visitor-1")
    db.save_patient(p1)
    db.save_patient(p2)
    assert db.get_patient(p1.id) == p1
    assert db.list_patients() == [p1]
    assert db.list_patients("visitor-1") == [p2]
    assert db.get_patient("nope") is None


def test_encounter_entries_live_in_their_own_table(db):
    enc = Encounter(
        patient_id="p1",
        sources=[SourceItem(source_type=SourceType.AUDIO, text="Take azithromycin 500 mg.")],
        entries=[
            Entry(kind=EntryKind.MEDICATION, label="Azithromycin 500 mg",
                  medication=Medication(dose="500 mg", duration_days=3)),
            Entry(kind=EntryKind.ALLERGY, label="Penicillin"),
        ],
    )
    db.save_encounter(enc)

    loaded = db.get_encounter(enc.id)
    assert loaded == enc
    assert loaded.sources[0].text == "Take azithromycin 500 mg."
    assert all(e.patient_id == "p1" and e.encounter_id == enc.id for e in loaded.entries)
    assert db.list_encounters("p1")[0].entries == []  # list view stays light

    # Confirming one entry is a single-row update, visible through the encounter too.
    med = db.get_entry(enc.entries[0].id)
    med.set_status(EntryStatus.CONFIRMED, by="p1")
    db.save_entry(med)
    assert [e.label for e in db.list_entries("p1", EntryStatus.CONFIRMED)] == ["Azithromycin 500 mg"]
    assert db.get_encounter(enc.id).entries[0].status == EntryStatus.CONFIRMED


def test_entry_without_patient_is_refused(db):
    with pytest.raises(ValueError):
        db.save_entry(Entry(kind=EntryKind.NOTE, label="orphan"))


def test_access_log_chains_per_patient_and_verifies(db):
    for _ in range(3):
        db.append_access(_log_entry("p1"))
    db.append_access(_log_entry("p2"))

    log = db.access_log("p1")
    assert len(log) == 3 and verify_chain(log)
    assert log[0].prev_hash == "" and log[1].prev_hash == log[0].hash
    assert db.access_log("p2")[0].prev_hash == ""  # chains are per patient


def test_tampering_in_the_database_is_detected(db):
    for _ in range(2):
        db.append_access(_log_entry("p1"))
    first = db.access_log("p1")[0]
    first.place = "edited later"
    with db._tx() as conn:
        conn.execute("UPDATE access_log SET data = ? WHERE seq = 1", (first.model_dump_json(),))
    assert not verify_chain(db.access_log("p1"))


def test_alerts_and_trusted_links(db):
    a = Alert(recipient_id="p1", patient_id="p1", log_entry_id="x", message="Emergency access")
    db.save_alert(a)
    assert db.list_alerts("p1") == [a]
    db.add_trusted(TrustedLink(patient_id="p1", trusted_id="p2"))
    db.add_trusted(TrustedLink(patient_id="p1", trusted_id="p2"))  # no duplicate
    assert db.trusted_people("p1") == ["p2"]
    assert db.patients_trusting("p2") == ["p1"]


def test_file_database_survives_reopen(tmp_path):
    path = tmp_path / "sub" / "app.sqlite3"
    s = Storage(path)
    p = Patient(name="Asha Verma (synthetic)")
    s.save_patient(p)
    s.close()
    s2 = Storage(path)
    assert s2.get_patient(p.id) == p
    s2.close()
