"""Stage-2 grounding validation (SPEC §6.5): every sentence must be backed by verbatim quotes.

A verbatim quote proves the *quote* exists, not that the *sentence* follows from it. So besides
matching quotes we require that every number (digits and spelled-out), and the presence of
negation, in a sentence is backed by the QUOTES it cites (not merely somewhere in the chunk),
and we drop sentences carrying URLs or markup. Sentences that share no content word with their
quotes are flagged. Fuzzy matching is deliberately stricter than the SPEC's ">= 0.9 ratio": the
matched window must have the same alphanumeric tokens as the quote (see ``match_quote``), so a
changed name, month, modal or contraction is never accepted. What this cannot catch is a fluent
wrong inference from a real quote; that is what the optional LLM-judge stage and the M5 eval
are for.
"""

import html
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.config import Settings
from app.generation.schema import RawAnswer, RawSentence
from app.retrieval.types import RetrievedChunk

_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
}  # fmt: skip
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_NEGATIONS = {"not", "no", "never", "without", "cannot", "neither", "nor", "none"}
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_STOP = {
    "that", "this", "with", "from", "have", "will", "must", "shall", "their", "there", "which",
    "when", "than", "then", "they", "been", "were", "does", "into", "such",
}  # fmt: skip
_STRIP = " \t\n\r.,;:!?\"'()[]{}"


def _norm_char(ch: str) -> str:
    ch = _QUOTES.get(ch, ch).translate(_DASHES)
    return unicodedata.normalize("NFKC", ch).casefold()


def normalise(text: str) -> tuple[str, list[int]]:
    """Normalise for matching, returning the string and, per output char, its original index.

    NFKC (ligatures, full-width; combining marks are composed with their base), casefold, curly
    quotes/dashes to ASCII, zero-width / bidi / soft-hyphen characters removed, whitespace
    collapsed, and 'word- word' line-break hyphenation joined. The index map lets us report the
    matched span in the ORIGINAL chunk text.
    """
    # Group each base character with its combining marks so "e" + U+0301 composes to "é".
    clusters: list[tuple[str, int]] = []
    for i, raw in enumerate(text):
        cat = unicodedata.category(raw)
        if cat in ("Cf", "Cc") and not raw.isspace():
            continue  # zero-width, bidi, soft hyphen, control chars
        if cat == "Mn" and clusters and not clusters[-1][0].isspace():
            clusters[-1] = (clusters[-1][0] + raw, clusters[-1][1])
        else:
            clusters.append((raw, i))

    out: list[str] = []
    idx: list[int] = []
    pending_space = False
    for cluster, i in clusters:
        if cluster.isspace():
            pending_space = bool(out)
            continue
        base = cluster[0]
        normalised = unicodedata.normalize(
            "NFKC", _QUOTES.get(base, base).translate(_DASHES) + cluster[1:]
        ).casefold()
        for ch in normalised:
            if pending_space:
                # "carry- forward" (line-break hyphenation) -> "carry-forward"
                if not (out and out[-1] == "-" and ch.isalnum()):
                    out.append(" ")
                    idx.append(i)
                pending_space = False
            out.append(ch)
            idx.append(i)
    return "".join(out), idx


_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_DECIMAL_COMMA = re.compile(r"\d+,\d+")
_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "million", "billion", "dozen", "half", "twice", "double", "triple",
}  # fmt: skip
_NEGATION_RE = re.compile(r"\b(?:not|no|never|without|cannot|neither|nor|none)\b|n't\b")
_URL = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|org|net|io|dev|co|ru|cn)/", re.I)
_MARKUP = re.compile(r"!\[|\]\(|</?[a-z][^>]*>|`{3}|javascript:", re.I)


def _canon_number(tok: str) -> str:
    """Comma as thousands separator ("1,200" -> "1200") vs decimal comma ("10,5" -> "10.5")."""
    if _THOUSANDS.fullmatch(tok):
        return tok.replace(",", "")
    if _DECIMAL_COMMA.fullmatch(tok):
        return tok.replace(",", ".")
    return tok


