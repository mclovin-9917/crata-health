"""Test values: numbers printed on a report or a prescription, read into the record as they are.

They enter the record without a confirm tap (the one exception to "only the patient confirms"),
so code checks every one against the document before it is kept:
the quote is in the source, the number is printed in the quote, and so is the parameter's name.
A result that fails any check is dropped and listed with the visit's left-out suggestions.
Values never reach the emergency card.
"""

from __future__ import annotations

import re
from datetime import date

from app.pipeline.extract import ExtractedResult, Extraction
from app.pipeline.quotes import locate, normalize, quote_in_source
from app.schemas.record import (
    Entry,
    EntryKind,
    EntryStatus,
    Provenance,
    SourceItem,
    SourceType,
    Speaker,
    StatusChange,
    TestResult,
    Tier,
)

READ_NOTE = "Read from the report"

# 14,200 and 1,42,000 (Indian grouping), 13.2, .5, 138. A "." or "," ending a sentence is not part
# of the number.
_NUMBER = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|(?<![\d.])\d+(?:\.\d+)?|(?<![\d.])\.\d+")


def numbers_in(text: str) -> list[float]:
    """Every number printed in the text, as numbers ('14,200' is 14200, '13.2' is not 132)."""
    return [float(m.group().replace(",", "")) for m in _NUMBER.finditer(text)]


def printed(value: float | None, quote: str) -> bool:
    """Is this number one of the numbers printed in the quote?"""
    return value is not None and any(abs(n - value) < 1e-9 for n in numbers_in(quote))


_MONTHS = ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%b-%y")
_NUMERIC = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y", "%Y-%m-%d")


def printed_date(text: str | None, source_text: str | None, today: date) -> date | None:
    """The date the model gave for a value, if it is a real, not-future date that is printed in
    the source in a common way (12/10/2026, 12-10-2026, 12 Oct 2026, 2026-10-12)."""
    try:
        d = date.fromisoformat(text or "")
    except ValueError:
        return None
    if not date(1950, 1, 1) <= d <= today or not source_text:
        return None
    hay = " ".join(source_text.casefold().split())
    for fmt in (*_NUMERIC, *_MONTHS):
        written = d.strftime(fmt).casefold()
        # "%d" pads with a zero; documents also write 5/3/2026, so try both.
        unpadded = "".join(p.lstrip("0") or "0" if p[:1].isdigit() else p
                           for p in re.split(r"([/.\- ,]+)", written))
        # No digit may touch either end: 1/10/2026 is not inside 11/10/2026, nor 17/10/20 in 17/10/2026.
        if any(re.search(rf"(?<!\d){re.escape(w)}(?!\d)", hay) for w in {written, unpadded}):
            return d
    return None


def words_printed(text: str, quote: str) -> bool:
    """Is a result in words printed in the row, as typed? Signs and hyphens count ('<0.5' is not
    '>0.5'), and a result is not taken out of a negation: 'Reactive' is not in 'Non-reactive',
    'Detected' is not in 'Not detected', 'Growth' is not in 'No growth'."""
    row, words = " ".join(quote.casefold().split()), " ".join(text.casefold().split())
    if not words:
        return False
    for m in re.finditer(re.escape(words), row):
        before, after = row[:m.start()], row[m.end():]
        if re.search(r"[\w\-]$", before) or re.match(r"\w", after):
            continue  # inside another word or a hyphenated one (non-reactive)
        if re.search(r"\b(no|not|non|without)\W*$", before):
            continue  # the printed result is the opposite
        return True
    return False


def same_row(r: ExtractedResult) -> bool:
    """The name and the value sit on one line of the quote, so a quote that runs over two rows
    cannot lend one row's number to another row's name."""
    for line in r.quote.splitlines():
        if not quote_in_source(r.name, line):
            continue
        value_ok = r.value is None or printed(r.value, line)
        words_ok = r.value is not None or words_printed(_clean(r.value_text) or "", line)
        if value_ok and words_ok:
            return True
    return False


