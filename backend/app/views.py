"""Views derived from a patient's entries: the consolidated summary, the visit-by-visit report,
the timeline and the emergency summary.

All use CONFIRMED entries only. Every visit's entry is kept as evidence; when the same fact
shows up in several visits, the views show it once and list where it was seen. Pure functions,
no storage access, so the same derivation serves the API, the eval and tests.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel

from app.parameters import MEASURES, PRESETS, Measure, Preset, measure_of, unit_key
from app.pipeline.medicines import from_table
from app.schemas.patient import Patient
from app.schemas.record import (
    PINNED_KINDS,
    SHOWN_ON_CARD,
    Encounter,
    EncounterStatus,
    Entry,
    EntryKind,
    EntryStatus,
    HospitalStay,
    MedicationState,
    SourceType,
    Speaker,
    TestResult,
    Tier,
    VisitDateSource,
)

# ---- one fact, many sightings ------------------------------------------------------------
_FILLER = re.compile(
    r"\b(allerg(y|ies|ic)|to|drugs?|medicines?|tabs?|tablets?|syp|syrup|caps?|capsules?|"
    r"inj|injection|inhaler)\b"
)


def fact_key(e: Entry) -> str:
    """Entries that state the same fact share a key: kind plus a normalized name.

    'Penicillin', 'penicillin' and 'Penicillin allergy' match; so do 'Azithromycin 500 mg' and
    'azithromycin 250 mg' (same medicine, another course). Medicines with known active
    ingredients match by those, so 'Dolo 650' and 'Paracetamol 650 mg' are one fact. Entries
    stored before generic names existed are looked up in the curated table here.
    """
    name = e.label.casefold()
    if e.kind == EntryKind.RESULT:  # by measure, so "Fasting blood sugar" and a hand "Blood sugar (fasting)" agree
        return f"result:{measure_of(e.label).key}"
    if e.kind == EntryKind.MEDICATION:
        generics = (e.medication.generic_names if e.medication else []) or from_table(e.label)
        if generics:
            return f"{e.kind.value}:{'+'.join(sorted(generics))}"
        name = re.split(r"\d", name, maxsplit=1)[0]  # the medicine, not its strength
    name = _FILLER.sub(" ", re.sub(r"[^\w\s]", " ", name))
    return f"{e.kind.value}:{' '.join(name.split()) or e.label.casefold()}"


def entry_date(e: Entry, visit_dates: dict[str, date]) -> date:
    """The day an entry belongs to: a test value's report date, else its visit's date, else
    when it was added."""
    if e.result and e.result.measured_on:
        return e.result.measured_on
    if e.encounter_id and e.encounter_id in visit_dates:
        return visit_dates[e.encounter_id]
    first = e.status_history[0].at if e.status_history else datetime.min
    return first.date()


def _added(e: Entry) -> datetime:
    """When the entry first got a status (read as it is, or confirmed): only the fallback for an
    entry whose visit is not known (added by hand)."""
    return e.status_history[0].at if e.status_history else datetime.min.replace(tzinfo=UTC)


class Sighting(BaseModel):
    entry_id: str
    encounter_id: str | None  # None: added by hand
    on: date
    said_by: Speaker | None


class Fact(BaseModel):
    key: str
    kind: EntryKind
    latest: Entry  # the most recent statement of this fact
    sightings: list[Sighting]  # every visit it was confirmed in, newest first


def consolidate(
    entries: list[Entry], visit_dates: dict[str, date], uploaded: dict[str, datetime] | None = None
) -> list[Fact]:
    """Group confirmed entries into facts, newest statement first. Two visits on the same day
    (a prescription uploaded twice) go by when each visit was uploaded (`uploaded`, as the visit
    list sorts them), so the latest statement is the one from the visit added last."""
    uploaded = uploaded or {}
    groups: dict[str, list[Entry]] = {}
    for e in entries:
        if e.status == EntryStatus.CONFIRMED:
            groups.setdefault(fact_key(e), []).append(e)
    facts = []
    for key, group in groups.items():
        group.sort(
            key=lambda e: (entry_date(e, visit_dates), uploaded.get(e.encounter_id or "") or _added(e)),
            reverse=True,
        )
        facts.append(
            Fact(
                key=key,
                kind=group[0].kind,
                latest=group[0],
                sightings=[
                    Sighting(entry_id=e.id, encounter_id=e.encounter_id,
                             on=entry_date(e, visit_dates), said_by=e.said_by)
                    for e in group
                ],
            )
        )
    facts.sort(key=lambda f: f.sightings[0].on, reverse=True)
    return facts


def already_in_record(proposed: list[Entry], confirmed_elsewhere: list[Entry]) -> dict[str, str]:
    """For the Review screen: proposed entry id -> the label it matches in the record."""
    known = {fact_key(e): e.label for e in confirmed_elsewhere
             if e.status == EntryStatus.CONFIRMED}
    return {e.id: known[fact_key(e)] for e in proposed if fact_key(e) in known}


# ---- visits ------------------------------------------------------------------------------
class VisitSource(BaseModel):
    source_type: SourceType
    filename: str | None


class Visit(BaseModel):
    encounter_id: str | None  # None: the "added by hand" group
    visit_date: date | None
    visit_date_source: VisitDateSource | None
    title: str | None = None  # the visit's name (the model's suggestion or the patient's own)
    title_source: Literal["ai", "patient"] | None = None
    facility: str | None = None  # hospital, clinic or lab
    uploaded_at: datetime | None
    status: EncounterStatus | None
    sources: list[VisitSource]
    entries: list[Entry]  # confirmed entries from this visit
    pending: int  # proposals still waiting for review
    stay: HospitalStay | None = None  # an emergency or hospital stay the patient logged


def visits(encounters: list[Encounter], entries: list[Entry]) -> list[Visit]:
    """One card per visit, newest first, plus hand-added entries as their own group."""
    by_visit: dict[str | None, list[Entry]] = {}
    for e in entries:
        by_visit.setdefault(e.encounter_id, []).append(e)

    def confirmed(es: list[Entry]) -> list[Entry]:
        return [e for e in es if e.status == EntryStatus.CONFIRMED]

    out = [
        Visit(
            encounter_id=enc.id,
            # Visits recorded before visit dates existed fall back to their upload day.
            visit_date=enc.visit_date or enc.created_at.date(),
            visit_date_source=enc.visit_date_source or VisitDateSource.UPLOAD,
            # A stay shows the place the patient wrote.
            title=enc.title,
            title_source=enc.title_source,
            facility=(enc.stay.place if enc.stay and enc.stay.place else enc.facility),
            uploaded_at=enc.created_at,
            status=enc.status,
            sources=[VisitSource(source_type=s.source_type, filename=s.filename)
                     for s in enc.sources],
            entries=confirmed(by_visit.get(enc.id, [])),
            pending=sum(e.status == EntryStatus.PROPOSED for e in by_visit.get(enc.id, [])),
            stay=enc.stay,
        )
        for enc in encounters
    ]
    out.sort(key=lambda v: (v.visit_date or date.min, v.uploaded_at), reverse=True)
    by_hand = confirmed(by_visit.get(None, []))
    if by_hand:
        out.append(Visit(encounter_id=None, visit_date=None, visit_date_source=None,
                         uploaded_at=None, status=None, sources=[], entries=by_hand, pending=0))
    return out


# ---- timeline ----------------------------------------------------------------------------
def timeline(entries: list[Entry]) -> list[Entry]:
    """Dated events, each once, pinned kinds first, newest first; undated last in each group.

    ISO partial dates ('2019', '2019-03', '2019-03-12') sort correctly as strings.
    """
    seen: set[tuple[str, str | None]] = set()
    events = []
    for e in entries:
        if e.is_event and (fact_key(e), e.occurred_on) not in seen:
            seen.add((fact_key(e), e.occurred_on))
            events.append(e)
    dated = sorted((e for e in events if e.occurred_on), key=lambda e: e.occurred_on, reverse=True)
    ordered = dated + [e for e in events if not e.occurred_on]
    return [e for e in ordered if e.kind in PINNED_KINDS] + [
        e for e in ordered if e.kind not in PINNED_KINDS
    ]


# ---- the patient's record ----------------------------------------------------------------
class PatientRecord(BaseModel):
    patient: Patient
    facts: list[Fact]  # consolidated: each fact once
    visits: list[Visit]  # visit by visit
    timeline: list[Entry]


def visit_dates(encounters: list[Encounter]) -> dict[str, date]:
    return {e.id: e.visit_date or e.created_at.date() for e in encounters}


def visit_uploaded(encounters: list[Encounter]) -> dict[str, datetime]:
    return {e.id: e.created_at for e in encounters}


def patient_record(
    patient: Patient, entries: list[Entry], encounters: list[Encounter]
) -> PatientRecord:
    confirmed = [e for e in entries if e.status == EntryStatus.CONFIRMED]
    return PatientRecord(
        patient=patient,
        facts=consolidate(confirmed, visit_dates(encounters), visit_uploaded(encounters)),
        visits=visits(encounters, entries),
        timeline=timeline(confirmed),
    )


# ---- my health numbers -------------------------------------------------------------------
class NumberRange(BaseModel):
    """A range printed on a report. `borrowed`: this point had none of its own, and is compared
    with the latest one printed for the same measure and unit."""

    low: float | None
    high: float | None
    on: date | None
    encounter_id: str | None
    borrowed: bool = False


class NumberPoint(BaseModel):
    entry_id: str
    on: date
    value: float
    unit: str | None
    source: Literal["report", "you"]
    encounter_id: str | None
    range: NumberRange | None
    position: Literal["low", "high", "within"] | None  # against `range`; None: nothing to compare with
    removed: bool = False  # the patient took it out: listed struck through, not charted
    edited: bool = False


class NumberSeries(BaseModel):
    key: str
    name: str
    unit: str | None
    unit_missing: bool  # values whose unit was not printed, kept apart from those with one
    points: list[NumberPoint]  # oldest first
    latest: NumberPoint | None  # the newest one still in the record
    range: NumberRange | None  # the latest range printed for this series


class NumbersGroup(BaseModel):
    """One card: a measure (blood pressure: two lines)."""

    key: str
    name: str
    series: list[NumberSeries]
    latest_on: date | None
    count: int  # readings in the record
    flagged: bool  # the newest reading of some line is outside its range


class Numbers(BaseModel):
    groups: list[NumbersGroup]
    presets: list[Preset]


def _replaced(e: Entry) -> bool:
    """A value a careful re-read replaced: not the patient's choice, never shown."""
    return e.status == EntryStatus.REJECTED and bool(e.status_history) and e.status_history[-1].by == "system"


