from datetime import date

import pytest

from app.ai.client import AIError
from app.pipeline.crosscheck import (
    FamilyAnswer,
    FamilyAnswers,
    allergy_clashes,
    billed_not_prescribed,
    for_review,
    still_open,
)
from app.pipeline.families import FAMILIES, RELATED, allergy_target, ingredients_in
from app.schemas.record import (
    BillLine,
    DocumentKind,
    Encounter,
    Entry,
    EntryCheck,
    EntryKind,
    EntryStatus,
    Medication,
    Provenance,
    SourceItem,
    SourceType,
)


class FamilyAI:
    """Answers the family question from a script; records the names it was asked about."""

    def __init__(self, answers=(), fail=False):
        self.answers, self.fail, self.asked = list(answers), fail, []

    def reason_json(self, system, user, schema, **kw):
        assert schema is FamilyAnswers
        self.asked += user.splitlines()
        if self.fail:
            raise AIError("endpoint busy")
        return FamilyAnswers(items=self.answers)


def _prov(quote, kind=SourceType.IMAGE):
    return Provenance(source_id="s1", source_type=kind, quote=quote)


def med(label, generics=(), status=EntryStatus.PROPOSED, generic_from="table", checks=()):
    e = Entry(kind=EntryKind.MEDICATION, label=label, provenance=[_prov(label)],
              medication=Medication(generic_names=list(generics),
                                    generic_from=generic_from if generics else None),
              checks=list(checks))
    if status != EntryStatus.PROPOSED:
        e.set_status(status, by="p1")
    return e


def allergy(label, reaction=None, status=EntryStatus.CONFIRMED, manual=True):
    prov = (Provenance(source_id="manual", source_type=SourceType.MANUAL) if manual
            else _prov(f"Known allergy: {label}"))
    e = Entry(kind=EntryKind.ALLERGY, label=label, provenance=[prov],
              fields={"reaction": reaction} if reaction else {})
    if status != EntryStatus.PROPOSED:
        e.set_status(status, by="p1")
    return e


def bill(name, generics=(), is_drug=True):
    return BillLine(name=name, quantity=10, is_drug=is_drug, generic_names=list(generics),
                    generic_from="table" if generics else None, provenance=_prov(f"{name} 10"))


SEPTRAN = ("sulfamethoxazole", "trimethoprim")


# ---- the tables --------------------------------------------------------------------------------
def test_the_table_has_about_thirty_families_and_related_pairs_point_at_real_ones():
    assert 25 <= len(FAMILIES) <= 40
    assert all(k in FAMILIES for pair in RELATED for k in pair)


@pytest.mark.parametrize(("label", "families", "ingredients"), [
    ("Sulfa drugs", {"sulfonamide_antibiotics"}, set()),
    ("Sulpha allergy", {"sulfonamide_antibiotics"}, set()),
    ("Known allergy: Sulfa drugs", {"sulfonamide_antibiotics"}, set()),
    ("Penicillin", {"penicillins"}, {"penicillin"}),
    ("Septran", set(), set(SEPTRAN)),
    ("Amoxicillin", set(), {"amoxicillin"}),
    ("NSAIDs", {"nsaids"}, set()),
])
def test_allergy_labels_are_read_with_the_tables(label, families, ingredients):
    t = allergy_target(label)
    assert t is not None and t.medicine
    assert set(t.families) == families and set(t.ingredients) == ingredients


def test_food_and_pollen_are_not_medicines_and_unknown_names_are_left_to_the_model():
    assert allergy_target("Peanuts").medicine is False
    assert allergy_target("Dust mites").medicine is False
    assert allergy_target("Ceftum") is None
    assert allergy_target("allergy") is None


def test_ingredients_written_out_in_a_label_are_found():
    assert ingredients_in("Cefixime 200 mg") == ("cefixime",)
    assert ingredients_in("Septran DS") == SEPTRAN
    assert ingredients_in("Silver sulfadiazine cream") == ("silver sulfadiazine",)


