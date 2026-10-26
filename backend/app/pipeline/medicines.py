"""Medicine names and wording checks, run on proposals before the patient sees them.

- Brand to generic: a small curated table of common Indian brands, so 'Dolo 650' and
  'Paracetamol 650 mg' are known to be the same medicine (consolidation, cross-checks). Names
  the table doesn't know go to the model in one call; its answer is stored as `generic_from
  = "model"`, so the source of every match is visible.
- Unnamed medicines: 'anti-malarial tablets' is a purpose, not a medicine. It stays a proposal,
  flagged so the patient checks the prescription for the actual name.
- Name in quote: the medicine or allergy name should appear in the words it came from (allowing
  for misspelling, e.g. 'parasetamal'). If not, the patient is asked to check it.
- English: labels and details are stored in English, quotes in the original language (plan
  decision 2026-10-11). Hindi words left in details get one translation pass, then a flag.

Matching and every decision are code; the model only answers the two narrow questions.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel

from app.ai.client import AIClient
from app.pipeline import prompts
from app.schemas.record import Entry, EntryCheck, EntryKind

# Brand -> active ingredients (lowercase). The demo drugs plus a few very common ones.
BRANDS: dict[str, tuple[str, ...]] = {
    "dolo": ("paracetamol",),
    "crocin": ("paracetamol",),
    "calpol": ("paracetamol",),
    "augmentin": ("amoxicillin", "clavulanic acid"),
    "clavam": ("amoxicillin", "clavulanic acid"),
    "septran": ("sulfamethoxazole", "trimethoprim"),
    "bactrim": ("sulfamethoxazole", "trimethoprim"),
    "montair lc": ("montelukast", "levocetirizine"),
    "montair": ("montelukast",),
    "asthalin": ("salbutamol",),
    "pantocid": ("pantoprazole",),
    "pan": ("pantoprazole",),
    "ascoril ls": ("ambroxol", "levosalbutamol", "guaifenesin"),
    "azithral": ("azithromycin",),
    "azee": ("azithromycin",),
    "sucrafil": ("sucralfate",),
    "glycomet": ("metformin",),
    "amlong": ("amlodipine",),
    "thyronorm": ("levothyroxine",),
    "eltroxin": ("levothyroxine",),
    "brufen": ("ibuprofen",),
    "combiflam": ("ibuprofen", "paracetamol"),
    "allegra": ("fexofenadine",),
    "cetzine": ("cetirizine",),
    "zyrtec": ("cetirizine",),
    "omez": ("omeprazole",),
    "lariago": ("chloroquine",),
    "coartem": ("artemether", "lumefantrine"),
}

# Generic names recognised as they are. Multi-word names are matched as phrases.
GENERICS: frozenset[str] = frozenset(
    {g for gs in BRANDS.values() for g in gs}
    | {
        "co-trimoxazole", "cotrimoxazole", "amlodipine", "atorvastatin", "aspirin", "cefixime",
        "ciprofloxacin", "doxycycline", "insulin", "levothyroxine", "losartan", "telmisartan",
        "metronidazole", "ondansetron", "prednisolone", "ranitidine", "rabeprazole",
        "domperidone", "diclofenac", "artesunate", "primaquine", "budesonide", "levocetirizine",
    }
)
_ALIASES = {"co-trimoxazole": ("sulfamethoxazole", "trimethoprim"),
            "cotrimoxazole": ("sulfamethoxazole", "trimethoprim")}

_NOISE = {
    "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules", "syp", "syrup",
    "susp", "suspension", "inj", "injection", "drops", "drop", "inhaler", "cream", "ointment",
    "gel", "duo", "ds", "sr", "er", "xl", "mg", "ml", "mcg", "g", "iu", "puff", "puffs",
}


def _words(text: str) -> list[str]:
    """Lowercase words without numbers, units or dosage forms."""
    words = re.sub(r"[^a-z\s-]", " ", text.casefold()).replace("-", " ").split()
    return [w for w in words if w not in _NOISE]


def _has_phrase(words: list[str], phrase: str) -> bool:
    p = phrase.replace("-", " ").split()
    return any(words[i:i + len(p)] == p for i in range(len(words) - len(p) + 1))


def _close_brand(words: list[str]) -> str | None:
    """A one-word brand read with a stray space or a slip ('septr an' -> 'septran'). Long
    brands only, and a high bar, so short words never match by accident."""
    candidates = words + [a + b for a, b in pairwise(words)]
    for brand in BRANDS:
        if " " not in brand and len(brand) >= 6:
            for c in candidates:
                if SequenceMatcher(None, brand, c).ratio() >= 0.85:
                    return brand
    return None


def from_table(label: str) -> tuple[str, ...] | None:
    """Active ingredients from the curated table, or None if the table doesn't know the name."""
    words = _words(label)
    for brand in sorted(BRANDS, key=len, reverse=True):  # 'montair lc' before 'montair'
        if _has_phrase(words, brand):
            return BRANDS[brand]
    if close := _close_brand(words):
        return BRANDS[close]
    found: list[str] = []
    for g in sorted(GENERICS, key=len, reverse=True):
        if _has_phrase(words, g):
            for ingredient in _ALIASES.get(g, (g,)):
                if ingredient not in found:
                    found.append(ingredient)
    return tuple(found) or None


def _similar(a: str, b: str) -> bool:
    return a == b or (len(a) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.75)


def name_in_quote(entry: Entry) -> bool:
    """Does the medicine or allergy name appear in the quote? Loose: misspellings count."""
    quote_words = _words(" ".join(p.quote or "" for p in entry.provenance))
    names = [w for w in _words(entry.label) if len(w) >= 4 and w not in {"allergy", "allergic"}]
    if entry.medication:
        names += [w for g in entry.medication.generic_names for w in g.split() if len(w) >= 4]
    if not names:
        return True  # nothing distinctive to look for
    return any(_similar(n, q) for n in names for q in quote_words)


