from datetime import date

from app.pipeline.bills import (
    DocumentSort,
    ReadBillLine,
    SortedDocument,
    apply_kinds,
    bill_date,
    bill_only,
    extraction_text,
    is_drug,
    number_in,
    parts_of,
    to_bill_lines,
)
from app.pipeline.extract import ExtractedItem
from app.pipeline.medicines import GenericMatch
from app.pipeline.run import Media, process_encounter
from app.schemas.record import (
    DocumentKind,
    Encounter,
    EncounterStatus,
    EntryKind,
    EntryStatus,
    SourceItem,
    SourceType,
    VisitDateSource,
)
from app.storage import Storage
from tests.test_medicines import ScriptedAI
from tests.test_pipeline import FakeAI

# A synthetic bill reading, laid out the way the document reader returns tables.
BILL = """Wellness Pharmacy
Bill No: SYN-4471     Date: 12/10/2026
Item | Qty | Rate | Amount
Augmentin 625 Duo Tab | 10 | 22.40 | 224.00
Septran DS Tab | 10 | 3.10 | 31.00
Pantocid 40 Tab | 10 | 14.50 | 145.00
Zyloric 100 Tab | 30 | 1.20 | 36.00
Syringe 5 ml | 2 | 8.00 | 16.00
Total 452.00"""
RX = "Rx\n1. Tab Augmentin 625 mg  1-0-1 x 5 days\n2. Tab Septran DS  1-0-1 x 5 days"


def _line(name, quote, quantity=None, item_type="medicine", **kw):
    return ReadBillLine(name=name, quote=quote, quantity=quantity, item_type=item_type, **kw)


BILL_LINES = [
    _line("Augmentin 625 Duo Tab", "Augmentin 625 Duo Tab | 10", 10),
    _line("Septran DS Tab", "Septran DS Tab | 10", 10),
    _line("Pantocid 40 Tab", "Pantocid 40 Tab | 10", 10),
    _line("Zyloric 100 Tab", "Zyloric 100 Tab | 30", 30),  # not in the brand table
    _line("Syringe 5 ml", "Syringe 5 ml | 2", 2, item_type="medicine"),  # model got it wrong
    _line("Crocin 500 Tab", "Crocin 500 Tab | 15", 15),  # not on the bill at all
    _line("Septran DS Tab", "Septran DS Tab | 10", 20),  # quantity not in the quote
]


def _sources():
    return parts_of([SourceItem(source_type=SourceType.IMAGE, text=RX),
                     SourceItem(source_type=SourceType.IMAGE, text=BILL)])


def _sort(lines=None, bill_on="2026-10-12"):
    return DocumentSort(documents=[
        SortedDocument(source_id="doc1", kind="prescription"),
        SortedDocument(source_id="doc2", kind="bill", date=bill_on, lines=lines or BILL_LINES),
    ])


def test_bill_lines_are_checked_against_the_reading():
    parts = _sources()
    zyloric = GenericMatch(name="Zyloric 100 Tab", status="specific", generics=["Allopurinol"])
    ai = ScriptedAI({"Zyloric 100 Tab": zyloric})
    apply_kinds(_sort(), parts)
    lines, failed = to_bill_lines(_sort(), parts, ai)

    assert [(b.name, b.quantity, b.is_drug) for b in lines] == [
        ("Augmentin 625 Duo Tab", 10, True),
        ("Septran DS Tab", 10, True),
        ("Pantocid 40 Tab", 10, True),
        ("Zyloric 100 Tab", 30, True),
        ("Syringe 5 ml", 2, False),  # code overrides the model: a syringe is not a drug
        ("Septran DS Tab", None, True),  # 20 is not in its quote, so no quantity
    ]
    assert failed == ["bill line Crocin 500 Tab"]
    assert lines[2].generic_names == ["pantoprazole"] and lines[2].generic_from == "table"
    assert lines[3].generic_names == ["allopurinol"] and lines[3].generic_from == "model"
    assert lines[4].generic_names == []  # non-drug lines are not looked up
    assert ai.calls == ["GenericMatches"]  # one call, for the brand the table doesn't know
    assert all(b.provenance.source_id == parts["doc2"].source.id for b in lines)
    assert all(b.provenance.locator is None for b in lines)  # a photo has no pages
    assert parts["doc1"].source.document_kind == DocumentKind.PRESCRIPTION
    assert parts["doc2"].source.document_kind == DocumentKind.BILL
    assert extraction_text(parts["doc1"].source) == RX
    assert extraction_text(parts["doc2"].source) is None


