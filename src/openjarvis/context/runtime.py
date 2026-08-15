"""Authoritative request-time runtime context.

The model's training data and conversation memory are not suitable sources for
questions about the current date or time.  This module keeps those values at
the request boundary so every caller can use the same clock semantics.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


RUNTIME_CONTEXT_PREFIX = "## Authoritative Runtime Context"

_TIME_SENSITIVE_QUERY = re.compile(
    r"\b(?:"
    r"today|tomorrow|yesterday|now|currently|current|"
    r"what\s+(?:day|date|time)|"
    r"day\s+of\s+(?:the\s+)?week|"
    r"time\s*zone|timezone|week\s+number|"
    r"this\s+(?:day|week|month|year)"
    r")\b",
    re.IGNORECASE,
)

_CLOCK_LOOKUP_QUERY = re.compile(
    r"\b(?:"
    r"what\s+(?:day|date|time)\s+is\s+it|"
    r"what(?:'s|\s+is)\s+the\s+current\s+(?:day|date|time)|"
    r"current\s+(?:day|date|time)|"
    r"today(?:'s)?\s+(?:day|date)"
    r")\b",
    re.IGNORECASE,
)


def is_time_sensitive_query(query: str) -> bool:
    """Return whether *query* needs request-time clock data."""

    return bool(_TIME_SENSITIVE_QUERY.search(query or ""))


def is_clock_lookup_query(query: str) -> bool:
    """Return whether a query is asking directly for the current clock."""

    return bool(_CLOCK_LOOKUP_QUERY.search(query or ""))


def _configured_timezone(config: Any = None) -> str:
    """Resolve an explicit IANA timezone without guessing from persona data."""

    for key in ("OPENJARVIS_TIMEZONE", "TZ"):
        value = os.environ.get(key, "").strip()
        if value:
            return value

    runtime_config = getattr(config, "runtime", None)
    value = getattr(runtime_config, "timezone", "") if runtime_config else ""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _resolve_timezone(config: Any = None, timezone_name: str | None = None) -> tuple[str, tzinfo]:
    """Return a display name and timezone object.

    An explicit configured IANA timezone wins.  If none is configured, use the
    operating system's local timezone.  The OS fallback is important on
    Windows, where ``ZoneInfo`` does not ship with a Windows-to-IANA mapping.
    """

    requested = (timezone_name or _configured_timezone(config)).strip()
    if requested:
        try:
            return requested, ZoneInfo(requested)
        except ZoneInfoNotFoundError:
            # Invalid configuration must not make the assistant fail.  Keep a
            # truthful local-clock fallback and expose the actual abbreviation.
            pass

    local_now = datetime.now().astimezone()
    local_zone = local_now.tzinfo or timezone.utc
    display_name = local_now.tzname() or "local time"
    return display_name, local_zone


def clock_snapshot(
    *,
    config: Any = None,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, str | int]:
    """Return deterministic current date/time fields for a request or tool."""

    display_name, local_zone = _resolve_timezone(config, timezone_name)
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_now = instant.astimezone(local_zone)
    date_text = f"{local_now.strftime('%A, %B')} {local_now.day}, {local_now.year}"
    time_text = local_now.strftime("%I:%M:%S %p").lstrip("0")
    iso_text = local_now.isoformat(timespec="seconds")
    return {
        "date": date_text,
        "time": time_text,
        "timezone": display_name,
        "iso": iso_text,
        "weekday": local_now.strftime("%A"),
        "week_number": local_now.isocalendar().week,
    }


def build_runtime_context(
    query: str,
    *,
    config: Any = None,
    now: datetime | None = None,
) -> str | None:
    """Build a model-facing clock context only when the query needs it."""

    if not is_time_sensitive_query(query):
        return None

    snapshot = clock_snapshot(config=config, now=now)
    return "\n".join(
        [
            RUNTIME_CONTEXT_PREFIX,
            "Captured by the server at request time; this is authoritative for current date/time questions.",
            "Do not infer the answer from training data, memory, or earlier conversation.",
            f"- Current date: {snapshot['date']}",
            f"- Current time: {snapshot['time']}",
            f"- Timezone: {snapshot['timezone']}",
            f"- ISO timestamp: {snapshot['iso']}",
            f"- ISO week number: {snapshot['week_number']}",
        ]
    )


def format_clock_answer(query: str, snapshot: dict[str, str | int]) -> str:
    """Format a deterministic, concise answer for a direct clock lookup."""

    text = (query or "").lower()
    if "week" in text:
        return f"This is ISO week {snapshot['week_number']} of the year."
    if "time" in text and not any(term in text for term in ("day", "date")):
        return f"The current time is {snapshot['time']} ({snapshot['timezone']})."
    return f"Today is {snapshot['date']}."


__all__ = [
    "RUNTIME_CONTEXT_PREFIX",
    "build_runtime_context",
    "clock_snapshot",
    "format_clock_answer",
    "is_clock_lookup_query",
    "is_time_sensitive_query",
]
