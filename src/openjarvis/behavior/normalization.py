"""Small, dependency-free normalization helpers for spoken commands."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_FILLER_WORDS = re.compile(r"\b(?:uh|um|erm|er|hmm|hey|jarvis|please)\b")
_CONTRACTIONS = {
    "what's": "what is",
    "whats": "what is",
    "that's": "that is",
    "thats": "that is",
    "it's": "it is",
    "its": "it is",
    "don't": "do not",
    "dont": "do not",
}


def normalize_text(value: object) -> str:
    """Normalize a transcript while preserving words useful for entity lookup."""
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("’", "'")
    for source, replacement in _CONTRACTIONS.items():
        text = text.replace(source, replacement)
    text = _FILLER_WORDS.sub(" ", text)
    text = re.sub(r"[^a-z0-9_.]+", " ", text)
    return " ".join(text.split())


def tokenize(value: object) -> tuple[str, ...]:
    """Return normalized word tokens."""
    normalized = normalize_text(value)
    return tuple(normalized.replace(".", " ").split())


def text_similarity(left: object, right: object) -> float:
    """Return a conservative similarity score for typo-tolerant matching."""
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    if not left_text or not right_text:
        return 0.0
    if left_text == right_text:
        return 1.0
    left_tokens = set(tokenize(left_text))
    right_tokens = set(tokenize(right_text))
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left_text, right_text).ratio()
    return max(jaccard, sequence)


def token_similarity(left: str, right: str) -> float:
    """Compare two individual words, useful for speech-transcription typos."""
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def is_reference_phrase(value: object) -> bool:
    """Whether a transcript refers to a recently discussed device."""
    text = normalize_text(value)
    return bool(
        re.search(r"\b(?:it|this|that)\b", text)
        or re.search(r"\b(?:the|that|this)\s+(?:light|lamp|device|fan)\b", text)
    )


__all__ = [
    "is_reference_phrase",
    "normalize_text",
    "text_similarity",
    "token_similarity",
    "tokenize",
]
