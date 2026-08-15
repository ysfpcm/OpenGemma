"""Normalize observable Codex protocol events into Phase 0 contracts."""

from __future__ import annotations

from typing import Any, Optional

from openjarvis.cognition import Observation

from .redaction import redact


def _thread_id(params: dict[str, Any]) -> Optional[str]:
    return params.get("threadId") or params.get("thread", {}).get("id")


def _item(params: dict[str, Any]) -> dict[str, Any]:
    item = params.get("item", {})
    return item if isinstance(item, dict) else {}


def normalize(
    method: str, params: dict[str, Any]
) -> tuple[Observation, dict[str, Any], Optional[str]]:
    safe = redact(params)
    item = _item(safe)
    item_type = str(item.get("type", ""))
    if "reasoning" in method.lower() or item_type == "reasoning":
        safe = {
            key: value for key, value in safe.items() if key in {"threadId", "turnId"}
        }
        safe["item"] = {
            key: value for key, value in item.items() if key in {"id", "type", "status"}
        }
        item = _item(safe)
    summary: Optional[str] = None
    if method == "thread/started":
        summary = "Codex opened the read-only investigation"
    elif method == "turn/started":
        summary = "Codex began investigating"
    elif method == "turn/plan/updated":
        summary = "Codex updated its observable plan"
    elif method == "turn/diff/updated":
        summary = "Codex updated the aggregated file diff"
    elif method == "turn/completed":
        status = safe.get("turn", {}).get("status", "completed")
        summary = f"Codex turn {status}"
    elif method in {"error", "turn/error"}:
        summary = "Codex reported an error"
    elif method == "item/completed" and item_type:
        labels = {
            "commandExecution": "Codex completed a read-only command",
            "fileChange": "Codex reported a file-change item",
            "agentMessage": "Codex produced an observable finding",
            "mcpToolCall": "Codex completed an MCP tool call",
            "dynamicToolCall": "Codex completed a dynamic or browser tool call",
            "webSearch": "Codex completed web research",
            "imageView": "Codex inspected an image",
            "plan": "Codex completed a plan item",
        }
        summary = labels.get(item_type, f"Codex completed {item_type}")
    contract = Observation(
        subject=f"codex:{method}",
        value=safe,
        source_kind="observed",
        provenance={"system": "codex-app-server", "method": method},
        sensitivity_labels=["workspace"],
    )
    display = {
        "method": method,
        "thread_id": _thread_id(safe),
        "item_type": item_type,
        "payload": safe,
    }
    return contract, display, summary
