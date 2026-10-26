"""Cross-checks between a visit's sources and the record.

Billed but not prescribed (`billed_not_prescribed`, amber): a medicine on the bill that the
visit's prescription and consult do not give and that is not current in the record. Compared
by active ingredients. Plain code; worked out again whenever Review loads, so it follows the
patient's confirmations and rejections.

Allergy clash: a medicine in this visit (prescribed, said in the consult, or on the bill) that
shares an active ingredient or a drug family with an allergy. Allergies come from the record
(confirmed) and from this visit (suggested, not rejected).

- Same ingredient or same family: red (`severity` "urgent").
- Related families (penicillins and cephalosporins...): amber (`severity` "review").

The curated tables in `families.py` answer the common cases. Names they do not know go to the
model in one call, and it may only answer with the table's family keys, so the decision itself
is code. A clash that relied on the model says so (`matched_by` "model").

Wording is decision support for review with a clinician: it says what was found and to show it
to a doctor or pharmacist, never to take or not take a medicine.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel

from app.ai.client import AIClient
from app.pipeline import prompts
from app.pipeline.bills import bill_only
from app.pipeline.families import (
    FAMILIES,
    KNOWN,
    AllergyTarget,
    allergy_target,
    families_of,
    ingredients_in,
    related,
)
from app.schemas.record import (
    SHOWN_ON_CARD,
    BillLine,
    Discrepancy,
    Encounter,
    Entry,
    EntryCheck,
    EntryKind,
    EntryStatus,
    Provenance,
    SourceItem,
    SourceType,
)

ALLERGY_CLASH = "allergy_clash"
SHOW_TO = "Please show this to your doctor or pharmacist."


# ---- the model's one narrow question ---------------------------------------------------------
class FamilyAnswer(BaseModel):
    name: str
    kind: Literal["medicine", "group", "not_medicine", "unknown"]
    ingredients: list[str] = []
    families: list[str] = []


class FamilyAnswers(BaseModel):
    items: list[FamilyAnswer]


def family_prompt() -> str:
    lines = [f"{f.key}: {f.name} (e.g. {', '.join(sorted(f.ingredients)[:3])})"
             for f in FAMILIES.values()]
    return prompts.DRUG_FAMILIES + "\n".join(lines)


def ask_families(names: list[str], ai: AIClient) -> dict[str, FamilyAnswer]:
    """The model's reading of names the tables don't know, keyed by the name (casefolded).
    Family keys outside the table are dropped."""
    if not names:
        return {}
    answer = ai.reason_json(family_prompt(), "\n".join(names), FamilyAnswers)
    out = {}
    for a in answer.items:
        out[a.name.casefold().strip()] = a.model_copy(update={
            "ingredients": [i.casefold().strip() for i in a.ingredients if i.strip()],
            "families": [f for f in a.families if f in FAMILIES],
        })
    return out


# ---- what is compared --------------------------------------------------------------------------
@dataclass
class _Allergy:
    entry: Entry
    in_visit: bool  # suggested in this visit, rather than confirmed in the record
    target: AllergyTarget | None = None
    from_model: bool = False

    def families(self) -> set[str]:
        fams = set(self.target.families) if self.target else set()
        for i in self.target.ingredients if self.target else ():
            fams |= families_of(i)
        return fams


@dataclass
class _Drug:
    name: str
    ingredients: list[str]
    from_model: bool
    entry_ids: list[str] = field(default_factory=list)
    bill_line_ids: list[str] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)
    model_families: dict[str, set[str]] = field(default_factory=dict)  # ingredient -> families

    def families(self, ingredient: str) -> set[str]:
        return families_of(ingredient) | self.model_families.get(ingredient, set())


def _drugs(entries: list[Entry], bill_lines: list[BillLine]) -> list[_Drug]:
    """This visit's medicines, one per set of ingredients: a prescribed medicine and its bill
    line are one drug with two pieces of evidence."""
    by_key: dict[str, _Drug] = {}

    def add(name: str, generics: list[str], from_model: bool, prov: Provenance,
            entry_id: str | None = None, bill_id: str | None = None) -> None:
        ingredients = list(generics) or list(ingredients_in(name))
        key = "+".join(sorted(ingredients)) or name.casefold()
        d = by_key.setdefault(key, _Drug(name=name, ingredients=ingredients, from_model=from_model))
        if entry_id:
            if not d.entry_ids:
                d.name = name  # the prescription's name reads better than the bill's
            d.entry_ids.append(entry_id)
        if bill_id:
            d.bill_line_ids.append(bill_id)
        if not any(p.source_id == prov.source_id and p.quote == prov.quote for p in d.provenance):
            d.provenance.append(prov)  # a bill-only visit's suggestion quotes its bill line

    for e in entries:
        if (e.kind == EntryKind.MEDICATION and e.status != EntryStatus.REJECTED
                and EntryCheck.MEDICINE_UNNAMED not in e.checks):
            m = e.medication
            for p in e.provenance[:1]:
                add(e.label, m.generic_names if m else [], bool(m and m.generic_from == "model"),
                    p, entry_id=e.id)
    for b in bill_lines:
        if b.is_drug:
            add(b.name, b.generic_names, b.generic_from == "model", b.provenance, bill_id=b.id)
    return list(by_key.values())


def _allergies(entries: list[Entry], record_allergies: list[Entry]) -> list[_Allergy]:
    out = [_Allergy(e, in_visit=False) for e in record_allergies
           if e.kind == EntryKind.ALLERGY and e.status == EntryStatus.CONFIRMED]
    out += [_Allergy(e, in_visit=True) for e in entries
            if e.kind == EntryKind.ALLERGY and e.status != EntryStatus.REJECTED]
    return out


# ---- the check ---------------------------------------------------------------------------------
def allergy_clashes(
    entries: list[Entry],
    bill_lines: list[BillLine],
    record_allergies: list[Entry],
    ai: AIClient,
) -> list[Discrepancy]:
    """Allergy clashes for one visit. At most one model call, only for names the tables don't
    know, and only when there is a medicine allergy and a medicine to compare."""
    allergies = _allergies(entries, record_allergies)
    drugs = _drugs(entries, bill_lines)
    if not allergies or not drugs:
        return []

    for a in allergies:
        a.target = allergy_target(a.entry.label)
    unknown_allergies = [a for a in allergies if a.target is None]
    if not unknown_allergies and not any(a.target.medicine for a in allergies):
        return []  # only allergies to foods, pollen and the like

    unknown_names = [a.entry.label for a in unknown_allergies]
    for d in drugs:
        unknown_names += [i for i in d.ingredients if i not in KNOWN] or (
            [] if d.ingredients else [d.name])
    try:
        answers = ask_families(list(dict.fromkeys(unknown_names)), ai)
    except Exception:  # noqa: BLE001 - the tables still answer; the visit must not fail here
        answers = {}

    for a in unknown_allergies:
        ans = answers.get(a.entry.label.casefold().strip())
        if ans and ans.kind in ("medicine", "group") and (ans.ingredients or ans.families):
            a.target = AllergyTarget(frozenset(ans.families), frozenset(ans.ingredients))
            a.from_model = True
    for d in drugs:
        ans = None if d.ingredients else answers.get(d.name.casefold().strip())
        if ans and ans.kind == "medicine" and ans.ingredients:
            d.ingredients, d.from_model = ans.ingredients, True
            if ans.families:
                d.model_families = {i: set(ans.families) for i in ans.ingredients}
        for i in d.ingredients:
            ans = answers.get(i)
            if ans and ans.families and not families_of(i):
                d.model_families[i] = set(ans.families)

    found = []
    for d in drugs:
        matches = [m for a in allergies if a.target and (m := _match(a, d))]
        if matches:
            matches.sort(key=lambda m: (m.severity != "urgent", m.allergy.in_visit))
            found.append(_discrepancy(d, matches))
    found.sort(key=lambda x: x.severity != "urgent")
    return found


@dataclass
class _Match:
    allergy: _Allergy
    severity: Literal["urgent", "review"]
    ingredient: str
    family: str | None  # the drug's family that matched
    allergy_family: str | None  # for related families: the allergy's family
    from_model: bool


def _match(a: _Allergy, d: _Drug) -> _Match | None:
    assert a.target is not None
    best: _Match | None = None
    allergy_fams = a.families()
    for i in d.ingredients:
        model = a.from_model or d.from_model or (i in d.model_families and not families_of(i))
        fams = d.families(i)
        if i in a.target.ingredients:
            return _Match(a, "urgent", i, min(fams) if len(fams) == 1 else None, None, model)
        if same := sorted(fams & allergy_fams):
            return _Match(a, "urgent", i, same[0], None, model)
        if best is None:
            for f in sorted(fams):
                if rel := sorted(g for g in allergy_fams if related(f, g)):
                    best = _Match(a, "review", i, f, rel[0], model)
                    break
    return best


def _discrepancy(d: _Drug, matches: list[_Match]) -> Discrepancy:
    m = matches[0]
    allergy_ids = list(dict.fromkeys(x.allergy.entry.id for x in matches))
    allergy_prov = [p for x in matches for p in x.allergy.entry.provenance[:1] if p.quote]
    return Discrepancy(
        check=ALLERGY_CLASH,
        severity=m.severity,
        message=clash_message(d, m),
        entry_ids=[*d.entry_ids, *allergy_ids],
        bill_line_ids=d.bill_line_ids,
        provenance=[*d.provenance, *allergy_prov],
        matched_by="model" if m.from_model else "table",
    )


def clash_message(d: _Drug, m: _Match) -> str:
    """Cautious, plain wording: what was found, and who to show it to."""
    e = m.allergy.entry
    reaction = f" ({e.fields['reaction']})" if e.fields.get("reaction") else ""
    where = "This visit mentions" if m.allergy.in_visit else "Your record lists"
    allergy = f"{where} an allergy to {e.label}{reaction}."
    on_bill_only = not d.entry_ids
    subject = f"The bill lists {d.name}, which" if on_bill_only else d.name
    named = m.ingredient in d.name.casefold()
    fam = FAMILIES[m.family] if m.family else None

    if fam and named:
        what = f"{subject} is {fam.one}."
    elif fam:
        what = f"{subject} contains {m.ingredient}, {fam.one}."
    elif named:
        what = f"The bill lists {d.name}." if on_bill_only else f"This visit includes {d.name}."
    else:
        what = f"{subject} contains {m.ingredient}."

    if m.severity == "urgent":
        return f"{what} {allergy} {SHOW_TO}"
    other = FAMILIES[m.allergy_family].name if m.allergy_family else "these"
    return (f"{what} {allergy} {other[0].upper()}{other[1:]} and {fam.name if fam else 'it'} "
            f"are related, and some people react to both. "
            "Worth asking your doctor or pharmacist about it.")


# ---- billed but not prescribed -----------------------------------------------------------------
BILLED_NOT_PRESCRIBED = "billed_not_prescribed"
CHECK_WITH = "Worth checking with the pharmacy or your doctor."

_WHERE = {SourceType.AUDIO: "the consult", SourceType.TEXT: "your notes",
          SourceType.IMAGE: "the prescription", SourceType.MANUAL: "your record"}


@dataclass
class _Given:
    """A medicine the patient was given: in this visit, or current in the record."""

    entry: Entry
    ingredients: frozenset[str]
    where: str  # "the prescription", "the consult", "your notes", "your record"


def _ingredients(name: str, generics: list[str]) -> frozenset[str]:
    return frozenset(generics) or frozenset(ingredients_in(name))


def _name_words(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", name.casefold()) if len(w) >= 4]


def _same_name(a: str, b: str) -> bool:
    """For lines nobody could name the ingredients of: does the first long word match?"""
    wa, wb = _name_words(a), _name_words(b)
    return bool(wa and wb) and SequenceMatcher(None, wa[0], wb[0]).ratio() >= 0.85


def _with_ingredients(name: str, ingredients: frozenset[str]) -> str:
    """'Pantocid 40 Tab (pantoprazole)'; a name that already says them stays as it is."""
    if not ingredients or all(i in name.casefold() for i in ingredients):
        return name
    return f"{name} ({' + '.join(sorted(ingredients))})"


def billed_not_prescribed(
    entries: list[Entry],
    bill_lines: list[BillLine],
    sources: list[SourceItem],
    record_medicines: list[Entry],
    on: date,
) -> list[Discrepancy]:
    """Medicines on the bill that this visit did not give, and that are not current in the
    record (a refill of a long-term medicine is expected). Plain code, no model call.

    The same ingredients count as given, whatever the strength. Some shared ingredients but not
    all (Augmentin billed, Amoxicillin prescribed) is its own flag. A visit that is only a bill
    is not checked: its medicines are suggestions for the patient to confirm instead."""
    if bill_only(sources):
        return []
    given = [
        _Given(e, _ingredients(e.label, e.medication.generic_names if e.medication else []),
               _WHERE.get(e.provenance[0].source_type, "this visit") if e.provenance else "this visit")
        for e in entries
        if e.kind == EntryKind.MEDICATION and e.status != EntryStatus.REJECTED
    ]
    given += [
        _Given(e, _ingredients(e.label, e.medication.generic_names), "your record")
        for e in record_medicines
        if e.kind == EntryKind.MEDICATION and e.status == EntryStatus.CONFIRMED and e.medication
        and e.medication.state(on) in SHOWN_ON_CARD
    ]
    not_added = [e for e in entries
                 if e.kind == EntryKind.MEDICATION and e.status == EntryStatus.REJECTED]
    found = []
    for b in bill_lines:
        if not b.is_drug:
            continue
        ingredients = _ingredients(b.name, b.generic_names)
        if ingredients:
            if any(g.ingredients == ingredients for g in given):
                continue
            partial = [g for g in given if g.ingredients & ingredients]
            rejected = [e for e in not_added
                        if _ingredients(e.label, e.medication.generic_names if e.medication
                                        else []) == ingredients]
        else:
            if any(_same_name(b.name, g.entry.label) for g in given):
                continue
            partial = []
            rejected = [e for e in not_added if _same_name(b.name, e.label)]
        if rejected and not partial:
            # The visit did give it; the patient chose not to add it. Say exactly that.
            message = (f"{_with_ingredients(b.name, ingredients)} is on the bill. You chose not to "
                       f"add {rejected[0].label} from this visit, so it is not in your record.")
        elif partial:
            g = partial[0]  # this visit before the record
            message = (f"The bill has {_with_ingredients(b.name, ingredients)}; {g.where} has "
                       f"{_with_ingredients(g.entry.label, g.ingredients)}. Worth checking it is "
                       f"the medicine meant.")
        else:
            message = (f"{_with_ingredients(b.name, ingredients)} is on the bill, but not on the "
                       f"prescription or in the consult. {CHECK_WITH}")
        found.append(Discrepancy(
            check=BILLED_NOT_PRESCRIBED,
            severity="review",
            message=message,
            entry_ids=[g.entry.id for g in partial[:1]] or [e.id for e in rejected[:1]],
            bill_line_ids=[b.id],
            provenance=[b.provenance, *[p for g in partial[:1] for p in g.entry.provenance[:1]]],
            matched_by="model" if b.generic_from == "model" else "table",
        ))
    return found


# ---- what Review shows -------------------------------------------------------------------------
def for_review(
    enc: Encounter,
    confirmed_elsewhere: list[Entry],
    entry: Callable[[str], Entry | None],
    on: date,
) -> list[Discrepancy]:
    """The visit's findings as they stand after the patient's review: allergy clashes that
    rejections settled are left out, and the bill is compared again with what is in the visit
    and the record now. Red first."""
    clashes = still_open([d for d in enc.discrepancies if d.check == ALLERGY_CLASH], entry,
                         bill_counts=not bill_only(enc.sources))
    # A hospital bill lists what was used in hospital, not what was prescribed to take home.
    billed = [] if enc.stay else billed_not_prescribed(enc.entries, enc.bill_lines, enc.sources,
                                                       confirmed_elsewhere, on)
    return [*[d for d in clashes if d.severity == "urgent"],
            *[d for d in clashes if d.severity != "urgent"], *billed]


def still_open(found: list[Discrepancy], entry: Callable[[str], Entry | None],
               *, bill_counts: bool = True) -> list[Discrepancy]:
    """Clashes that still stand after the patient's review: one whose allergy (every one it
    names) was rejected, or whose medicine was rejected and is not on the bill, is left out.

    In a visit that is only a bill (`bill_counts` False), the suggestion is the bill line:
    rejecting it means "not mine" (bought for someone else), which settles the clash too."""
    out = []
    for x in found:
        if x.check != ALLERGY_CLASH:
            out.append(x)
            continue
        named = [(i, entry(i)) for i in x.entry_ids]
        allergies = [e for _, e in named if e and e.kind == EntryKind.ALLERGY]
        drugs = [e for _, e in named if e and e.kind != EntryKind.ALLERGY]
        allergy_ok = any(e.status != EntryStatus.REJECTED for e in allergies)
        billed = bill_counts and bool(x.bill_line_ids)
        drug_ok = billed or any(e.status != EntryStatus.REJECTED for e in drugs)
        if allergy_ok and drug_ok:
            out.append(x)
    return out