def test_a_shortened_quote_is_replaced_by_the_bills_own_line():
    shortened = [_line("Zyloric 100 Tab", "Zyloric Tab 30", 30),  # seen live: a word left out
                 _line("Tab", "Tab x 10", 10)]  # on several lines: too vague to place
    lines, failed = to_bill_lines(_sort(shortened), _sources(), ScriptedAI())
    assert [(b.name, b.quantity, b.provenance.quote) for b in lines] == [
        ("Zyloric 100 Tab", 30, "Zyloric 100 Tab | 30 | 1.20 | 36.00")]
    assert failed == ["bill line Tab"]


def test_no_model_call_when_the_table_knows_every_name():
    ai = ScriptedAI()
    to_bill_lines(_sort(BILL_LINES[:3]), _sources(), ai)
    assert ai.calls == []


def test_a_pdf_is_sorted_page_by_page():
    pdf = SourceItem(source_type=SourceType.IMAGE, pages=3, page_texts=[RX, BILL, "Page 3 notes"],
                     text=f"{RX}\n\n{BILL}\n\nPage 3 notes")
    photo = SourceItem(source_type=SourceType.IMAGE, text="Lab report")
    parts = parts_of([photo, pdf])
    assert list(parts) == ["doc1", "doc2-page1", "doc2-page2", "doc2-page3"]

    sort = DocumentSort(documents=[  # the model skipped page 3
        SortedDocument(source_id="doc1", kind="report"),
        SortedDocument(source_id="doc2-page1", kind="prescription"),
        SortedDocument(source_id="doc2-page2", kind="bill", lines=BILL_LINES[2:3]),
    ])
    apply_kinds(sort, parts)
    assert pdf.page_kinds == ["prescription", "bill", "other"]
    assert pdf.document_kind == DocumentKind.PRESCRIPTION  # not a bill: it still gets read
    assert extraction_text(pdf) == f"{RX}\n\nPage 3 notes"  # without the bill page
    (line,), _ = to_bill_lines(sort, parts, ScriptedAI())
    assert line.name == "Pantocid 40 Tab" and line.provenance.locator == "page 2"


def test_what_counts_as_a_drug():
    assert not is_drug(_line("Disposable Gloves", "x"))
    assert not is_drug(_line("Consultation fee", "x"))
    assert not is_drug(_line("Accu-Chek test strips", "x"))
    assert is_drug(_line("Dolo 650 strip", "x", item_type="non_drug"))  # the table wins
    assert is_drug(_line("Zyloric 100", "x"))
    assert not is_drug(_line("Hot water bag", "x", item_type="non_drug"))


def test_numbers_in_quotes():
    assert number_in("Pantocid 40 Tab | 10 | 14.50", 10)
    assert number_in("Syrup 1.5 bottles", 1.5)
    assert not number_in("Pantocid 40 Tab | 10", 1)
    assert not number_in("Pantocid 40 Tab | 10", 4)


def test_bill_date_skips_bad_and_future_dates():
    today = date(2026, 10, 12)
    assert bill_date(_sort(), today) == date(2026, 10, 12)
    assert bill_date(_sort(bill_on="2026-12-01"), today) is None
    assert bill_date(_sort(bill_on="2026-02-30"), today) is None
    assert bill_date(_sort(bill_on=None), today) is None


# ---- in the pipeline -------------------------------------------------------------------------
def _visit(docs, items):
    db = Storage()
    enc = Encounter(patient_id="p1", sources=[
        SourceItem(source_type=SourceType.IMAGE, filename="rx.jpg"),
        SourceItem(source_type=SourceType.IMAGE, filename="bill.jpg"),
    ])
    db.save_encounter(enc)
    media = [Media(s.id, "image", b"img", "image/jpeg") for s in enc.sources]

    class TwoDocsAI(FakeAI):
        def perceive(self, parts, instruction, **kw):
            self.calls.append(("perceive", "image_url", instruction))
            return RX if len([c for c in self.calls if c[0] == "perceive"]) == 1 else BILL

    ai = TwoDocsAI(items, docs=docs)
    process_encounter(db, ai, enc.id, media)
    return db.get_encounter(enc.id), ai


