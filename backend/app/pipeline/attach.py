"""Smart attach: a new visit made only of reports may be the report for tests that an earlier visit
ordered and that are still waiting for one.

A lab report usually holds several tests of one visit, so the report is matched to a VISIT first,
then its tests are paired with that visit's waiting tests.

Code decides everything except the match itself:
- when to look at all (a new visit of documents only, every one a report or a scan, with values);
- the candidate visits and their waiting tests (confirmed, no confirmed value, the patient's own
  read visits in a window of days before the report; never an archived or a stay visit);
- what the model may answer (numbers it was offered, pairs inside the chosen visit, no repeats);
- what an attach moves, and where the values are linked.
The model sees test names, result names, visit dates and the visit names the AI itself gave. Nothing
else about the patient, and its words never reach the screen. The patient decides: this only ever
produces a suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field, field_validator

from app.ai.client import AIClient
from app.pipeline import prompts
from app.schemas.record import (
    AttachPair,
    AttachSuggestion,
    Discrepancy,
    DocumentKind,
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    SourceType,
)
from app.storage import Storage

WINDOW_DAYS = 120  # a visit counts if it is at most this many days before the report
MAX_CANDIDATES = 10  # visits offered, the nearest ones, so the prompt stays short
MAX_WAITING = 15  # waiting tests listed under one visit
MAX_NAMES = 20  # tests of the report sent to the model
MAX_RESULTS = 8  # result names sent for each test
REPORT_KINDS = {DocumentKind.REPORT, DocumentKind.IMAGING}


class AttachPairChoice(BaseModel):
    new_test: int = Field(description="Number of a test of the new report")
    waiting: str = Field(description="Number of a waiting test of the chosen visit, like '2.1'")

    @field_validator("waiting", mode="before")
    @classmethod
    def _as_text(cls, v: object) -> str:
        return str(v)


class AttachChoice(BaseModel):
    """What the model returns: one offered visit and the pairs inside it, or none."""

    visit: int | None = Field(default=None, description="Number of the chosen visit")
    pairs: list[AttachPairChoice] = Field(default_factory=list)


@dataclass
class Candidate:
    number: int  # as the model is shown it, from 1
    visit: Encounter
    tests: list[Entry]  # its waiting tests, numbered from 1 in this order


class AttachTarget(BaseModel):
    """A suggestion as the new visit's page shows it: the visit and the tests it points at."""

    encounter_id: str
    test_entry_ids: list[str]
    test_labels: list[str]
    visit_date: date | None
    visit_title: str | None


def reports_only(enc: Encounter) -> bool:
    """The visit is documents only (no recording, no typed notes), and every document, or every
    page of one, is a report or a scan: no prescription, no bill, nothing unsorted."""
    if enc.stay is not None or not enc.sources:
        return False
    for s in enc.sources:
        if s.source_type != SourceType.IMAGE:
            return False
        kinds = s.page_kinds if s.page_kinds and len(s.page_kinds) == len(s.page_texts) \
            else [s.document_kind]
        if not kinds or any(k not in REPORT_KINDS for k in kinds):
            return False
    return True


def _live(entries: list[Entry], kind: EntryKind) -> list[Entry]:
    return [e for e in entries if e.kind == kind and e.status != EntryStatus.REJECTED]


def _day(enc: Encounter) -> date:
    return enc.visit_date or enc.created_at.date()


def _could_hold(visit: Encounter) -> bool:
    """A visit a report can be added to: the patient's own, read, not a stay."""
    return visit.stay is None and visit.status == EncounterStatus.READY


def candidates(db: Storage, enc: Encounter) -> list[Candidate]:
    """The patient's other visits this report could belong to, each with its waiting tests
    (confirmed, no confirmed value yet). A visit counts if it is the patient's own, read, not
    archived and not a stay, dated up to `WINDOW_DAYS` before the report's date and not after it,
    and has at least one waiting test. Newest first. Empty unless this visit is reports only and
    has values to move. (It is asked about while the visit is being finished, so the visit's own
    status is not looked at.)"""
    if not reports_only(enc) or enc.visit_date is None:
        return []
    if not any(e.kind == EntryKind.RESULT and e.status == EntryStatus.CONFIRMED
               for e in enc.entries):
        return []
    latest = enc.visit_date  # the report's date: the day printed on it, else the upload day
    earliest = latest - timedelta(days=WINDOW_DAYS)
    visits = {v.id: v for v in db.list_encounters(enc.patient_id)}
    confirmed = db.list_entries(enc.patient_id, EntryStatus.CONFIRMED)
    with_values = {i for e in confirmed if e.kind == EntryKind.RESULT for i in e.links}
    waiting: dict[str, list[Entry]] = {}
    for e in confirmed:
        visit = visits.get(e.encounter_id or "")
        if e.kind != EntryKind.TEST or e.id in with_values or visit is None:
            continue
        if visit.id == enc.id or not _could_hold(visit) or not earliest <= _day(visit) <= latest:
            continue
        waiting.setdefault(visit.id, []).append(e)
    nearest = sorted((visits[i] for i in waiting), key=_day, reverse=True)[:MAX_CANDIDATES]
    return [Candidate(n, v, waiting[v.id][:MAX_WAITING]) for n, v in enumerate(nearest, start=1)]