def _numbers(text: str) -> Counter[str]:
    return Counter(_canon_number(n) for n in _NUMBER.findall(text))


def _number_words(text: str) -> Counter[str]:
    return Counter(w for w in _WORD.findall(text) if w in _NUMBER_WORDS)


def _has_negation(text: str) -> bool:
    return bool(_NEGATION_RE.search(text))


def _negations(text: str) -> Counter[str]:
    return Counter(_NEGATION_RE.findall(text))


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text)


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text) if len(w) > 3 and w not in _STOP and not w.isdigit()}


@dataclass(frozen=True)
class QuoteMatch:
    start: int  # in the original chunk text
    end: int
    exact: bool


def match_quote(quote: str, chunk_text: str, settings: Settings) -> QuoteMatch | None:
    """Find ``quote`` as ONE contiguous span of ``chunk_text``; None if it cannot be verified."""
    q_norm, _ = normalise(html.unescape(quote))
    q_norm = q_norm.strip(_STRIP)
    if not q_norm:
        return None
    c_norm, c_idx = normalise(chunk_text)

    def span(a: int, b: int, exact: bool) -> QuoteMatch:
        return QuoteMatch(c_idx[a], c_idx[b - 1] + 1, exact)

    # Exact match, but only on word boundaries: "paid leave" must not match inside "unpaid leave".
    lead = r"(?<![^\W_])" if q_norm[0].isalnum() else ""
    trail = r"(?![^\W_])" if q_norm[-1].isalnum() else ""
    m = re.search(lead + re.escape(q_norm) + trail, c_norm)
    if m is not None:
        return span(m.start(), m.end(), True)
    # Short quotes must match exactly: fuzzy ratios are meaningless on a few characters.
    if len(q_norm) < settings.quote_fuzzy_min_chars:
        return None
    # partial_ratio compares one contiguous window (never token-set: that accepts reordering).
    al = fuzz.partial_ratio_alignment(q_norm, c_norm, score_cutoff=settings.quote_fuzzy_ratio)
    if al is None:
        return None
    a, b = al.dest_start, al.dest_end
    while a > 0 and c_norm[a - 1] != " ":
        a -= 1
    while b < len(c_norm) and c_norm[b] != " ":
        b += 1
    window = c_norm[a:b]
    # A high ratio can hide a changed name / month / modal / contraction ("Jensen" vs "Hansen",
    # "can't" vs "can", "March" vs "May"). Require the same alphanumeric tokens, allowing at most
    # ``quote_fuzzy_token_tolerance`` differing ones (0 by default): fuzzy only forgives
    # punctuation and spacing. Numbers and negations must always be identical.
    qt, wt = _tokens(q_norm), _tokens(window)
    if len(qt) != len(wt):
        return None
    if sum(x != y for x, y in zip(qt, wt, strict=True)) > settings.quote_fuzzy_token_tolerance:
        return None
    if _numbers(q_norm) != _numbers(window) or _negations(q_norm) != _negations(window):
        return None
    return span(a, b, False)


@dataclass
class ValidatedCitation:
    chunk: RetrievedChunk
    quote: str  # the span actually found in the chunk (not the model's wording)
    char_start: int
    char_end: int


@dataclass
class ValidatedSentence:
    text: str
    citations: list[ValidatedCitation]
    flags: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    sentences: list[ValidatedSentence]
    total: int
    dropped: int
    drop_reasons: dict[str, int]
    status: str  # "answered" | "not_found"
    abstain_reason: str | None