def numbers(entries: list[Entry], encounters: list[Encounter]) -> Numbers:
    """Every number in the record, by measure and unit, over time. No unit is converted, and a
    number is only compared with a range printed on a report (its own, else the latest for the
    same measure and unit); what it means is for the patient's doctor."""
    dates = visit_dates(encounters)
    found: dict[str, list[tuple[Measure, Entry, date]]] = {}
    for e in entries:
        r = e.result
        if (e.kind != EntryKind.RESULT or r is None or r.value is None or _replaced(e)
                or e.status == EntryStatus.PROPOSED):
            continue
        m = measure_of(e.label)
        found.setdefault(m.card, []).append((m, e, r.measured_on or entry_date(e, dates)))

    order = {m.key: i for i, m in enumerate(MEASURES)}
    cards: dict[str, tuple[str, list[NumberSeries]]] = {}  # a card's key -> (its name, its lines)
    for card, items in found.items():
        per_measure: dict[str, dict[str, list[tuple[Measure, Entry, date]]]] = {}
        for key in sorted({m.key for m, _, _ in items}, key=lambda k: order.get(k, len(order))):
            mine = [t for t in items if t[0].key == key]
            units = {unit_key(e.result.unit) for _, e, _ in mine if e.result} - {""}

            def bucket(e: Entry, units: set[str] = units) -> str:
                u = unit_key(e.result.unit) if e.result else ""
                if u:
                    return u
                # No unit printed: it joins the only unit this measure has, else stands apart.
                return "" if not units else next(iter(units)) if len(units) == 1 else "?"

            buckets: dict[str, list[tuple[Measure, Entry, date]]] = {}
            for t in mine:
                buckets.setdefault(bucket(t[1]), []).append(t)
            per_measure[key] = buckets
        # A measure in several units makes a card per unit (one chart, one scale): nothing is
        # converted. Decided for the whole card, so blood pressure's two lines stay together.
        split = any(len(b) > 1 for b in per_measure.values())
        for key, buckets in per_measure.items():
            for u in sorted(buckets):
                s = _series(key, buckets[u], u == "?")
                card_key = f"{card}:{u or 'none'}" if split else card
                name = items[0][0].card_name + (f", {s.unit or 'unit not printed'}" if split else "")
                cards.setdefault(card_key, (name, []))[1].append(s)
    groups = []
    for card_key, (name, series) in cards.items():
        groups.append(NumbersGroup(
            key=card_key, name=name, series=series,
            latest_on=max((s.latest.on for s in series if s.latest), default=None),
            # a blood pressure is two numbers but one reading: count the longest line
            count=max((sum(1 for p in s.points if not p.removed) for s in series), default=0),
            flagged=any(s.latest and s.latest.position in ("low", "high") for s in series),
        ))
    groups.sort(key=lambda g: g.name.casefold())  # newest first, then A to Z (the sort is stable)
    groups.sort(key=lambda g: g.latest_on or date.min, reverse=True)
    return Numbers(groups=groups, presets=list(PRESETS))