def report_tests(enc: Encounter) -> list[Entry]:
    """The new report's own tests, numbered from 1 in this order for the model."""
    return _live(enc.entries, EntryKind.TEST)[:MAX_NAMES]


def _one_line(text: str) -> str:
    return " ".join(text.split())


def prompt_text(enc: Encounter, found: list[Candidate]) -> str:
    """What the model is shown: names and dates only. A visit's name goes only if the AI gave it
    (a name the patient wrote is theirs, not for the model)."""
    results = _live(enc.entries, EntryKind.RESULT)
    mine = []
    for i, test in enumerate(report_tests(enc), start=1):
        names = dict.fromkeys(_one_line(r.label) for r in results if test.id in r.links)
        mine.append(f"{i}. {_one_line(test.label)}: "
                    f"{'; '.join(list(names)[:MAX_RESULTS]) or '(no values)'}")
    earlier = []
    for c in found:
        title = _one_line(c.visit.title or "") if c.visit.title_source == "ai" else ""
        earlier.append(f"Visit {c.number}: on {_day(c.visit).isoformat()}" + (f" | {title}" if title else ""))
        earlier.extend(f"  {c.number}.{k} {_one_line(t.label)}" for k, t in enumerate(c.tests, 1))
    return ("New report\n" + "\n".join(mine or ["(no tests)"])
            + "\n\nEarlier visits with tests waiting for a report\n" + "\n".join(earlier))


def ask(ai: AIClient, enc: Encounter, found: list[Candidate]) -> AttachSuggestion | None:
    """One model call. The answer is used only if it names an offered visit and valid pairs, and
    the values could really be filed under them."""
    choice = ai.reason_json(prompts.ATTACH, prompt_text(enc, found), AttachChoice,
                            max_tokens=768, temperature=0.0, thinking=False)
    return accept(choice, enc, found)


def accept(choice: AttachChoice, enc: Encounter, found: list[Candidate]) -> AttachSuggestion | None:
    """Code drops any number it did not offer, a pair whose waiting test is not in the chosen
    visit, and repeats. At least one pair must stand, and a value must end up under it."""
    picked = next((c for c in found if c.number == choice.visit), None)
    if picked is None:
        return None
    mine = report_tests(enc)
    pairs: list[AttachPair] = []
    for p in choice.pairs:
        visit_no, _, k = p.waiting.strip().partition(".")
        if not (visit_no.isdigit() and k.isdigit() and int(visit_no) == picked.number
                and 1 <= int(k) <= len(picked.tests) and 1 <= p.new_test <= len(mine)):
            continue
        new, waiting = mine[p.new_test - 1], picked.tests[int(k) - 1]
        if any(x.new_test_id == new.id or x.waiting_test_id == waiting.id for x in pairs):
            continue
        pairs.append(AttachPair(new_test_id=new.id, waiting_test_id=waiting.id))
    if not pairs or plan(enc, {p.new_test_id: p.waiting_test_id for p in pairs}) is None:
        return None
    return AttachSuggestion(encounter_id=picked.visit.id, pairs=pairs)


