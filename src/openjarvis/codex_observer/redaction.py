"""Recursive redaction for Codex observer payloads."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
}
_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"),
)


def redact(value: Any, *, key: str = "") -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern in _PATTERNS:
            result = pattern.sub(REDACTED, result)
        return result
    return value