def _series(key: str, items: list[tuple[Measure, Entry, date]], unit_missing: bool) -> NumberSeries:
    items = sorted(items, key=lambda t: t[2])
    # A value with no unit printed may sit on a line that has one (it joined it), but it is not
    # known to be in that unit: it never lends its range to the line, nor borrows the line's.
    has_unit = any(e.result and e.result.unit for _, e, _ in items)
    printed = [(d, e) for _, e, d in items
               if e.status != EntryStatus.REJECTED and e.result
               and (e.result.ref_low is not None or e.result.ref_high is not None)
               and (e.result.unit or not has_unit)]
    latest_range = None
    if printed:
        d, e = printed[-1]
        latest_range = NumberRange(low=e.result.ref_low, high=e.result.ref_high, on=d,
                                   encounter_id=e.encounter_id)
    points = []
    for _, e, d in items:
        r = e.result
        assert r is not None and r.value is not None
        own = r.ref_low is not None or r.ref_high is not None
        can_borrow = bool(r.unit) or not has_unit
        rng = (NumberRange(low=r.ref_low, high=r.ref_high, on=d, encounter_id=e.encounter_id)
               if own else latest_range and can_borrow and latest_range.model_copy(update={"borrowed": True}))
        rng = rng or None
        position = (TestResult(value=r.value, ref_low=rng.low, ref_high=rng.high).position()
                    if rng else None)
        points.append(NumberPoint(
            entry_id=e.id, on=d, value=r.value, unit=r.unit,
            source="you" if any(p.source_type == SourceType.MANUAL for p in e.provenance) else "report",
            encounter_id=e.encounter_id, range=rng, position=position,
            removed=e.status == EntryStatus.REJECTED, edited=bool(e.edits),
        ))
    live = [p for p in points if not p.removed]
    shown_unit = next((p.unit for p in reversed(points) if p.unit), None)
    return NumberSeries(key=key, name=items[0][0].name, unit=shown_unit, unit_missing=unit_missing,
                        points=points, latest=live[-1] if live else None, range=latest_range)