def test_bills_are_kept_out_of_the_record_extraction():
    rx_item = ExtractedItem(kind=EntryKind.MEDICATION, label="Septran DS", source_id="doc1",
                            quote="Tab Septran DS", said_by="document", confidence=0.9)
    done, ai = _visit(_sort(BILL_LINES[:3]).documents, [rx_item])

    assert done.status == EncounterStatus.READY
    assert [e.label for e in done.entries] == ["Septran DS"]
    assert [b.name for b in done.bill_lines] == [
        "Augmentin 625 Duo Tab", "Septran DS Tab", "Pantocid 40 Tab"]
    extraction_input = next(c[2] for c in ai.calls if c[:2] == ("reason_json", "Extraction"))
    assert "Pantocid" not in extraction_input and "Augmentin 625 mg" in extraction_input
    assert done.visit_date.isoformat() == "2026-10-12"
    assert done.visit_date_source == VisitDateSource.DOCUMENT  # printed on the bill
    assert "sorting_documents" in done.step_seconds


def test_a_visit_with_only_a_bill_suggests_its_medicines_without_extraction():
    docs = [SortedDocument(source_id="doc1", kind="bill", lines=BILL_LINES[:1]),
            SortedDocument(source_id="doc2", kind="bill", lines=[BILL_LINES[2], BILL_LINES[4]])]
    done, ai = _visit(docs, [])
    assert done.status == EncounterStatus.READY
    assert len(done.bill_lines) == 2  # doc1 is RX, not this bill line
    assert done.quote_failures == ["bill line Augmentin 625 Duo Tab"]
    assert not any(c[:2] == ("reason_json", "Extraction") for c in ai.calls)

    # The medicine is a suggestion for the patient to confirm (it may be someone else's); the
    # syringe is not. Nothing to compare the bill with, so no "billed, not prescribed" flag.
    (pantocid,) = done.entries
    assert pantocid.label == "Pantocid 40 Tab" and pantocid.status == EntryStatus.PROPOSED
    assert pantocid.medication.generic_names == ["pantoprazole"]
    assert pantocid.fields == {"bought": "10"} and pantocid.said_by == "document"
    assert pantocid.provenance[0].quote == "Pantocid 40 Tab | 10"
    assert pantocid.medication.started_on == done.visit_date
    assert done.discrepancies == []


def test_billed_but_not_prescribed_in_a_visit_with_a_prescription():
    rx_item = ExtractedItem(kind=EntryKind.MEDICATION, label="Septran DS", source_id="doc1",
                            quote="Tab Septran DS", said_by="document", confidence=0.9)
    done, _ = _visit(_sort(BILL_LINES[:3]).documents, [rx_item])
    assert [d.message.split(" is on")[0] for d in done.discrepancies] == [
        "Augmentin 625 Duo Tab (amoxicillin + clavulanic acid)", "Pantocid 40 Tab (pantoprazole)"]
    assert all(d.check == "billed_not_prescribed" for d in done.discrepancies)


def test_bill_only():
    bill = SourceItem(source_type=SourceType.IMAGE, text=BILL, document_kind=DocumentKind.BILL)
    rx = SourceItem(source_type=SourceType.IMAGE, text=RX, document_kind=DocumentKind.PRESCRIPTION)
    notes = SourceItem(source_type=SourceType.TEXT, text="Septran for 5 days")
    assert bill_only([bill])
    assert not bill_only([bill, rx]) and not bill_only([bill, notes]) and not bill_only([])


def test_a_pdf_with_a_prescription_and_a_bill_page():
    from tests.test_pdf import scanned_pdf

    db = Storage()
    enc = Encounter(patient_id="p1", sources=[
        SourceItem(source_type=SourceType.IMAGE, filename="visit.pdf", pages=2)])
    db.save_encounter(enc)
    readings = iter([RX, BILL])

    class PagesAI(FakeAI):
        def perceive(self, parts, instruction, **kw):
            self.calls.append(("perceive", "image_url", instruction))
            return next(readings)

    rx_item = ExtractedItem(kind=EntryKind.MEDICATION, label="Septran DS", source_id="doc1",
                            quote="Tab Septran DS", said_by="document", confidence=0.9)
    docs = [SortedDocument(source_id="doc1-page1", kind="prescription"),
            SortedDocument(source_id="doc1-page2", kind="bill", lines=BILL_LINES[2:3])]
    ai = PagesAI([rx_item], docs=docs)
    process_encounter(db, ai, enc.id,
                      [Media(enc.sources[0].id, "image", scanned_pdf(2), "application/pdf")])
    done = db.get_encounter(enc.id)

    assert done.status == EncounterStatus.READY
    assert done.sources[0].page_kinds == ["prescription", "bill"]
    (entry,) = done.entries
    assert entry.label == "Septran DS" and entry.provenance[0].locator == "page 1"
    assert [(b.name, b.provenance.locator) for b in done.bill_lines] == [("Pantocid 40 Tab", "page 2")]
    extraction_input = next(c[2] for c in ai.calls if c[:2] == ("reason_json", "Extraction"))
    assert "Pantocid" not in extraction_input


