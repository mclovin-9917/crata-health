import re

import pytest

from app.ai.client import AIError
from app.media.audio import to_wav
from app.pipeline import prompts
from app.pipeline.bills import DocumentSort, SortedDocument
from app.pipeline.crosscheck import FamilyAnswer, FamilyAnswers
from app.pipeline.extract import DrugNames, ExtractedItem, Extraction
from app.pipeline.medicines import GenericMatch, GenericMatches, Translation
from app.pipeline.run import Media, consult_date, process_encounter
from app.schemas.record import (
    Encounter,
    EncounterStatus,
    EntryKind,
    EntryStatus,
    SourceItem,
    SourceType,
    Tier,
)
from app.storage import Storage
from tests.test_audio import tone

DOC = "Rx: Azithromycin 500 mg, once a day, 3 days\nAllergy noted: Penicillin"
CONSULT = (
    "Any allergies? Yes, I am allergic to penicillin. I got a rash last time. "
    "Okay, then no amoxicillin. I will give you azithromycin 500 milligrams, once a day for "
    "three days. You had your appendix out in 2015, right?"
)


WAV_3S = to_wav(tone(3.0, 16_000))


class FakeAI:
    """Stands in for AIClient: scripted perceive text and extraction, records every call."""

    def __init__(self, items, fail_on=None, docs=None, extra=None):
        self.items, self.fail_on, self.calls = items, fail_on, []
        self.extra = extra or {}  # more extraction fields: visit_title, facility, ...
        self.docs = docs  # scripted document sort; default: every document is a prescription
        self.thinking: list[tuple[str, bool | None]] = []

    def perceive(self, parts, instruction, **kw):
        kind = parts[0]["type"]
        self.calls.append(("perceive", kind, instruction))
        self.thinking.append((kind, kw.get("thinking")))
        if self.fail_on == kind:
            raise AIError("endpoint busy")
        return DOC if kind == "image_url" else CONSULT

    def reason_json(self, system, user, schema, **kw):
        self.calls.append(("reason_json", schema.__name__, user))
        if schema is DrugNames:
            return DrugNames(names=["Azithromycin"])
        if schema is DocumentSort:
            if self.docs is not None:
                return DocumentSort(documents=self.docs)
            return DocumentSort(documents=[
                SortedDocument(source_id=sid, kind="prescription")
                for sid in re.findall(r"source_id: (\S+) ===", user)])
        if schema is GenericMatches:
            return GenericMatches(items=[GenericMatch(name=n, status="unknown", generics=[])
                                         for n in user.splitlines()])
        if schema is Translation:
            return Translation(english=user.splitlines())
        if schema is FamilyAnswers:
            return FamilyAnswers(items=[FamilyAnswer(name=n, kind="unknown")
                                        for n in user.splitlines()])
        return Extraction(items=self.items, **self.extra)


def _item(**kw):
    base = {"confidence": 0.9, "said_by": "doctor"}
    return ExtractedItem(**{**base, **kw})


ITEMS = [
    _item(kind=EntryKind.MEDICATION, label="Azithromycin 500 mg", source_id="audio1",
          quote="azithromycin 500 milligrams, once a day for three days", dose="500 mg",
          doses_per_day=1, duration_value=3, duration_unit="day",
          fields={"frequency": "once a day"}),
    _item(kind=EntryKind.ALLERGY, label="Penicillin", source_id="doc1",
          quote="Allergy noted: Penicillin", fields={"reaction": "rash"}),
    _item(kind=EntryKind.SURGERY, label="Appendectomy", source_id="audio1",
          quote="appendix out in 2015", occurred_on="2015"),
    # Hallucinated: not in the consult.
    _item(kind=EntryKind.MEDICATION, label="Amoxicillin 500 mg", source_id="audio1",
          quote="take amoxicillin 500 mg twice a day"),
    # Real words, but the source id doesn't exist.
    _item(kind=EntryKind.NOTE, label="Ghost", source_id="doc9", quote="Penicillin"),
]


@pytest.fixture
def setup():
    db = Storage()
    enc = Encounter(patient_id="p1", sources=[
        SourceItem(source_type=SourceType.IMAGE, filename="rx.jpg"),
        SourceItem(source_type=SourceType.AUDIO, filename="consult.wav"),
    ])
    db.save_encounter(enc)
    media = [
        Media(source_id=enc.sources[0].id, kind="image", data=b"img", mime="image/jpeg"),
        Media(source_id=enc.sources[1].id, kind="audio", data=WAV_3S, mime="audio/wav"),
    ]
    yield db, enc, media
    db.close()