# ---- deleting a visit --------------------------------------------------------------------
class Leaving(BaseModel):
    kind: EntryKind
    label: str


class Removal(BaseModel):
    """What deleting one visit would take out of the record, for the confirmation."""

    visit_date: date | None
    stay: bool
    confirmed: int  # facts from this visit that are in the record
    values: int  # of those, test values
    waiting: int  # suggestions still waiting for review
    leaving: list[Leaving]  # facts only this visit says: they leave the record
    leaves_card: list[Leaving]  # on the emergency card today, not any more afterwards
    joins_card: list[Leaving]  # not on the card today, there afterwards (an older statement)


def _card(summary: EmergencySummary, label: Callable[[Entry], str]) -> dict[str, Leaving]:
    """Everything the emergency card shows, by what it is (a fact once, an event by its date)."""
    items: dict[str, Leaving] = {}
    facts = [*summary.allergies, *(m.entry for m in summary.medications), *summary.conditions,
             *summary.implants]
    for e in facts:
        items[fact_key(e)] = Leaving(kind=e.kind, label=label(e))
    for e in summary.timeline:
        items[f"{fact_key(e)}|{e.occurred_on}"] = Leaving(kind=e.kind, label=label(e))
    for s in summary.open_stays:
        items[f"stay|{s.kind}|{s.since}"] = Leaving(
            kind=EntryKind(s.kind), label=f'Shown as "In hospital now" ({s.place or s.reason or "stay"})')
    return items