def target_of(db: Storage, enc: Encounter) -> tuple[Encounter, list[tuple[Entry, Entry]]] | None:
    """The visit and the (new test, waiting test) pairs the stored suggestion points at, if every
    one still holds: the visit is the patient's own, read, not archived and not a stay, each
    waiting test is still confirmed and still has no confirmed value, and each new test is still
    in this visit. None otherwise (the suggestion is stale)."""
    s = enc.attach_suggestion
    if s is None or not s.pairs or s.encounter_id == enc.id:
        return None
    target = db.get_encounter(s.encounter_id)
    if target is None or target.patient_id != enc.patient_id or not _could_hold(target):
        return None
    valued = {i for e in db.list_entries(enc.patient_id, EntryStatus.CONFIRMED)
              if e.kind == EntryKind.RESULT for i in e.links}
    mine = {e.id: e for e in _live(enc.entries, EntryKind.TEST)}
    waiting = {e.id: e for e in target.entries
               if e.kind == EntryKind.TEST and e.status == EntryStatus.CONFIRMED}
    pairs: list[tuple[Entry, Entry]] = []
    for p in s.pairs:
        new, wait = mine.get(p.new_test_id), waiting.get(p.waiting_test_id)
        if new is None or wait is None or wait.id in valued:
            return None
        pairs.append((new, wait))
    if len({n.id for n, _ in pairs}) != len(pairs) or len({w.id for _, w in pairs}) != len(pairs):
        return None
    return target, pairs


def target_view(db: Storage, enc: Encounter) -> AttachTarget | None:
    """The suggestion for the new visit's page; None when there is none or it no longer holds
    (or no value would end up under a waiting test). The card's words are built here from the
    patient's own test names, never from the model's."""
    if enc.status != EncounterStatus.READY or enc.stay is not None:
        return None
    found = target_of(db, enc)
    if found is None:
        return None
    target, pairs = found
    if plan(enc, {n.id: w.id for n, w in pairs}) is None:
        return None
    return AttachTarget(encounter_id=target.id, test_entry_ids=[w.id for _, w in pairs],
                        test_labels=[w.label for _, w in pairs], visit_date=target.visit_date,
                        visit_title=target.title)


def plan(source: Encounter, pair_map: dict[str, str]) -> tuple[list[Entry], list[Entry]] | None:
    """What an attach does with the new visit's entries, without changing anything: the entries
    that move (copies with their links put right) and the ones dropped.

    `pair_map` is new test id -> waiting test id. Each paired new test is dropped (the waiting test
    stands in for it) and the values under it link to the waiting test. A new test with no pair
    (Urine routine) moves with its values, as a test of the earlier visit. Everything else moves as
    it is, with its status history. None when no value would end up under a waiting test."""
    moved: list[Entry] = []
    dropped: list[Entry] = []
    for e in source.entries:
        if e.id in pair_map:
            dropped.append(e)
            continue
        copy = e.model_copy(deep=True)
        copy.links = list(dict.fromkeys(pair_map.get(i, i) for i in copy.links))
        moved.append(copy)
    waiting = set(pair_map.values())
    if not any(e.kind == EntryKind.RESULT and e.status != EntryStatus.REJECTED
               and waiting & set(e.links) for e in moved):
        return None
    return moved, dropped


def merge(source: Encounter, target: Encounter, pairs: list[tuple[Entry, Entry]]) -> bool:
    """Put the new visit into the target visit, in memory (the caller saves both changes in one
    step and erases the new visit). The files' records (`SourceItem`s) move with the values, so
    every quote still has its source; they are marked `attached_to` the first waiting test, so a
    careful re-read of one replaces only its values (`run._reread`). A file already marked as the
    report for a dropped test is marked for its waiting test. Findings (`discrepancies`) move too,
    so a clash on a medicine read off the report still shows and still stops a bulk confirm. The
    target keeps its own date, name and place. False when there is nothing to attach (nothing
    changed)."""
    pair_map = {new.id: waiting.id for new, waiting in pairs}
    done = plan(source, pair_map)
    if done is None:
        return False
    moved, _ = done
    first = pairs[0][1].id
    for s in source.sources:
        if s.report_for in pair_map:
            s.report_for = pair_map[s.report_for or ""]
        elif s.source_type == SourceType.IMAGE and s.report_for is None:
            s.attached_to = first
    known = {d.id for d in target.discrepancies}
    carried: list[Discrepancy] = [
        d.model_copy(update={"entry_ids": [pair_map.get(i, i) for i in d.entry_ids]})
        for d in source.discrepancies if d.id not in known]
    target.entries = [*target.entries, *moved]
    target.sources = [*target.sources, *source.sources]
    target.bill_lines = [*target.bill_lines, *source.bill_lines]
    target.discrepancies = [*target.discrepancies, *carried]
    target.quote_failures = list(dict.fromkeys([*target.quote_failures, *source.quote_failures]))
    return True