def label_of(r: ExtractedResult) -> str:
    """The name as printed; a blood pressure reading is two values, 'BP (systolic)' and so on."""
    return f"{r.name.strip()} ({r.part})" if r.part else r.name.strip()


def _clean(text: str | None) -> str | None:
    return (text or "").strip() or None


_MARKER = re.compile(r"^\s*(?:[-*•–—]|\d{1,2}[.)])\s+")


def strip_marker(text: str | None) -> str | None:
    """A list marker ('- ', '* ', a bullet, '1. ', '1) ') at the start of a result in words is
    layout, not part of the finding. Applied after the checks, so validation is unchanged."""
    if text is None:
        return None
    return _MARKER.sub("", text, count=1).strip() or text


def check_result(r: ExtractedResult, source: SourceItem | None) -> TestResult | None:
    """The result's values if every check passes, else None."""
    if source is None or source.source_type != SourceType.IMAGE:
        return None  # only what a document prints, never what was said
    if not quote_in_source(r.quote, source.text):
        return None
    if not quote_in_source(r.name, r.quote) or not same_row(r):
        return None  # the number must sit on the row of the parameter it is filed under
    text = _clean(r.value_text)
    value = r.value
    if value is not None and not printed(value, r.quote):
        return None
    if text is not None and not words_printed(text, r.quote):
        if value is None:
            return None
        text = None
    if value is None and text is None:
        return None
    low = r.ref_low if printed(r.ref_low, r.quote) else None
    high = r.ref_high if printed(r.ref_high, r.quote) else None
    unit = _clean(r.unit)
    if unit is not None and not (
        unit in r.quote if not normalize(unit) else quote_in_source(unit, r.quote)
    ):
        unit = None  # kept only if it is printed in the row (symbols such as % compare as typed)
    ref_text = _clean(r.ref_text)
    if ref_text is not None and not quote_in_source(ref_text, r.quote):
        ref_text = None
    return TestResult(value=value, value_text=strip_marker(text), unit=unit, ref_low=low, ref_high=high,
                      ref_text=ref_text)


def to_results(
    extraction: Extraction,
    sources: dict[str, SourceItem],
    measured_on: date,
    tests: list[Entry],
    fallback_test: Entry | None = None,
    today: date | None = None,
) -> tuple[list[Entry], list[str]]:
    """Result entries that pass the checks, and labels of the ones that failed.

    A value is dated by the date the model read next to it if that date is printed in its
    document (`printed_date`), else `measured_on` (the document's date, else the upload day).
    `tests` are the test entries of this visit (new and earlier) a value can belong to, matched
    by label. A value whose test is not found goes to `fallback_test` (the one the patient tapped
    "Add report" on), else stands alone (vitals on a prescription).
    """
    by_label = {e.label.casefold().strip(): e for e in tests}
    kept: list[Entry] = []
    failed: list[str] = []
    for r in extraction.results:
        source = sources.get(r.source_id)
        checked = check_result(r, source)
        if checked is None or source is None:
            failed.append(f"{label_of(r)} (value)")
            continue
        checked.measured_on = printed_date(r.measured_on, source.text, today or date.today()) \
            or measured_on
        test = by_label.get((r.test or "").casefold().strip()) or fallback_test
        entry = Entry(
            kind=EntryKind.RESULT,
            label=label_of(r),
            provenance=[Provenance(source_id=source.id, source_type=source.source_type,
                                   locator=locate(r.quote, source.segments), quote=r.quote)],
            status=EntryStatus.CONFIRMED,
            status_history=[StatusChange(status=EntryStatus.CONFIRMED, by="system", note=READ_NOTE)],
            confidence=r.confidence,
            tier=Tier.FULL_RECORD,
            said_by=Speaker.DOCUMENT,
            result=checked,
            added_as_read=True,
            links=[test.id] if test else [],
        )
        kept.append(entry)
    return kept, failed
