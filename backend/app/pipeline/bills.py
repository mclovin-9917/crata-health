"""Sorting document photos and reading bills.

A pharmacy bill says what was sold, not what was prescribed, so in a visit with a prescription,
consult or notes its lines never become record entries (the bill line 'Pantocid 40 Tab' is not a
prescription). One model call sorts the
visit's documents (prescription, bill, report, other) and reads each bill line by line. A
multi-page PDF is sorted page by page, because one file often holds a prescription and a bill.
Code then:

- keeps a line only if its name is in the bill's reading, with an exact quote: the model's, or
  if the model shortened it, the reading's own line holding that name;
- keeps the quantity only if that number is in the quote (no unbacked numbers for the
  quantity check);
- decides drug or not: a name the brand table knows is a drug; consumables and fees on a short
  list are not; otherwise the model's answer stands;
- fills generic names from the brand table, then the model for names it doesn't know.

Bills, and the bill pages of a PDF, are then left out of the record extraction. The cross-checks compare bill lines with
the prescription and the consult.

A visit that is only a bill has nothing to compare with. There, each medicine line becomes a
suggestion the patient confirms or rejects (`bill_suggestions`): a bill may hold medicines
bought for someone else, so nothing enters the record without the patient's yes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.client import AIClient
from app.pipeline import prompts
from app.pipeline.medicines import ask_generics, from_table
from app.pipeline.quotes import normalize, quote_in_source
from app.schemas.record import (
    BillLine,
    DocumentKind,
    Entry,
    EntryKind,
    Medication,
    Provenance,
    SourceItem,
    Speaker,
    Tier,
)


class ReadBillLine(BaseModel):
    name: str
    quote: str
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = None
    units_per_pack: int | None = Field(default=None, ge=1)
    item_type: Literal["medicine", "non_drug"]


class SortedDocument(BaseModel):
    source_id: str
    kind: DocumentKind
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    lines: list[ReadBillLine] = Field(default_factory=list)


class DocumentSort(BaseModel):
    documents: list[SortedDocument]


# Bill items that are not medicines, whatever the model says. Matched as whole words.
NON_DRUG = re.compile(
    r"\b(syringes?|needles?|gloves?|cotton|bandages?|gauze|plasters?|masks?|cannula|"
    r"iv set|catheters?|thermometers?|glucometers?|lancets?|test strips?|swabs?|"
    r"fees?|charges?|consultation|registration|delivery|packing|service)\b"
)


def is_drug(line: ReadBillLine) -> bool:
    """Policy in code: the brand table wins, then the non-drug list, then the model's answer."""
    if from_table(line.name):
        return True
    if NON_DRUG.search(line.name.casefold()):
        return False
    return line.item_type == "medicine"


def number_in(quote: str, value: float) -> bool:
    """Is this number written in the quote? 10.0 matches '10'; 1.5 matches '1.5'."""
    return f" {normalize(f'{value:g}')} " in f" {normalize(quote)} "


def line_with(name: str, text: str) -> str | None:
    """The only line of the reading that holds this name, or None (absent, or ambiguous)."""
    found = [ln.strip() for ln in text.splitlines() if ln.strip() and quote_in_source(name, ln)]
    return found[0] if len(found) == 1 else None


def bill_date(sort: DocumentSort, today: date) -> date | None:
    """The earliest real, not-future date printed on a bill."""
    found = []
    for d in sort.documents:
        if d.kind == DocumentKind.BILL and d.date:
            try:
                found.append(date.fromisoformat(d.date))
            except ValueError:
                continue
    found = [d for d in found if date(1950, 1, 1) <= d <= today]
    return min(found, default=None)


@dataclass
class Part:
    """What the sort sees as one document: a photo, a one-page PDF, or one page of a PDF."""

    source: SourceItem
    page: int | None  # page number within a multi-page PDF
    text: str


def parts_of(documents: list[SourceItem]) -> dict[str, Part]:
    """Short ids for the model: 'doc1' for a photo, 'doc2-page1', 'doc2-page2' for PDF pages."""
    out: dict[str, Part] = {}
    for i, s in enumerate(documents, start=1):
        if len(s.page_texts) > 1:
            for n, text in enumerate(s.page_texts, start=1):
                if text.strip():
                    out[f"doc{i}-page{n}"] = Part(s, n, text)
        elif s.text:
            out[f"doc{i}"] = Part(s, None, s.text)
    return out