# ---- red: same ingredient or family ------------------------------------------------------------
def test_planted_case_septran_against_a_sulfa_allergy_is_red_without_a_model_call():
    ai = FamilyAI()
    rx = med("Septran DS", SEPTRAN)
    sulfa = allergy("Sulfa drugs", "hives")
    (clash,) = allergy_clashes([rx], [], [sulfa], ai)
    assert clash.check == "allergy_clash" and clash.severity == "urgent"
    assert clash.message == (
        "Septran DS contains sulfamethoxazole, a sulfonamide antibiotic. Your record lists an "
        "allergy to Sulfa drugs (hives). Please show this to your doctor or pharmacist.")
    assert clash.entry_ids == [rx.id, sulfa.id] and clash.matched_by == "table"
    assert ai.asked == []


def test_the_same_medicine_on_the_prescription_and_the_bill_is_one_clash():
    rx = med("Septran DS", SEPTRAN)
    line = bill("Septran DS Tab", SEPTRAN)
    (clash,) = allergy_clashes([rx], [line], [allergy("Sulfa drugs")], FamilyAI())
    assert clash.bill_line_ids == [line.id]
    assert [p.quote for p in clash.provenance] == ["Septran DS", "Septran DS Tab 10"]
    assert clash.message.startswith("Septran DS contains")


def test_a_clash_only_on_the_bill_says_so():
    (clash,) = allergy_clashes([], [bill("Septran DS Tab", SEPTRAN)], [allergy("Sulfa drugs")],
                               FamilyAI())
    assert clash.message.startswith(
        "The bill lists Septran DS Tab, which contains sulfamethoxazole, a sulfonamide antibiotic.")
    assert clash.entry_ids == [clash.entry_ids[0]]  # the allergy only


def test_same_ingredient_and_same_family_are_red():
    (by_ingredient,) = allergy_clashes([med("Augmentin 625", ("amoxicillin", "clavulanic acid"))],
                                       [], [allergy("Amoxicillin")], FamilyAI())
    assert by_ingredient.severity == "urgent"
    assert by_ingredient.message.startswith("Augmentin 625 contains amoxicillin, a penicillin.")

    (by_family,) = allergy_clashes([med("Ampicillin 500 mg", ("ampicillin",))], [],
                                   [allergy("Amoxicillin")], FamilyAI())
    assert by_family.severity == "urgent"
    assert by_family.message.startswith("Ampicillin 500 mg is a penicillin.")


def test_an_allergy_suggested_in_this_visit_counts_and_says_so():
    sulfa = allergy("Sulfa drugs", status=EntryStatus.PROPOSED, manual=False)
    (clash,) = allergy_clashes([med("Septran DS", SEPTRAN), sulfa], [], [], FamilyAI())
    assert "This visit mentions an allergy to Sulfa drugs." in clash.message
    assert [p.quote for p in clash.provenance][-1] == "Known allergy: Sulfa drugs"


def test_the_record_allergy_is_named_before_the_same_one_in_this_visit():
    in_visit = allergy("Sulfa drugs", status=EntryStatus.PROPOSED, manual=False)
    record = allergy("Sulfa drugs", "hives")
    (clash,) = allergy_clashes([med("Septran DS", SEPTRAN), in_visit], [], [record], FamilyAI())
    assert "Your record lists an allergy to Sulfa drugs (hives)." in clash.message
    assert set(clash.entry_ids) >= {record.id, in_visit.id}


# ---- amber: related families -------------------------------------------------------------------
def test_a_cephalosporin_against_a_penicillin_allergy_is_amber():
    (clash,) = allergy_clashes([med("Cefixime 200 mg", ("cefixime",))], [],
                               [allergy("Penicillin", "rash")], FamilyAI())
    assert clash.severity == "review"
    assert clash.message == (
        "Cefixime 200 mg is a cephalosporin. Your record lists an allergy to Penicillin (rash). "
        "Penicillins and cephalosporins are related, and some people react to both. Worth "
        "asking your doctor or pharmacist about it.")