def test_full_run_keeps_quoted_entries_and_drops_the_rest(setup):
    db, enc, media = setup
    process_encounter(db, FakeAI(ITEMS), enc.id, media)

    done = db.get_encounter(enc.id)
    assert done.status == EncounterStatus.READY and done.step is None
    assert [e.label for e in done.entries] == ["Azithromycin 500 mg", "Penicillin", "Appendectomy"]
    assert done.quote_failures == ["Amoxicillin 500 mg", "Ghost"]
    assert all(e.status == EntryStatus.PROPOSED for e in done.entries)  # the AI only proposes
    assert done.sources[0].text == DOC and done.sources[1].text == CONSULT
    assert set(done.step_seconds) == {
        "preparing_audio", "reading_documents", "sorting_documents", "finding_drug_names",
        "transcribing", "extracting", "checking_medicines", "cross_checking",
    }
    assert media == []  # media dropped


def test_entries_get_tier_dates_and_provenance_from_code(setup):
    db, enc, media = setup
    process_encounter(db, FakeAI(ITEMS), enc.id, media)
    med, allergy, surgery = db.get_encounter(enc.id).entries

    assert med.tier == Tier.EMERGENCY_CARD
    assert med.medication.duration_days == 3 and med.medication.doses_per_day == 1
    assert med.medication.started_on == consult_date(enc)
    assert med.provenance[0].source_id == enc.sources[1].id
    assert med.provenance[0].quote.startswith("azithromycin")
    assert allergy.tier == Tier.EMERGENCY_CARD and allergy.fields["reaction"] == "rash"
    assert surgery.tier == Tier.CLINICAL_SUMMARY
    assert surgery.occurred_on == "2015" and surgery.date_precision.value == "year"
    assert surgery.unverified  # said in the consult, not shown on a document


def test_transcription_gets_drug_names_only_as_hints(setup):
    db, enc, media = setup
    ai = FakeAI(ITEMS)
    process_encounter(db, ai, enc.id, media)
    instruction = next(c[2] for c in ai.calls if c[:2] == ("perceive", "audio_url"))
    assert "Azithromycin" in instruction
    assert "500" not in instruction and "3 days" not in instruction


def test_a_failing_step_marks_the_encounter_failed(setup):
    db, enc, media = setup
    process_encounter(db, FakeAI(ITEMS, fail_on="audio_url"), enc.id, media)
    failed = db.get_encounter(enc.id)
    assert failed.status == EncounterStatus.FAILED
    assert failed.error.startswith("transcribing:") and "endpoint busy" in failed.error
    assert media == []  # dropped even on failure


def test_typed_notes_work_without_audio():
    db = Storage()
    enc = Encounter(patient_id="p1")
    db.save_encounter(enc)
    items = [_item(kind=EntryKind.ALLERGY, label="Sulfa drugs", source_id="notes1",
                   quote="allergic to sulfa drugs")]
    process_encounter(db, FakeAI(items), enc.id, [], notes="Patient is allergic to sulfa drugs.")
    done = db.get_encounter(enc.id)
    assert [e.label for e in done.entries] == ["Sulfa drugs"]
    assert done.sources[0].source_type == SourceType.TEXT


def test_nothing_readable_fails_cleanly():
    db = Storage()
    enc = Encounter(patient_id="p1")
    db.save_encounter(enc)
    process_encounter(db, FakeAI([]), enc.id, [], notes="   ")
    assert db.get_encounter(enc.id).status == EncounterStatus.FAILED


def test_consult_date_is_taken_in_india_time():
    from datetime import UTC, datetime

    late_evening_utc = Encounter(patient_id="p1", created_at=datetime(2026, 10, 11, 20, 0, tzinfo=UTC))
    assert str(consult_date(late_evening_utc)) == "2026-10-12"  # 01:30 IST next day


def test_empty_fields_are_dropped():
    from datetime import date

    from app.pipeline.extract import to_entries

    src = SourceItem(source_type=SourceType.TEXT, text="Paracetamol 650 if needed")
    item = _item(kind=EntryKind.MEDICATION, label="Paracetamol 650 mg", source_id="notes1",
                 quote="Paracetamol 650 if needed", as_needed=True,
                 fields={"instructions": "", "note": "  ", "dose": "650 mg",
                         "doses_per_day": "2", "duration_unit": "day"})
    (entry,), _ = to_entries(Extraction(items=[item]), {"notes1": src}, date(2026, 10, 12))
    assert entry.fields == {"frequency": "as needed"}


