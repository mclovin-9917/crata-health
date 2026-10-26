from datetime import date

from app.schemas.record import (
    EVENT_KINDS,
    DatePrecision,
    Entry,
    EntryKind,
    EntryStatus,
    Medication,
    MedicationState,
    Provenance,
    SourceType,
    emergency_entries,
)

TODAY = date(2026, 10, 12)


def _med(**kw) -> Entry:
    e = Entry(kind=EntryKind.MEDICATION, label="Azithromycin 500 mg", medication=Medication(**kw))
    e.set_status(EntryStatus.CONFIRMED, by="p1")
    return e


def test_status_history_is_kept():
    e = Entry(kind=EntryKind.ALLERGY, label="Penicillin")
    e.set_status(EntryStatus.CONFIRMED, by="p1")
    e.set_status(EntryStatus.REJECTED, by="p1", note="changed my mind")
    assert [s.status for s in e.status_history] == [EntryStatus.CONFIRMED, EntryStatus.REJECTED]
    assert e.status == EntryStatus.REJECTED


def test_emergency_view_uses_confirmed_only():
    a = Entry(kind=EntryKind.ALLERGY, label="Penicillin")
    b = Entry(kind=EntryKind.MEDICATION, label="Amoxicillin")
    c = Entry(kind=EntryKind.SURGERY, label="Appendectomy")
    a.set_status(EntryStatus.CONFIRMED, by="p1")
    c.set_status(EntryStatus.REJECTED, by="p1")
    assert emergency_entries([a, b, c]) == [a]


def test_event_entry_with_partial_date():
    e = Entry(kind=EntryKind.SURGERY, label="Knee surgery", occurred_on="2019-03",
              date_precision=DatePrecision.MONTH, unverified=True)
    assert e.is_event and e.kind in EVENT_KINDS
    assert Entry(kind=EntryKind.NOTE, label="x").is_event is False


def test_course_end_date_counts_start_day():
    m = Medication(started_on=date(2026, 10, 10), duration_days=3)
    assert m.end_date() == date(2026, 10, 12)
    assert m.state(TODAY) == MedicationState.CURRENT
    assert m.state(date(2026, 10, 13)) == MedicationState.ENDED


def test_medication_states():
    assert Medication(ongoing=True).state(TODAY) == MedicationState.CURRENT
    assert Medication(started_on=TODAY).state(TODAY) == MedicationState.END_UNKNOWN
    assert Medication(duration_days=5).state(TODAY) == MedicationState.END_UNKNOWN
    stopped = Medication(ongoing=True, stopped_on=date(2026, 10, 1))
    assert stopped.state(TODAY) == MedicationState.STOPPED


def test_emergency_card_drops_finished_and_stopped_medicines_only():
    old_course = _med(started_on=date(2023, 1, 1), duration_days=5)
    current = _med(started_on=TODAY, duration_days=5)
    no_end = _med(started_on=date(2023, 1, 1))  # shown, labelled "duration not known"
    long_term = _med(ongoing=True)
    stopped = _med(ongoing=True, stopped_on=date(2026, 9, 1))
    no_details = Entry(kind=EntryKind.MEDICATION, label="Paracetamol")
    no_details.set_status(EntryStatus.CONFIRMED, by="p1")
    shown = emergency_entries([old_course, current, no_end, long_term, stopped, no_details], TODAY)
    assert shown == [current, no_end, long_term, no_details]


def test_edit_keeps_original_quote_and_status():
    e = Entry(
        kind=EntryKind.MEDICATION,
        label="Amoxicillin 500 mg",
        medication=Medication(dose="500 mg", duration_days=5),
        provenance=[Provenance(source_id="s1", source_type=SourceType.IMAGE,
                               quote="Amoxicillin 500 mg")],
    )
    e.apply_edit("p1", label="Amoxicillin 250 mg", medication={"dose": "250 mg"})
    assert e.status == EntryStatus.PROPOSED  # still needs the patient's confirm
    assert e.edited_by_patient
    assert [(x.field, x.old, x.new) for x in e.edits] == [
        ("label", "Amoxicillin 500 mg", "Amoxicillin 250 mg"),
        ("medication.dose", "500 mg", "250 mg"),
    ]
    assert e.medication.duration_days == 5  # untouched fields survive
    assert e.provenance[0].quote == "Amoxicillin 500 mg"


def test_edit_with_no_real_change_records_nothing():
    e = Entry(kind=EntryKind.ALLERGY, label="Penicillin", fields={"reaction": "rash"})
    e.apply_edit("p1", label="Penicillin", fields={"reaction": "rash"})
    assert not e.edited_by_patient


def test_a_system_translation_is_not_the_patients_edit():
    e = Entry(kind=EntryKind.NOTE, label="Garam paani piyo", patient_id="p1")
    e.apply_edit("system", label="Drink warm water")
    assert e.edits and not e.edited_by_patient
    e.apply_edit("p1", label="Drink warm water often")
    assert e.edited_by_patient