def test_red_clashes_come_before_amber_ones():
    found = allergy_clashes(
        [med("Cefixime 200 mg", ("cefixime",)), med("Augmentin", ("amoxicillin", "clavulanic acid"))],
        [], [allergy("Penicillin")], FamilyAI())
    assert [x.severity for x in found] == ["urgent", "review"]


# ---- no clash ----------------------------------------------------------------------------------
@pytest.mark.parametrize(("medicines", "allergies"), [
    ([med("Azithromycin 500 mg", ("azithromycin",))], [allergy("Penicillin")]),
    ([med("Dolo 650", ("paracetamol",))], [allergy("Sulfa drugs")]),
    ([med("Septran DS", SEPTRAN)], [allergy("Peanuts")]),
    ([med("Septran DS", SEPTRAN)], [allergy("Sulfa drugs", status=EntryStatus.REJECTED)]),
    ([med("Septran DS", SEPTRAN, status=EntryStatus.REJECTED)], [allergy("Sulfa drugs")]),
    ([med("Anti-malarial tablets", checks=[EntryCheck.MEDICINE_UNNAMED])], [allergy("Chloroquine")]),
    ([], [allergy("Sulfa drugs")]),
])
def test_negatives(medicines, allergies):
    ai = FamilyAI()
    assert allergy_clashes(medicines, [], allergies, ai) == []
    assert ai.asked == []


def test_non_drug_bill_lines_and_food_allergies_never_reach_the_model():
    ai = FamilyAI()
    assert allergy_clashes([], [bill("Syringe 5 ml", is_drug=False)], [allergy("Sulfa drugs")], ai) == []
    assert allergy_clashes([med("Unknownex 10")], [], [allergy("Peanuts")], ai) == []
    assert ai.asked == []


# ---- the model, for names the tables do not know -----------------------------------------------
def test_an_unknown_allergy_name_is_read_by_the_model_and_marked():
    ai = FamilyAI([FamilyAnswer(name="Ceftum", kind="medicine", ingredients=["Cefuroxime"],
                                families=["cephalosporins", "made_up_family"])])
    (clash,) = allergy_clashes([med("Cefixime 200 mg", ("cefixime",))], [], [allergy("Ceftum")], ai)
    assert ai.asked == ["Ceftum"]
    assert clash.severity == "urgent" and clash.matched_by == "model"


def test_an_unknown_ingredient_gets_its_family_from_the_model():
    ai = FamilyAI([FamilyAnswer(name="cefditoren", kind="medicine", ingredients=["cefditoren"],
                                families=["cephalosporins"])])
    (clash,) = allergy_clashes([med("Spectracef", ("cefditoren",), generic_from="model")], [],
                               [allergy("Penicillin")], ai)
    assert ai.asked == ["cefditoren"]
    assert clash.severity == "review" and clash.matched_by == "model"


def test_a_medicine_with_no_known_ingredients_is_asked_about_by_name():
    ai = FamilyAI([FamilyAnswer(name="Xylotrim", kind="medicine",
                                ingredients=["sulfamethoxazole"], families=[])])
    (clash,) = allergy_clashes([med("Xylotrim")], [], [allergy("Sulfa drugs")], ai)
    assert ai.asked == ["Xylotrim"]
    assert clash.severity == "urgent" and clash.matched_by == "model"


def test_a_failing_model_leaves_the_table_matches():
    ai = FamilyAI(fail=True)
    found = allergy_clashes([med("Septran DS", SEPTRAN), med("Unknownex")], [],
                            [allergy("Sulfa drugs"), allergy("Ceftum")], ai)
    assert [x.severity for x in found] == ["urgent"]


# ---- what Review shows after the patient's review ----------------------------------------------
def test_a_clash_goes_away_when_its_allergy_or_its_medicine_is_rejected():
    rx = med("Septran DS", SEPTRAN)
    sulfa = allergy("Sulfa drugs", status=EntryStatus.PROPOSED, manual=False)
    (clash,) = allergy_clashes([rx, sulfa], [], [], FamilyAI())
    lookup = {e.id: e for e in (rx, sulfa)}.get
    assert still_open([clash], lookup) == [clash]

    sulfa.set_status(EntryStatus.REJECTED, by="p1")
    assert still_open([clash], lookup) == []
    sulfa.set_status(EntryStatus.CONFIRMED, by="p1")
    rx.set_status(EntryStatus.REJECTED, by="p1")
    assert still_open([clash], lookup) == []


