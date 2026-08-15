"""Tests for the built-in readiness agent templates."""

from __future__ import annotations

from openjarvis.agents.manager import AgentManager


def test_readiness_templates_are_discoverable():
    templates = {template["id"]: template for template in AgentManager.list_templates()}

    spiritual = templates["spiritual_readiness"]
    assert spiritual["schedule_type"] == "cron"
    assert spiritual["schedule_value"] == "0 5 * * *"
    assert "devotional_content" in spiritual["tools"]

    commute = templates["commute_readiness"]
    assert commute["schedule_type"] == "cron"
    assert "commute_readiness" in commute["tools"]