def test_long_audio_is_transcribed_in_timed_pieces_and_quotes_get_timestamps():
    import numpy as np

    pieces = iter(["Any allergies? Yes, penicillin. I got a rash.",
                   "I will give you azithromycin 500 milligrams once a day for three days."])

    class ChunkAI(FakeAI):
        def perceive(self, parts, instruction, **kw):
            self.calls.append(("perceive", parts[0]["type"], instruction))
            return next(pieces)

    items = [
        _item(kind=EntryKind.ALLERGY, label="Penicillin", source_id="audio1",
              quote="Yes, penicillin"),
        _item(kind=EntryKind.MEDICATION, label="Azithromycin 500 mg", source_id="audio1",
              quote="azithromycin 500 milligrams once a day"),
        # Straddles the cut between the two pieces.
        _item(kind=EntryKind.NOTE, label="Rash, then azithromycin", source_id="audio1",
              quote="I got a rash. I will give you azithromycin"),
    ]
    db = Storage()
    enc = Encounter(patient_id="p1", sources=[SourceItem(source_type=SourceType.AUDIO)])
    db.save_encounter(enc)
    # 30 s tone, a 1 s pause, 30 s tone: cut in the pause.
    samples = np.concatenate([tone(30, 16_000), np.zeros(16_000, np.int16), tone(30, 16_000)])
    media = [Media(enc.sources[0].id, "audio", to_wav(samples), "audio/wav")]
    process_encounter(db, ChunkAI(items), enc.id, media)

    done = db.get_encounter(enc.id)
    segs = done.sources[0].segments
    assert len(segs) == 2
    assert 30.0 <= segs[0].end_s <= 31.0 and segs[0].end_s == segs[1].start_s  # cut in the pause
    allergy, med, note = done.entries
    assert allergy.provenance[0].locator == "00:00-00:30"
    assert med.provenance[0].locator == "00:30-01:01"
    assert note.provenance[0].locator == "00:00-01:01"  # straddles the cut


def test_unreadable_audio_fails_before_any_model_call(setup):
    db, enc, media = setup
    media[1].data = b"not audio at all"
    ai = FakeAI(ITEMS)
    process_encounter(db, ai, enc.id, media)
    failed = db.get_encounter(enc.id)
    assert failed.status == EncounterStatus.FAILED
    assert failed.error.startswith("preparing_audio:")
    assert ai.calls == []


def test_hint_prompt_without_documents_is_plain():
    assert prompts.transcribe_with_hints([]) == prompts.TRANSCRIBE


# ---- careful re-read ---------------------------------------------------------------------
def _reviewed_visit(setup):
    """A read visit: Azithromycin confirmed, Appendectomy rejected, Penicillin still proposed."""
    db, enc, media = setup
    originals = [Media(m.source_id, m.kind, m.data, m.mime) for m in media]
    process_encounter(db, FakeAI(ITEMS), enc.id, media)
    azithro, _, appendix = db.get_encounter(enc.id).entries
    azithro.set_status(EntryStatus.CONFIRMED, by="p1")
    appendix.set_status(EntryStatus.REJECTED, by="p1")
    db.save_entry(azithro)
    db.save_entry(appendix)
    return db, enc, originals


CAREFUL_ITEMS = [
    ITEMS[0],  # Azithromycin again: already confirmed, so dropped
    ITEMS[1],  # Penicillin again: replaces the unreviewed one
    ITEMS[2],  # Appendectomy again: the patient rejected it, so dropped
    _item(kind=EntryKind.NOTE, label="Three-day course", source_id="doc1",
          quote="once a day, 3 days"),
]


def test_careful_reread_reads_documents_with_thinking_on(setup):
    db, enc, media = _reviewed_visit(setup)
    ai = FakeAI(CAREFUL_ITEMS)
    process_encounter(db, ai, enc.id, media, careful=True)
    assert ("image_url", True) in ai.thinking
    assert all(t is None for k, t in ai.thinking if k == "audio_url")  # audio stays as it is