def test_a_rejected_medicine_that_was_also_billed_keeps_its_clash():
    rx = med("Septran DS", SEPTRAN)
    sulfa = allergy("Sulfa drugs")
    (clash,) = allergy_clashes([rx], [bill("Septran DS Tab", SEPTRAN)], [sulfa], FamilyAI())
    rx.set_status(EntryStatus.REJECTED, by="p1")
    assert still_open([clash], {e.id: e for e in (rx, sulfa)}.get) == [clash]


# ---- billed but not prescribed -----------------------------------------------------------------
TODAY = date(2026, 10, 13)
RX_SOURCE = SourceItem(source_type=SourceType.IMAGE, text="Rx", document_kind=DocumentKind.PRESCRIPTION)
BILL_SOURCE = SourceItem(source_type=SourceType.IMAGE, text="Bill", document_kind=DocumentKind.BILL)
PANTOCID = ("pantoprazole",)
AUGMENTIN = ("amoxicillin", "clavulanic acid")


def billed(entries, lines, record=(), sources=(RX_SOURCE, BILL_SOURCE)):
    return billed_not_prescribed(entries, lines, list(sources), list(record), TODAY)


def record_med(label, generics, **medication):
    e = med(label, generics, status=EntryStatus.CONFIRMED)
    e.medication = e.medication.model_copy(update=medication)
    return e


def test_planted_case_pantocid_billed_never_prescribed():
    (flag,) = billed([med("Septran DS", SEPTRAN)],
                     [bill("Septran DS Tab", SEPTRAN), bill("Pantocid 40 Tab", PANTOCID),
                      bill("Syringe 5 ml", is_drug=False)])
    assert flag.check == "billed_not_prescribed" and flag.severity == "review"
    assert flag.message == (
        "Pantocid 40 Tab (pantoprazole) is on the bill, but not on the prescription or in the "
        "consult. Worth checking with the pharmacy or your doctor.")
    assert flag.entry_ids == [] and len(flag.bill_line_ids) == 1
    assert [p.quote for p in flag.provenance] == ["Pantocid 40 Tab 10"]


def test_the_same_ingredients_at_another_strength_count_as_prescribed():
    assert billed([med("Pantoprazole 20 mg", PANTOCID)], [bill("Pantocid 40 Tab", PANTOCID)]) == []


def test_a_current_medicine_in_the_record_counts_as_prescribed_but_an_ended_one_does_not():
    line = [bill("Pantocid 40 Tab", PANTOCID)]
    ongoing = record_med("Pantoprazole 40 mg", PANTOCID, ongoing=True)
    no_end = record_med("Pan 40", PANTOCID)
    ended = record_med("Pan 40", PANTOCID, started_on=date(2026, 1, 1), duration_days=5)
    stopped = record_med("Pan 40", PANTOCID, ongoing=True, stopped_on=date(2026, 9, 1))
    assert billed([], line, [ongoing]) == []
    assert billed([], line, [no_end]) == []  # "duration not known" stays current
    assert len(billed([], line, [ended])) == 1
    assert len(billed([], line, [stopped])) == 1


def test_a_rejected_suggestion_does_not_count_as_prescribed_and_the_flag_says_why():
    rejected = med("Pantocid 40", PANTOCID, status=EntryStatus.REJECTED)
    (flag,) = billed([rejected], [bill("Pantocid 40 Tab", PANTOCID)])
    assert flag.message == ("Pantocid 40 Tab (pantoprazole) is on the bill. You chose not to add "
                            "Pantocid 40 from this visit, so it is not in your record.")
    assert flag.entry_ids == [rejected.id]


