"""Drug families for the allergy check: which active ingredients belong together, and which
families are related (some people allergic to one react to the other).

A curated table, so the planted demo case and the common cases need no model call. The model
is asked only about names this table does not know, and it may answer only with these family
keys (`crosscheck.py`), so what counts as a family stays here, in code.

Ingredient names are lowercase generic names, as in `Medication.generic_names`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.pipeline.medicines import BRANDS, GENERICS, from_table


@dataclass(frozen=True)
class Family:
    key: str
    name: str  # plural, for "penicillins and cephalosporins are related"
    one: str  # one member, for "sulfamethoxazole, a sulfonamide antibiotic"
    ingredients: frozenset[str]
    words: tuple[str, ...] = ()  # allergy wording that names the whole family


def _f(key: str, name: str, one: str, ingredients: str, words: str = "") -> Family:
    return Family(key, name, one, frozenset(i.strip() for i in ingredients.split(",")),
                  tuple(w.strip() for w in words.split(",") if w.strip()))


FAMILIES: dict[str, Family] = {f.key: f for f in [
    _f("penicillins", "penicillins", "a penicillin",
       "penicillin, benzylpenicillin, phenoxymethylpenicillin, benzathine penicillin, amoxicillin, "
       "ampicillin, cloxacillin, dicloxacillin, flucloxacillin, piperacillin, ticarcillin",
       "penicillin, penicillins, pcn"),
    _f("cephalosporins", "cephalosporins", "a cephalosporin",
       "cefalexin, cephalexin, cefadroxil, cefazolin, cefuroxime, cefaclor, cefprozil, cefixime, "
       "cefpodoxime, cefdinir, ceftriaxone, cefotaxime, ceftazidime, cefoperazone, cefepime",
       "cephalosporin, cephalosporins"),
    _f("carbapenems", "carbapenems", "a carbapenem",
       "meropenem, imipenem, ertapenem, doripenem", "carbapenem, carbapenems"),
    _f("sulfonamide_antibiotics", "sulfonamide antibiotics", "a sulfonamide antibiotic",
       "sulfamethoxazole, sulfadiazine, silver sulfadiazine, sulfasalazine, sulfacetamide, "
       "sulfadoxine",
       "sulfa, sulpha, sulfas, sulfonamide, sulfonamides, sulphonamide, sulphonamides, "
       "co-trimoxazole, cotrimoxazole"),
    _f("macrolides", "macrolides", "a macrolide",
       "azithromycin, clarithromycin, erythromycin, roxithromycin", "macrolide, macrolides"),
    _f("fluoroquinolones", "fluoroquinolones", "a fluoroquinolone",
       "ciprofloxacin, levofloxacin, ofloxacin, norfloxacin, moxifloxacin",
       "quinolone, quinolones, fluoroquinolone, fluoroquinolones"),
    _f("tetracyclines", "tetracyclines", "a tetracycline",
       "tetracycline, doxycycline, minocycline", "tetracyclines"),
    _f("aminoglycosides", "aminoglycosides", "an aminoglycoside",
       "gentamicin, amikacin, tobramycin, streptomycin, neomycin", "aminoglycoside, aminoglycosides"),
    _f("lincosamides", "lincosamides", "a lincosamide", "clindamycin, lincomycin"),
    _f("glycopeptides", "glycopeptides", "a glycopeptide", "vancomycin, teicoplanin"),
    _f("nitroimidazoles", "nitroimidazoles", "a nitroimidazole",
       "metronidazole, tinidazole, ornidazole, secnidazole"),
    _f("rifamycins", "rifamycins", "a rifamycin", "rifampicin, rifampin, rifabutin, rifaximin"),
    _f("azole_antifungals", "azole antifungals", "an azole antifungal",
       "fluconazole, itraconazole, ketoconazole, voriconazole, posaconazole"),
    _f("aminoquinolines", "aminoquinoline antimalarials", "an aminoquinoline antimalarial",
       "chloroquine, hydroxychloroquine, amodiaquine"),
    _f("artemisinins", "artemisinin antimalarials", "an artemisinin antimalarial",
       "artemether, artesunate, arteether, dihydroartemisinin"),
    _f("nsaids", "NSAID painkillers", "an NSAID painkiller",
       "ibuprofen, diclofenac, aceclofenac, naproxen, aspirin, mefenamic acid, ketorolac, "
       "indomethacin, piroxicam, ketoprofen, nimesulide, etodolac",
       "nsaid, nsaids"),
    _f("coxibs", "COX-2 painkillers", "a COX-2 painkiller", "celecoxib, etoricoxib"),
    _f("opioids", "opioid painkillers", "an opioid",
       "morphine, codeine, tramadol, tapentadol, oxycodone, hydrocodone, fentanyl, pethidine",
       "opioid, opioids, opiate, opiates"),
    _f("amide_anaesthetics", "local anaesthetics (amide type)", "a local anaesthetic",
       "lidocaine, lignocaine, bupivacaine, levobupivacaine, ropivacaine, prilocaine",
       "local anaesthetic, local anesthetic, local anaesthetics, local anesthetics"),
    _f("ester_anaesthetics", "local anaesthetics (ester type)", "a local anaesthetic",
       "procaine, benzocaine, tetracaine, chloroprocaine"),
    _f("ace_inhibitors", "ACE inhibitors", "an ACE inhibitor",
       "enalapril, ramipril, lisinopril, perindopril, captopril", "ace inhibitor, ace inhibitors"),
    _f("statins", "statins", "a statin",
       "atorvastatin, rosuvastatin, simvastatin, pravastatin", "statin, statins"),
    _f("aromatic_anticonvulsants", "aromatic anti-seizure medicines", "an aromatic anti-seizure medicine",
       "carbamazepine, oxcarbazepine, phenytoin, phenobarbital, lamotrigine"),
    _f("benzodiazepines", "benzodiazepines", "a benzodiazepine",
       "diazepam, lorazepam, alprazolam, clonazepam, midazolam, chlordiazepoxide",
       "benzodiazepine, benzodiazepines"),
    _f("ppis", "proton pump inhibitors", "a proton pump inhibitor",
       "omeprazole, esomeprazole, pantoprazole, rabeprazole, lansoprazole"),
    _f("piperazine_antihistamines", "cetirizine-type antihistamines", "a cetirizine-type antihistamine",
       "cetirizine, levocetirizine, hydroxyzine"),
    _f("loop_diuretics", "loop diuretics", "a loop diuretic", "furosemide, torsemide, bumetanide"),
    _f("thiazides", "thiazide diuretics", "a thiazide diuretic",
       "hydrochlorothiazide, chlorthalidone, indapamide"),
    _f("sulfonylureas", "sulfonylureas", "a sulfonylurea",
       "glibenclamide, glimepiride, gliclazide, glipizide"),
    _f("heparins", "heparins", "a heparin", "heparin, enoxaparin, dalteparin"),
    _f("iodinated_contrast", "iodine contrast dyes", "an iodine contrast dye",
       "iohexol, iopamidol, iodixanol, iopromide",
       "contrast, contrast dye, iodine contrast, iodinated contrast, iv contrast"),
]}

# Families where some people allergic to one react to the other: an amber "worth asking",
# never a red clash. Read both ways.
RELATED: frozenset[frozenset[str]] = frozenset(frozenset(p) for p in [
    ("penicillins", "cephalosporins"),
    ("penicillins", "carbapenems"),
    ("cephalosporins", "carbapenems"),
    ("sulfonamide_antibiotics", "loop_diuretics"),
    ("sulfonamide_antibiotics", "thiazides"),
    ("sulfonamide_antibiotics", "sulfonylureas"),
    ("nsaids", "coxibs"),
])

# Allergies that are not to a medicine: nothing to compare, no model call.
NOT_MEDICINES: frozenset[str] = frozenset({
    "dust", "dust mite", "dust mites", "pollen", "grass", "mould", "mold", "pet", "cat", "cats",
    "dog", "dogs", "animal", "bee", "bees", "wasp", "insect", "latex", "nickel", "peanut", "peanuts",
    "nut", "nuts", "tree nut", "tree nuts", "shellfish", "prawn", "prawns", "shrimp", "fish",
    "seafood", "egg", "eggs", "milk", "dairy", "lactose", "gluten", "wheat", "soy", "soya", "sesame",
    "brinjal", "food", "smoke", "perfume", "cold", "sun", "sunlight",
})

_FAMILY_OF: dict[str, set[str]] = {}
for _fam in FAMILIES.values():
    for _i in _fam.ingredients:
        _FAMILY_OF.setdefault(_i, set()).add(_fam.key)

# Every ingredient the tables know, with or without a family: an ingredient outside this set
# goes to the model.
KNOWN: frozenset[str] = frozenset(_FAMILY_OF) | GENERICS | {g for gs in BRANDS.values() for g in gs}

# Words that say "allergy" rather than what to: left out before matching.
_FILLER = {"allergy", "allergies", "allergic", "to", "drug", "drugs", "medicine", "medicines",
           "group", "class", "known", "reaction", "and", "or", "the", "a", "an", "of", "type"}


def families_of(ingredient: str) -> set[str]:
    return set(_FAMILY_OF.get(ingredient.casefold().strip(), ()))


def related(a: str, b: str) -> bool:
    return frozenset((a, b)) in RELATED


def _clean(label: str) -> str:
    words = re.sub(r"[^a-z0-9\s-]", " ", label.casefold()).split()
    return " ".join(w for w in words if w not in _FILLER)


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text) is not None


@dataclass(frozen=True)
class AllergyTarget:
    """What an allergy is to: whole families and/or particular ingredients. Empty both ways
    with `medicine` False for 'dust' or 'peanuts'. `None` from `allergy_target` means the
    table cannot tell (the model is asked)."""

    families: frozenset[str] = frozenset()
    ingredients: frozenset[str] = frozenset()
    medicine: bool = True


def allergy_target(label: str) -> AllergyTarget | None:
    """Read an allergy label with the tables. 'Sulfa drugs' is the sulfonamide family,
    'Septran' is its ingredients, 'Peanuts' is not a medicine; None when unknown."""
    text = _clean(label)
    if not text:
        return None
    fams = {f.key for f in FAMILIES.values() if any(_has(text, w) for w in f.words)}
    ingredients = set(from_table(text) or ())
    ingredients |= {i for i in _FAMILY_OF if _has(text, i)}
    if fams or ingredients:
        return AllergyTarget(frozenset(fams), frozenset(ingredients))
    if text in NOT_MEDICINES or all(w in NOT_MEDICINES for w in text.split()):
        return AllergyTarget(medicine=False)
    return None


def ingredients_in(label: str) -> tuple[str, ...]:
    """Ingredients named in a medicine label: the brand table, then family ingredients
    written out ('Cefixime 200 mg')."""
    found = list(from_table(label) or ())
    text = label.casefold()
    for i in sorted(_FAMILY_OF, key=len, reverse=True):  # 'silver sulfadiazine' first
        if _has(text, i) and not any(i in f for f in found):
            found.append(i)
    return tuple(found)
