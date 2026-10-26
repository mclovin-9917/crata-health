from app.pipeline.medicines import (
    GenericMatch,
    GenericMatches,
    Translation,
    check_entries,
    from_table,
    hindi_words,
    name_in_quote,
)
from app.schemas.record import Entry, EntryCheck, EntryKind, Medication, Provenance, SourceType
from app.views import fact_key


def _med(label: str, quote: str, **fields: str) -> Entry:
    return Entry(
        kind=EntryKind.MEDICATION, label=label, fields=dict(fields), medication=Medication(),
        provenance=[Provenance(source_id="s", source_type=SourceType.AUDIO, quote=quote)],
    )


class ScriptedAI:
    """Answers the two narrow questions from a script, and records them."""

    def __init__(self, generics: dict[str, GenericMatch] | None = None, translate=None):
        self.generics, self.translate, self.calls = generics or {}, translate, []

    def reason_json(self, system, user, schema, **kw):
        self.calls.append(schema.__name__)
        lines = user.splitlines()
        if schema is GenericMatches:
            return GenericMatches(items=[self.generics.get(n) or GenericMatch(
                name=n, status="unknown", generics=[]) for n in lines])
        return Translation(english=[self.translate(x) if self.translate else x for x in lines])


def test_brand_table_and_generic_names():
    assert from_table("Dolo 650") == ("paracetamol",)
    assert from_table("Tab Augmentin 625 mg") == ("amoxicillin", "clavulanic acid")
    assert from_table("Montair LC") == ("montelukast", "levocetirizine")  # not just Montair
    assert from_table("Septran DS") == ("sulfamethoxazole", "trimethoprim")
    assert from_table("Co-trimoxazole 960 mg") == ("sulfamethoxazole", "trimethoprim")
    assert from_table("Paracetamol 650 mg") == ("paracetamol",)
    assert from_table("Septr an DS") == ("sulfamethoxazole", "trimethoprim")  # misread space
    assert from_table("Augmentn 625") == ("amoxicillin", "clavulanic acid")  # small slip
    assert from_table("Pan 40") == ("pantoprazole",)
    assert from_table("Anti-malarial tablets") is None
    assert from_table("Zyx-Unknownol 20 mg") is None


def test_name_in_quote_allows_misspelling_but_not_a_different_word():
    assert name_in_quote(_med("Paracetamol 650 mg", "parasetamal chaiso pachas"))
    assert name_in_quote(_med("Septran DS", "2. Tab Septr an DS 1-0-1 x 5 days"))
    allergy = Entry(kind=EntryKind.ALLERGY, label="Sulfa drugs", provenance=[
        Provenance(source_id="s", source_type=SourceType.AUDIO, quote="salphad waks")])
    assert not name_in_quote(allergy)


def test_hindi_words_found_and_english_left_alone():
    assert hindi_words("raat ko ek tablet") == ["raat", "ko", "ek"]
    assert hindi_words("one tablet at night") == []
    assert hindi_words("do not take with milk") == []  # 'do' is English too


def test_vague_medicine_is_flagged_not_named():
    vague = _med("Anti-malarial tablets", "some tablets for anti-malarial tablets")
    ai = ScriptedAI({"Anti-malarial tablets": GenericMatch(
        name="Anti-malarial tablets", status="vague", generics=[])})
    check_entries([vague], ai)
    assert vague.checks == [EntryCheck.MEDICINE_UNNAMED]
    assert vague.medication.generic_names == []


def test_table_first_then_the_model_for_unknown_brands():
    known = _med("Dolo 650", "Tab Dolo 650 SOS")
    unlisted = _med("Zerodol SP", "Tab Zerodol SP twice a day")
    ai = ScriptedAI({"Zerodol SP": GenericMatch(
        name="Zerodol SP", status="specific",
        generics=["Aceclofenac", "Paracetamol", "Serratiopeptidase"])})
    check_entries([known, unlisted], ai)
    assert known.medication.generic_from == "table"
    assert unlisted.medication.generic_from == "model"
    assert unlisted.medication.generic_names == ["aceclofenac", "paracetamol", "serratiopeptidase"]
    assert ai.calls == ["GenericMatches"]  # one call, only for the name the table didn't know


def test_hindi_details_are_translated_once_and_flagged_if_still_hindi():
    good = _med("Montair LC", "montair LC raat ko ek tablet", frequency="raat ko ek tablet")
    check_entries([good], ScriptedAI(translate=lambda s: "one tablet at night"))
    assert good.fields["frequency"] == "one tablet at night"
    assert EntryCheck.NOT_ENGLISH not in good.checks

    stuck = _med("Montair LC", "montair LC raat ko ek tablet", frequency="raat ko ek tablet")
    check_entries([stuck], ScriptedAI())  # the translation came back unchanged
    assert EntryCheck.NOT_ENGLISH in stuck.checks


def test_brand_and_generic_are_one_fact_even_for_older_entries():
    dolo = _med("Dolo 650", "Tab Dolo 650")
    para = _med("Paracetamol 650 mg", "paracetamol 650")
    assert fact_key(dolo) == fact_key(para) == "medication:paracetamol"
    old = Entry(kind=EntryKind.MEDICATION, label="Augmentin 625 mg")  # no generic names stored
    assert fact_key(old) == "medication:amoxicillin+clavulanic acid"


def test_confirmed_entries_are_translated_and_keep_the_original():
    from app.pipeline.medicines import has_hindi, translate_confirmed

    e = _med("Montair LC", "Montair LC raat ko ek tablet", frequency="raat ko ek tablet",
             instructions="after food")
    ai = ScriptedAI(translate=lambda x: "one tablet at night" if "raat" in x else x)
    assert has_hindi(e) and translate_confirmed(e, ai)
    assert e.fields == {"frequency": "one tablet at night", "instructions": "after food"}
    (edit,) = e.edits
    assert edit.by == "system" and edit.old == "raat ko ek tablet"  # the original stays
    assert e.provenance[0].quote == "Montair LC raat ko ek tablet"  # quotes are never touched
    assert not translate_confirmed(e, ai)  # nothing left to translate


def test_a_translation_still_in_hindi_is_not_applied():
    from app.pipeline.medicines import translate_confirmed

    e = _med("Asthalin", "zaroorat pade toh do puff", frequency="zaroorat pade toh do puff")
    assert not translate_confirmed(e, ScriptedAI())  # the scripted "translation" echoes it
    assert e.edits == [] and e.fields["frequency"] == "zaroorat pade toh do puff"