def test_in_a_bill_only_visit_rejecting_the_suggestion_settles_its_clash():
    line = bill("Amoxicillin 500 Cap", ("amoxicillin",))
    suggestion = med("Amoxicillin 500 Cap", ("amoxicillin",))
    penicillin = allergy("Penicillin")
    enc = Encounter(patient_id="p1", sources=[BILL_SOURCE], entries=[suggestion],
                    bill_lines=[line])
    enc.discrepancies = allergy_clashes(enc.entries, enc.bill_lines, [penicillin], FamilyAI())
    lookup = {e.id: e for e in (suggestion, penicillin)}.get
    assert len(for_review(enc, [], lookup, TODAY)) == 1
    suggestion.set_status(EntryStatus.REJECTED, by="p1")  # bought for someone else
    assert for_review(enc, [], lookup, TODAY) == []


def test_a_bill_only_suggestion_and_its_bill_line_quote_once():
    line = bill("Septran DS Tab", SEPTRAN)
    suggestion = med("Septran DS Tab", SEPTRAN)
    suggestion.provenance = [line.provenance]
    (clash,) = allergy_clashes([suggestion], [line], [allergy("Sulfa drugs")], FamilyAI())
    assert [p.quote for p in clash.provenance] == ["Septran DS Tab 10"]


def test_a_different_mix_has_its_own_wording():
    rx = med("Amoxicillin 500 mg", ("amoxicillin",))
    (flag,) = billed([rx], [bill("Augmentin 625 Duo Tab", AUGMENTIN)])
    assert flag.message == (
        "The bill has Augmentin 625 Duo Tab (amoxicillin + clavulanic acid); the prescription has "
        "Amoxicillin 500 mg. Worth checking it is the medicine meant.")
    assert flag.entry_ids == [rx.id]


def test_a_medicine_said_in_the_consult_counts_and_is_named_as_such():
    said = med("Amoxicillin", ("amoxicillin",))
    said.provenance = [Provenance(source_id="a1", source_type=SourceType.AUDIO, quote="amoxicillin")]
    (flag,) = billed([said], [bill("Augmentin 625 Duo Tab", AUGMENTIN)])
    assert "; the consult has Amoxicillin." in flag.message


def test_a_line_nobody_could_name_is_compared_by_name():
    rx = med("Zyloric 100")
    assert billed([rx], [bill("Zyloric 100 Tab")]) == []
    (flag,) = billed([rx], [bill("Mystery Tonic")])
    assert flag.message.startswith("Mystery Tonic is on the bill")


def test_a_model_matched_line_says_so():
    line = bill("Zyloric 100 Tab", ("allopurinol",))
    line.generic_from = "model"
    (flag,) = billed([], [line])
    assert flag.matched_by == "model"


def test_a_visit_that_is_only_a_bill_is_not_checked():
    assert billed([], [bill("Pantocid 40 Tab", PANTOCID)], sources=[BILL_SOURCE]) == []


def test_review_shows_red_then_amber_then_the_bill_and_follows_rejections():
    rx = med("Pantocid 40", PANTOCID)
    cefixime = med("Cefixime 200 mg", ("cefixime",))
    augmentin = med("Augmentin", AUGMENTIN)
    penicillin = allergy("Penicillin")
    enc = Encounter(patient_id="p1", sources=[RX_SOURCE, BILL_SOURCE],
                    entries=[rx, cefixime, augmentin],
                    bill_lines=[bill("Pantocid 40 Tab", PANTOCID)])
    enc.discrepancies = allergy_clashes(enc.entries, enc.bill_lines, [penicillin], FamilyAI())
    lookup = {e.id: e for e in (rx, cefixime, augmentin, penicillin)}.get
    assert [d.severity for d in for_review(enc, [], lookup, TODAY)] == ["urgent", "review"]

    rx.set_status(EntryStatus.REJECTED, by="p1")  # Pantocid was not prescribed after all
    found = for_review(enc, [], lookup, TODAY)
    assert [(d.check, d.severity) for d in found] == [
        ("allergy_clash", "urgent"), ("allergy_clash", "review"),
        ("billed_not_prescribed", "review")]
