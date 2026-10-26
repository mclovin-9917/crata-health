"""What Review asks of the patient.

Essential kinds (allergies, medicines, diagnoses, events) wait for a confirm tap, in one box per
kind. Everything else goes in as read (`read_as_is`): marked, editable, removable. A row the code
has doubts about is "flagged": a bulk confirm never covers it, so a warning is never skipped.
"""

from __future__ import annotations

from app.schemas.record import ESSENTIAL_KINDS, Discrepancy, Entry, EntryStatus

READ_NOTE = "Read from the visit"
LOW_CONFIDENCE = 0.85  # below this the wording was unclear: "check this one"


def read_as_is(entries: list[Entry]) -> None:
    """Entries of a kind the patient need not confirm go into the record as they were read: confirmed
    by "system", marked `added_as_read`. Essential kinds stay proposed."""
    for e in entries:
        if e.kind in ESSENTIAL_KINDS or e.status != EntryStatus.PROPOSED:
            continue
        e.set_status(EntryStatus.CONFIRMED, by="system", note=READ_NOTE)
        e.added_as_read = True


def flagged(entries: list[Entry], findings: list[Discrepancy]) -> dict[str, str]:
    """Entry id -> why it needs a look of its own: `check` (a note from the checks), `clash` (named
    by an open allergy or bill finding: the medicine, and the allergy too, since turning the allergy
    down would make the warning disappear), `unsure` (the wording was unclear). Only entries still
    waiting for a decision, or read as they are and not yet corrected (an edit is the patient
    looking at it)."""
    in_finding = {i for d in findings for i in d.entry_ids}
    out: dict[str, str] = {}
    for e in entries:
        if e.status == EntryStatus.REJECTED:
            continue
        if e.status == EntryStatus.CONFIRMED and (not e.added_as_read or e.edits):
            continue  # the patient confirmed it, or corrected a value read as it is
        if e.checks:
            out[e.id] = "check"
        elif e.id in in_finding:
            out[e.id] = "clash"
        elif e.confidence is not None and e.confidence < LOW_CONFIDENCE:
            out[e.id] = "unsure"
    return out