def removal_preview(
    enc: Encounter, patient: Patient, entries: list[Entry], encounters: list[Encounter],
    today: date | None = None,
) -> Removal:
    """What deleting this visit changes. `entries` and `encounters` are all of the patient's.

    `leaving`: the visit's confirmed facts that nothing else in the record says. `leaves_card` and
    `joins_card` are worked out by building the emergency card with and without the visit, because
    the card shows each fact's latest statement: deleting a newer ongoing course can drop a medicine
    whose older course has ended, and deleting a "stopped" can bring an older one back."""
    today = today or date.today()
    rest = [e for e in entries if e.encounter_id != enc.id]
    kept = {fact_key(e) for e in rest if e.status == EntryStatus.CONFIRMED}
    mine = [e for e in entries if e.encounter_id == enc.id and e.status == EntryStatus.CONFIRMED]

    def label(e: Entry) -> str:  # the stay's own entry is "Pneumonia": say it is the admission
        if enc.stay and e.id == enc.stay.entry_id:
            what = "Hospital admission" if e.kind == EntryKind.ADMISSION else "Emergency visit"
            return e.label if e.label.startswith(what) else f"{what}: {e.label}"
        return e.label

    seen: set[str] = set()
    leaving = []
    for e in mine:
        if e.kind != EntryKind.RESULT and fact_key(e) not in kept and fact_key(e) not in seen:
            seen.add(fact_key(e))
            leaving.append(Leaving(kind=e.kind, label=label(e)))
    before = _card(emergency_summary(patient, entries, encounters, today), label)
    after = _card(emergency_summary(patient, rest, [x for x in encounters if x.id != enc.id], today), label)
    return Removal(
        visit_date=enc.visit_date, stay=enc.stay is not None, confirmed=len(mine),
        values=sum(e.kind == EntryKind.RESULT for e in mine),
        waiting=sum(e.status == EntryStatus.PROPOSED for e in enc.entries), leaving=leaving,
        leaves_card=[v for k, v in before.items() if k not in after],
        joins_card=[v for k, v in after.items() if k not in before],
    )


# ---- emergency summary -------------------------------------------------------------------
class CardMedication(BaseModel):
    entry: Entry
    state: MedicationState  # END_UNKNOWN is shown labelled "duration not known"


class OpenStay(BaseModel):
    """'In hospital since': a stay the patient logged and has not closed yet."""

    kind: str  # emergency_visit | admission
    place: str | None
    reason: str | None
    since: date


class EmergencySummary(BaseModel):
    patient: Patient
    open_stays: list[OpenStay] = []  # newest first
    allergies: list[Entry]
    medications: list[CardMedication]
    conditions: list[Entry]  # diagnoses tagged for the emergency card (chronic conditions)
    implants: list[Entry]
    timeline: list[Entry]  # dated events: surgeries and implants pinned first, then newest first


def open_stays(encounters: list[Encounter], entries: list[Entry]) -> list[OpenStay]:
    """Stays still open whose record entry the patient has not removed."""
    confirmed = {e.id for e in entries if e.status == EntryStatus.CONFIRMED}
    stays = [enc.stay for enc in encounters
             if enc.stay and enc.stay.open and enc.stay.entry_id in confirmed]
    return [OpenStay(kind=s.kind, place=s.place, reason=s.reason, since=s.started_on)
            for s in sorted(stays, key=lambda s: s.started_on, reverse=True)]


def emergency_summary(
    patient: Patient,
    entries: list[Entry],
    encounters: list[Encounter] | None = None,
    today: date | None = None,
) -> EmergencySummary:
    """Each fact once, from its latest confirmed statement. A medicine shows only if its latest
    course is still current (or has no known end)."""
    today = today or date.today()
    facts = consolidate(entries, visit_dates(encounters or []), visit_uploaded(encounters or []))

    def latest(kind: EntryKind) -> list[Entry]:
        return [f.latest for f in facts if f.kind == kind]

    medications = []
    for e in latest(EntryKind.MEDICATION):
        state = e.medication.state(today) if e.medication else MedicationState.END_UNKNOWN
        if state in SHOWN_ON_CARD:
            medications.append(CardMedication(entry=e, state=state))
    confirmed = [e for e in entries if e.status == EntryStatus.CONFIRMED]
    return EmergencySummary(
        patient=patient,
        open_stays=open_stays(encounters or [], entries),
        allergies=latest(EntryKind.ALLERGY),
        medications=medications,
        conditions=[e for e in latest(EntryKind.DIAGNOSIS) if e.tier == Tier.EMERGENCY_CARD],
        implants=latest(EntryKind.IMPLANT),
        timeline=timeline(confirmed),
    )
