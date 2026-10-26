"""The quote check: a model proposal is kept only if its quote really appears in its source.

Matching ignores case, spacing and punctuation, so "Azithromycin 500mg." matches
"azithromycin  500 mg". Letters and combining marks are kept, so Hindi (Devanagari vowel signs
are combining marks) is compared exactly.
"""

from __future__ import annotations

import itertools
import re
import unicodedata

from app.schemas.record import TranscriptSegment

_SPACES = re.compile(r"\s+")
_DIGIT_LETTER = re.compile(r"(?<=\d)(?=[^\W\d_])|(?<=[^\W\d_])(?=\d)")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    # Punctuation and symbols become spaces; letters, digits and combining marks stay.
    text = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)
    text = _DIGIT_LETTER.sub(" ", text)  # "500mg" and "500 mg" compare equal
    return _SPACES.sub(" ", text).strip()


def locate(quote: str, segments: list[TranscriptSegment]) -> str | None:
    """Audio quotes: the time span of the piece holding the quote, e.g. '00:30-01:00'.

    A quote can straddle a cut, so neighbouring pairs are tried after single pieces.
    """
    for s in segments:
        if quote_in_source(quote, s.text):
            return f"{_mmss(s.start_s)}-{_mmss(s.end_s)}"
    for a, b in itertools.pairwise(segments):
        if quote_in_source(quote, f"{a.text}\n{b.text}"):
            return f"{_mmss(a.start_s)}-{_mmss(b.end_s)}"
    return None


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def quote_in_source(quote: str | None, source_text: str | None) -> bool:
    """True if the (non-empty) quote appears in the source, word for word after normalizing."""
    if not quote or not source_text:
        return False
    q = normalize(quote)
    return bool(q) and f" {q} " in f" {normalize(source_text)} "
