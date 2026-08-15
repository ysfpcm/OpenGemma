"""Tests for the local devotional content store and tool."""

from __future__ import annotations

import sqlite3

from openjarvis.tools.devotional_tool import DevotionalContentTool


def test_read_seeds_schema_and_returns_day_specific_entry(tmp_path):
    db_path = tmp_path / "devotional.db"
    tool = DevotionalContentTool(db_path=db_path)

    result = tool.execute(action="read", date="2026-08-10")  # Monday

    assert result.success is True
    assert result.metadata["scripture_reference"] == "Psalm 5:3"
    assert "Scripture text:" in result.content
    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "devotionals" in tables
    assert "devotional_preferences" in tables


def test_user_can_add_entry_and_update_preferences(tmp_path):
    tool = DevotionalContentTool(db_path=tmp_path / "devotional.db")

    added = tool.execute(
        action="add",
        scripture_reference="John 3:16",
        scripture_text="For God so loved the world.",
        theme="Love",
        weekday=0,
    )
    preferences = tool.execute(
        action="set_preferences",
        translation="KJV",
        preferred_time="05:00",
        worship_focus="Prepare my heart for worship.",
    )

    assert added.success is True
    assert preferences.success is True


def test_add_requires_source_scripture(tmp_path):
    tool = DevotionalContentTool(db_path=tmp_path / "devotional.db")

    result = tool.execute(action="add", theme="Missing verse")

    assert result.success is False
    assert "scripture_reference" in result.content