def test_careful_reread_keeps_reviewed_entries_and_replaces_the_rest(setup):
    db, enc, media = _reviewed_visit(setup)
    process_encounter(db, FakeAI(CAREFUL_ITEMS), enc.id, media, careful=True)
    done = db.get_encounter(enc.id)

    assert done.status == EncounterStatus.READY and done.error is None
    assert [(e.label, e.status) for e in done.entries] == [
        ("Azithromycin 500 mg", EntryStatus.CONFIRMED),
        ("Penicillin", EntryStatus.REJECTED),  # replaced, history kept
        ("Appendectomy", EntryStatus.REJECTED),
        ("Penicillin", EntryStatus.PROPOSED),  # an allergy still waits for the patient
        ("Three-day course", EntryStatus.CONFIRMED),  # a note is read as it is
    ]
    replaced = done.entries[1].status_history[-1]
    assert replaced.by == "system" and replaced.note == "Replaced by a careful re-read"

    # New sources for the same files; confirmed entries keep their own source and quote.
    assert len(done.sources) == 4
    old_ids = {s.id for s in done.sources[:2]}
    new = done.sources[2:]
    assert all(s.careful for s in new) and {s.reread_of for s in new} == old_ids
    assert done.entries[0].provenance[0].source_id in old_ids
    assert done.entries[3].provenance[0].source_id == new[0].id


def test_a_failed_careful_reread_leaves_the_visit_as_it_was(setup):
    db, enc, media = _reviewed_visit(setup)
    before = db.get_encounter(enc.id)
    process_encounter(db, FakeAI(CAREFUL_ITEMS, fail_on="image_url"), enc.id, media, careful=True)
    after = db.get_encounter(enc.id)

    assert after.status == EncounterStatus.READY
    assert after.error.startswith("Careful re-read failed at reading_documents")
    assert len(after.sources) == 2
    assert [(e.label, e.status) for e in after.entries] == [
        (e.label, e.status) for e in before.entries
    ]
    assert media == []


# ---- cross-checks ------------------------------------------------------------------------
def _sulfa_in_record(db, patient_id="p1"):
    from app.schemas.record import Entry, Provenance

    sulfa = Entry(patient_id=patient_id, kind=EntryKind.ALLERGY, label="Sulfa drugs",
                  fields={"reaction": "hives"},
                  provenance=[Provenance(source_id="manual", source_type=SourceType.MANUAL)])
    sulfa.set_status(EntryStatus.CONFIRMED, by=patient_id)
    db.save_entry(sulfa)
    return sulfa


SEPTRAN_ITEM = _item(kind=EntryKind.MEDICATION, label="Septran DS", source_id="notes1",
                     quote="Septran DS twice a day for 5 days")


def test_a_visit_is_checked_against_allergies_in_the_record():
    db = Storage()
    sulfa = _sulfa_in_record(db)
    enc = Encounter(patient_id="p1")
    db.save_encounter(enc)
    process_encounter(db, FakeAI([SEPTRAN_ITEM]), enc.id, [],
                      notes="Septran DS twice a day for 5 days.")
    done = db.get_encounter(enc.id)
    (clash,) = done.discrepancies
    assert clash.check == "allergy_clash" and clash.severity == "urgent"
    assert clash.entry_ids == [done.entries[0].id, sulfa.id]
    assert "cross_checking" in done.step_seconds


def test_another_patients_allergy_is_not_used():
    db = Storage()
    _sulfa_in_record(db, patient_id="p2")
    enc = Encounter(patient_id="p1")
    db.save_encounter(enc)
    process_encounter(db, FakeAI([SEPTRAN_ITEM]), enc.id, [],
                      notes="Septran DS twice a day for 5 days.")
    assert db.get_encounter(enc.id).discrepancies == []


def test_a_careful_reread_checks_the_visit_again(setup):
    db, enc, media = _reviewed_visit(setup)
    _sulfa_in_record(db)
    septran = _item(kind=EntryKind.MEDICATION, label="Septran DS", source_id="doc1",
                    quote="Azithromycin 500 mg")  # any quote that is in DOC
    process_encounter(db, FakeAI([*CAREFUL_ITEMS, septran]), enc.id, media, careful=True)
    done = db.get_encounter(enc.id)
    assert [x.message.split(" contains")[0] for x in done.discrepancies] == ["Septran DS"]


def test_the_visit_records_which_model_answered_each_step(setup):
    db, enc, media = setup

    class BackupAI(FakeAI):
        """Documents answered by a backup, everything else by the primary."""

        def __init__(self, items):
            super().__init__(items)
            self.used = []

        def perceive(self, parts, instruction, **kw):
            kind = parts[0]["type"]
            self.used.append("nebius:google/gemma-3-27b-it (backup)" if kind == "image_url"
                             else "nvidia:omni")
            return super().perceive(parts, instruction, **kw)

    process_encounter(db, BackupAI(ITEMS), enc.id, media)
    done = db.get_encounter(enc.id)
    assert done.models_used["reading_documents"] == ["nebius:google/gemma-3-27b-it (backup)"]
    assert done.models_used["transcribing"] == ["nvidia:omni"]