# Romanized Hindi words that do not occur in English record wording. Ambiguous ones
# ('do', 'me', 'bar', 'sir') are left out on purpose.
_HINDI = frozenset({
    "ek", "teen", "chaar", "char", "paanch", "panch", "chhe", "saat", "aath", "das", "din",
    "dino", "raat", "subah", "shaam", "sham", "dopahar", "khane", "khana", "baad", "pehle",
    "pahle", "zaroorat", "jarurat", "zarurat", "pade", "toh", "baar", "mein", "ko", "ke", "ki",
    "tak", "goli", "dawai", "dawa", "lena", "lijiye", "aur", "nahi", "nahin", "agar", "phir",
    "saans", "bukhar", "khansi", "roz", "rozana", "hafte", "mahine", "pah",
})


def hindi_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", text.casefold()) if w in _HINDI]


# ---- the model's two narrow questions ---------------------------------------------------------
class GenericMatch(BaseModel):
    name: str
    status: Literal["specific", "vague", "unknown"]
    generics: list[str]


class GenericMatches(BaseModel):
    items: list[GenericMatch]


class Translation(BaseModel):
    english: list[str]


def ask_generics(names: list[str], ai: AIClient) -> dict[str, GenericMatch]:
    """The model's match for names the table doesn't know, keyed by the name as given
    (casefolded). One call; names it skipped are missing from the result."""
    if not names:
        return {}
    answer = ai.reason_json(prompts.GENERIC_NAMES, "\n".join(names), GenericMatches)
    return {
        m.name.casefold(): m.model_copy(
            update={"generics": [g.casefold().strip() for g in m.generics if g.strip()]}
        )
        for m in answer.items
    }


def check_entries(entries: list[Entry], ai: AIClient) -> list[Entry]:
    """Fill generic names and set checks on proposals. At most two short model calls."""
    meds = [e for e in entries if e.kind == EntryKind.MEDICATION and e.medication]
    unknown: list[Entry] = []
    for e in meds:
        generics = from_table(e.label)
        if generics:
            e.medication.generic_names, e.medication.generic_from = list(generics), "table"
        else:
            unknown.append(e)
    by_name = ask_generics([e.label for e in unknown], ai)
    for e in unknown:
        m = by_name.get(e.label.casefold())
        if m is None:
            continue
        if m.status == "vague":
            e.checks.append(EntryCheck.MEDICINE_UNNAMED)
        elif m.status == "specific" and m.generics:
            e.medication.generic_names, e.medication.generic_from = m.generics, "model"

    for e in entries:
        named = EntryCheck.MEDICINE_UNNAMED not in e.checks
        if e.kind in (EntryKind.MEDICATION, EntryKind.ALLERGY) and named and not name_in_quote(e):
            e.checks.append(EntryCheck.NAME_NOT_IN_QUOTE)

    _english(entries, ai)
    return entries


TRANSLATED_BY = "system"  # an edit made by the translation pass, not by the patient


def has_hindi(e: Entry) -> bool:
    return bool(hindi_words(e.label) or any(hindi_words(v) for v in e.fields.values()))


def translate_confirmed(e: Entry, ai: AIClient) -> bool:
    """English for an entry already in the record (confirmed before the translation pass, or
    still Hindi after it). Goes through `apply_edit`, so the original words stay in the entry's
    history, marked as a translation rather than a patient edit. True if anything changed."""
    spots = ([("label", e.label)] if hindi_words(e.label) else []) + [
        (k, v) for k, v in e.fields.items() if hindi_words(v)]
    if not spots:
        return False
    values = [" ".join(v.split()) for _, v in spots]
    english = ai.reason_json(prompts.TRANSLATE_FIELDS, "\n".join(values), Translation).english
    if len(english) != len(values):
        return False  # the answer doesn't line up: leave the entry as it is
    label, fields = None, {}
    for (k, old), new in zip(spots, english, strict=True):
        new = new.strip()
        if not new or new == old or hindi_words(new):
            continue  # nothing usable: keep the patient's confirmed words
        if k == "label":
            label = new
        else:
            fields[k] = new
    if label is None and not fields:
        return False
    e.apply_edit(TRANSLATED_BY, label=label, fields=fields or None)
    if EntryCheck.NOT_ENGLISH in e.checks and not has_hindi(e):
        e.checks.remove(EntryCheck.NOT_ENGLISH)
    return True


def _english(entries: list[Entry], ai: AIClient) -> None:
    """Translate Hindi left in labels and details, once; flag what is still Hindi after."""
    spots: list[tuple[Entry, str | None]] = []  # (entry, field name or None for the label)
    for e in entries:
        if hindi_words(e.label):
            spots.append((e, None))
        spots += [(e, k) for k, v in e.fields.items() if hindi_words(v)]
    if not spots:
        return
    values = [" ".join((e.label if k is None else e.fields[k]).split()) for e, k in spots]
    english = ai.reason_json(prompts.TRANSLATE_FIELDS, "\n".join(values), Translation).english
    if len(english) != len(values):
        english = values  # the answer doesn't line up: keep the original, flag below
    for (e, k), new in zip(spots, english, strict=True):
        new = new.strip() or (e.label if k is None else e.fields[k])
        if k is None:
            e.label = new
        else:
            e.fields[k] = new
        if hindi_words(new) and EntryCheck.NOT_ENGLISH not in e.checks:
            e.checks.append(EntryCheck.NOT_ENGLISH)