# ---- scan reports never make diagnoses ----
SCAN = "HRCT THORAX\nIMPRESSION:\n- Centrilobular emphysema, predominantly involving both upper lobes."
SCAN_QUOTE = "Centrilobular emphysema, predominantly involving both upper lobes."
RX_DX = "Rx\nDiagnosis: Acute bronchitis\n1. Tab Augmentin 625 mg"


def _dx(quote, label, source_id="doc1", chronic=False, said_by="document"):
    from app.pipeline.extract import ExtractedItem
    return ExtractedItem(kind=EntryKind.DIAGNOSIS, label=label, source_id=source_id, quote=quote,
                         said_by=said_by, confidence=0.9, chronic=chronic)


def _entry(item, source):
    from datetime import date

    from app.pipeline.extract import Extraction, to_entries
    (entry,), failed = to_entries(Extraction(items=[item]), {item.source_id: source}, date(2026, 10, 3))
    assert not failed
    return entry


def test_apply_kinds_keeps_imaging_for_a_photo_and_per_page():
    pdf = SourceItem(source_type=SourceType.IMAGE, pages=2, page_texts=[RX, SCAN], text=f"{RX}\n\n{SCAN}")
    photo = SourceItem(source_type=SourceType.IMAGE, text=SCAN)
    parts = parts_of([photo, pdf])
    sort = DocumentSort(documents=[
        SortedDocument(source_id="doc1", kind="imaging"),
        SortedDocument(source_id="doc2-page1", kind="prescription"),
        SortedDocument(source_id="doc2-page2", kind="imaging"),
    ])
    apply_kinds(sort, parts)
    assert photo.document_kind == DocumentKind.IMAGING
    assert pdf.page_kinds == ["prescription", "imaging"]
    assert extraction_text(photo) == SCAN  # still read (only bills are left out)


def test_a_diagnosis_from_a_scan_photo_becomes_a_note():
    from app.schemas.record import Tier
    photo = SourceItem(source_type=SourceType.IMAGE, text=SCAN, document_kind=DocumentKind.IMAGING)
    entry = _entry(_dx(SCAN_QUOTE, "Emphysema", chronic=True), photo)
    assert entry.kind == EntryKind.NOTE and entry.label == "Scan finding: Emphysema"
    assert entry.tier != Tier.EMERGENCY_CARD and entry.tier == Tier.FULL_RECORD


def test_a_diagnosis_from_a_discharge_summary_stays():
    summary = SourceItem(source_type=SourceType.IMAGE, text=RX_DX, document_kind=DocumentKind.REPORT)
    entry = _entry(_dx("Diagnosis: Acute bronchitis", "Acute bronchitis"), summary)
    assert entry.kind == EntryKind.DIAGNOSIS and entry.label == "Acute bronchitis"


def test_in_a_mixed_pdf_only_the_scan_page_is_turned_into_notes():
    pdf = SourceItem(source_type=SourceType.IMAGE, pages=2, page_texts=[RX_DX, SCAN],
                     text=f"{RX_DX}\n\n{SCAN}", document_kind=DocumentKind.PRESCRIPTION,
                     page_kinds=[DocumentKind.PRESCRIPTION, DocumentKind.IMAGING])
    assert _entry(_dx("Diagnosis: Acute bronchitis", "Acute bronchitis"), pdf).kind == EntryKind.DIAGNOSIS
    scan = _entry(_dx(SCAN_QUOTE, "Emphysema"), pdf)
    assert scan.kind == EntryKind.NOTE and scan.label == "Scan finding: Emphysema"


def test_a_diagnosis_from_a_consult_transcript_is_unchanged():
    audio = SourceItem(source_type=SourceType.AUDIO, text="Doctor: this is acute bronchitis.")
    entry = _entry(_dx("this is acute bronchitis", "Acute bronchitis", said_by="doctor"), audio)
    assert entry.kind == EntryKind.DIAGNOSIS and entry.label == "Acute bronchitis"
