from datetime import datetime, timezone

from openjarvis.context.runtime import (
    RUNTIME_CONTEXT_PREFIX,
    build_runtime_context,
    clock_snapshot,
    is_clock_lookup_query,
    is_time_sensitive_query,
)


def test_temporal_queries_are_classified_for_runtime_context():
    assert is_time_sensitive_query("What day is it today?")
    assert is_time_sensitive_query("What time is it now?")
    assert is_clock_lookup_query("What day is it today?")
    assert not is_time_sensitive_query("Turn on the kitchen light")


def test_clock_snapshot_uses_requested_timezone_and_fixed_instant():
    snapshot = clock_snapshot(
        timezone_name="America/Denver",
        now=datetime(2026, 8, 9, 1, 30, tzinfo=timezone.utc),
    )

    assert snapshot["date"] == "Saturday, August 8, 2026"
    assert snapshot["time"] == "7:30:00 PM"
    assert snapshot["timezone"] == "America/Denver"
    assert snapshot["weekday"] == "Saturday"
    assert snapshot["week_number"] == 32


def test_runtime_context_is_explicitly_authoritative():
    context = build_runtime_context(
        "What day is it today?",
        now=datetime(2026, 8, 9, 1, 30, tzinfo=timezone.utc),
    )

    assert context is not None
    assert context.startswith(RUNTIME_CONTEXT_PREFIX)
    assert "Do not infer the answer from training data" in context
    assert "Current date:" in context