def apply_kinds(sort: DocumentSort, parts: dict[str, Part]) -> None:
    """Record what each document (and each page of a PDF) is. A page the model skipped counts as
    'other', so it still goes to the record extraction."""
    kinds = {d.source_id: d.kind for d in sort.documents}
    for sid, part in parts.items():
        kind = kinds.get(sid, DocumentKind.OTHER)
        source = part.source
        if part.page is None:
            source.document_kind = kind
            continue
        if len(source.page_kinds) != len(source.page_texts):
            source.page_kinds = [DocumentKind.OTHER] * len(source.page_texts)
        source.page_kinds[part.page - 1] = kind
    for source in {id(p.source): p.source for p in parts.values() if p.page}.values():
        rest = [k for k in source.page_kinds if k != DocumentKind.BILL]
        source.document_kind = rest[0] if rest else DocumentKind.BILL


def extraction_text(source: SourceItem) -> str | None:
    """What the record extraction may read from a document: everything but its bill pages."""
    if source.page_kinds and len(source.page_kinds) == len(source.page_texts):
        pages = [t for t, k in zip(source.page_texts, source.page_kinds, strict=True)
                 if k != DocumentKind.BILL]
        return "\n\n".join(pages) or None
    return None if source.document_kind == DocumentKind.BILL else source.text


def from_imaging(source: SourceItem, quote: str) -> bool:
    """Is this quote from a scan report? With page kinds, the pages that hold the quote must all
    be imaging (a PDF can hold a prescription and a scan report); if no single page holds it,
    or there are no page kinds, the document's own kind decides."""
    whole = source.document_kind == DocumentKind.IMAGING
    if source.page_kinds and len(source.page_kinds) == len(source.page_texts):
        kinds = [k for t, k in zip(source.page_texts, source.page_kinds, strict=True)
                 if quote_in_source(quote, t)]
        if kinds:
            return all(k == DocumentKind.IMAGING for k in kinds)
    return whole


def to_bill_lines(
    sort: DocumentSort, parts: dict[str, Part], ai: AIClient
) -> tuple[list[BillLine], list[str]]:
    """Bill lines that pass the checks, and the names of the ones dropped. At most one short
    model call (brand names the table doesn't know)."""
    lines: list[BillLine] = []
    failed: list[str] = []
    for doc in sort.documents:
        part = parts.get(doc.source_id)
        if part is None or doc.kind != DocumentKind.BILL:
            continue
        source, text = part.source, part.text
        for read in doc.lines:
            quote = read.quote
            if not quote_in_source(quote, text):
                # The model shortened the line ("Ascoril LS Syrup 1" for "... 100 ml 1"): use
                # the bill's own line holding the name, so the quote is exact.
                quote = line_with(read.name, text)
            if not (quote and quote_in_source(read.name, text)):
                failed.append(f"bill line {read.name}")
                continue
            quantity = read.quantity
            if quantity is not None and not number_in(quote, quantity):
                quantity = None
            lines.append(BillLine(
                name=read.name.strip(),
                quantity=quantity,
                unit=(read.unit or "").strip() or None,
                units_per_pack=read.units_per_pack,
                is_drug=is_drug(read),
                provenance=Provenance(
                    source_id=source.id, source_type=source.source_type, quote=quote,
                    locator=f"page {part.page}" if part.page else None,
                ),
            ))

    unknown: list[BillLine] = []
    for line in lines:
        if not line.is_drug:
            continue
        if generics := from_table(line.name):
            line.generic_names, line.generic_from = list(generics), "table"
        else:
            unknown.append(line)
    matches = ask_generics([line.name for line in unknown], ai)
    for line in unknown:
        m = matches.get(line.name.casefold())
        if m and m.status == "specific" and m.generics:
            line.generic_names, line.generic_from = m.generics, "model"
    return lines, failed


def bill_only(sources: list[SourceItem]) -> bool:
    """The visit is bills and nothing else that was read: no prescription, report, recording or
    notes left for the record extraction."""
    read = [s for s in sources if s.text]
    return bool(read) and not any(extraction_text(s) for s in read)


def bill_suggestions(lines: list[BillLine], bought_on: date | None) -> list[Entry]:
    """A bill-only visit: each medicine line as a suggestion. No dose or duration (a bill does
    not say how it is taken), so the patient can add them by editing."""
    out = []
    for b in lines:
        if not b.is_drug:
            continue
        billed = " ".join(x for x in (f"{b.quantity:g}" if b.quantity is not None else "",
                                      b.unit or "") if x)
        out.append(Entry(
            kind=EntryKind.MEDICATION,
            label=b.name,
            fields={"bought": billed} if billed else {},
            provenance=[b.provenance],
            said_by=Speaker.DOCUMENT,
            tier=Tier.EMERGENCY_CARD,
            medication=Medication(started_on=bought_on, generic_names=b.generic_names,
                                  generic_from=b.generic_from),
        ))
    return out


def sort_documents(parts: dict[str, Part], ai: AIClient) -> DocumentSort:
    """One model call over the document readings."""
    block = "\n\n".join(f"=== source_id: {sid} ===\n{p.text}" for sid, p in parts.items())
    return ai.reason_json(prompts.SORT_DOCUMENTS, block, DocumentSort)