def _check_sentence(
    sent: RawSentence, sources: dict[str, RetrievedChunk], settings: Settings
) -> tuple[ValidatedSentence | None, str | None]:
    if not sent.text.strip():
        return None, "empty_sentence"
    if not sent.citations:
        return None, "no_citation"
    if len(sent.citations) > settings.citations_per_sentence_max:
        return None, "too_many_citations"
    seen: set[tuple[str, str]] = set()
    cites: list[ValidatedCitation] = []
    for c in sent.citations:
        chunk = sources.get(c.source.strip())
        if chunk is None:
            return None, "unknown_source"  # invented alias or a raw UUID
        q = c.quote.strip()
        if "..." in q or "…" in q:
            return None, "quote_not_contiguous"
        if len(q) > settings.quote_max_chars:
            return None, "quote_too_long"
        if len(_WORD.findall(q)) < settings.quote_min_tokens:
            return None, "quote_too_short"
        key = (c.source.strip(), normalise(q)[0])
        if key in seen:
            continue
        seen.add(key)
        m = match_quote(q, chunk.text, settings)
        if m is None:
            return None, "quote_not_found"
        cites.append(ValidatedCitation(chunk, chunk.text[m.start : m.end], m.start, m.end))
    quotes_norm = " ".join(normalise(vc.quote)[0] for vc in cites)
    sent_norm = normalise(sent.text)[0]
    # Unsafe content: the sentence is model-authored text, so URLs / markup (phishing links,
    # image beacons) are only allowed if they appear verbatim in the cited quotes.
    if _MARKUP.search(sent.text):
        return None, "unsafe_markup"
    for url in _URL.finditer(sent_norm):
        if url.group(0) not in quotes_norm:
            return None, "unsafe_markup"
    # Numbers (digits and spelled-out) and negation must be backed by the quotes themselves.
    quote_numbers = _numbers(quotes_norm)
    if any(n not in quote_numbers for n in _numbers(sent_norm)):
        return None, "number_mismatch"
    quote_words = _number_words(quotes_norm)
    if any(w not in quote_words for w in _number_words(sent_norm)):
        return None, "number_mismatch"
    if _has_negation(sent_norm) != _has_negation(quotes_norm):
        return None, "negation_mismatch"
    flags: list[str] = []
    sentence_words = _content_words(normalise(sent.text)[0])
    if sentence_words and not (sentence_words & _content_words(quotes_norm)):
        flags.append("weak_overlap")
    if any(vc.chunk.flags.get("injection") for vc in cites):
        flags.append("cites_flagged_chunk")
    return ValidatedSentence(sent.text.strip(), cites, flags), None


def validate_answer(
    answer: RawAnswer, sources: dict[str, RetrievedChunk], settings: Settings
) -> ValidationResult:
    """Drop sentences that fail grounding; abstain if more than 1/3 fail or none remain."""
    if answer.status == "not_found":
        # Sentences that accompany not_found are discarded unseen.
        return ValidationResult([], 0, 0, {}, "not_found", "model_not_found")
    if not answer.answer_sentences:
        return ValidationResult([], 0, 0, {}, "not_found", "empty_answer")

    # Merge duplicate sentences (same normalised text) before counting.
    unique: list[RawSentence] = []
    seen_text: set[str] = set()
    for s in answer.answer_sentences:
        key = normalise(s.text)[0]
        if key not in seen_text:
            seen_text.add(key)
            unique.append(s)

    reasons: Counter[str] = Counter()
    kept: list[ValidatedSentence] = []
    for pos, sent in enumerate(unique):
        if pos >= settings.answer_max_sentences:
            reasons["too_many_sentences"] += 1
            continue
        ok, reason = _check_sentence(sent, sources, settings)
        if ok is None:
            reasons[reason or "invalid"] += 1
        else:
            kept.append(ok)

    total, dropped = len(unique), len(unique) - len(kept)
    # Integer arithmetic: abstain when dropped/total > 1/3, or nothing survives.
    if not kept or 3 * dropped > total:
        return ValidationResult([], total, dropped, dict(reasons), "not_found", "ungrounded")
    return ValidationResult(kept, total, dropped, dict(reasons), "answered", None)
