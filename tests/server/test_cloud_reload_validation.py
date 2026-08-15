"""Validation tests for the cloud-key reload boundary."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from openjarvis.server.routes import (  # noqa: E402
    _parse_submitted_api_keys,
    _read_saved_api_keys,
)


def test_parse_submitted_api_keys_accepts_managed_keys() -> None:
    keys = _parse_submitted_api_keys(
        {
            "OPENAI_API_KEY": "sk-test",
            "TAVILY_API_KEY": "tvly-test",
            "GEMINI_API_KEY": "",
        }
    )

    assert keys == {
        "OPENAI_API_KEY": "sk-test",
        "TAVILY_API_KEY": "tvly-test",
        "GEMINI_API_KEY": "",
    }


def test_parse_submitted_api_keys_rejects_unknown_names() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _parse_submitted_api_keys({"EVIL_API_KEY": "value"})
    assert "Unsupported API key" in str(excinfo.value.detail)


def test_parse_submitted_api_keys_rejects_line_breaks() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _parse_submitted_api_keys({"OPENAI_API_KEY": "safe\nINJECTED=value"})
    assert "Invalid value" in str(excinfo.value.detail)


def test_read_saved_api_keys_filters_unknown_entries(tmp_path) -> None:
    path = tmp_path / "cloud-keys.env"
    path.write_text(
        "OPENAI_API_KEY=sk-test\nEVIL_API_KEY=ignored\nTAVILY_API_KEY=tvly-test\n",
        encoding="utf-8",
    )

    assert _read_saved_api_keys(path) == {
        "OPENAI_API_KEY": "sk-test",
        "TAVILY_API_KEY": "tvly-test",
    }
