"""Offline test harness for the block-agent POC.

Runs entirely without Azure: it fakes the Foundry, Graph and ARM HTTP endpoints
in memory, then replays the sample budget-alert payloads through the real parser
and dispatcher. It verifies that:

  * a budget alert blocks the agent across all three mechanisms, and
  * the unblock direction restores each mechanism's prior state (reversibility).

Run with:  python -m tests.test_harness   (from the repo root)
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

# ---- Environment must be set before importing blockagent.config -------------
os.environ.setdefault("AZURE_SUBSCRIPTION_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("AZURE_RESOURCE_GROUP", "rg-agents")
os.environ.setdefault("FOUNDRY_ACCOUNT_NAME", "agent-verse-resource")
os.environ.setdefault(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://agent-verse-resource.services.ai.azure.com/api/projects/demo",
)
os.environ.setdefault(
    "AGENT_TARGET_MAP",
    json.dumps(
        {
            "asst_demo123": {
                "foundry_agent_id": "asst_demo123",
                "service_principal_id": "11111111-1111-1111-1111-111111111111",
            }
        }
    ),
)

from blockagent.budget_alert import parse_budget_alert  # noqa: E402
from blockagent.config import load_config  # noqa: E402
from blockagent.dispatcher import dispatch  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeAzure:
    """In-memory simulation of the three target planes.

    Foundry is modelled on the **modern Agent Service API**: agents have a native
    ``state`` (enabled/disabled) toggled by the ``:disable`` / ``:enable`` action
    endpoints, and versions carry a full ``definition``.
    """

    def __init__(self):
        self.agent_state = {"asst_demo123": "enabled"}
        self.agent_metadata = {"asst_demo123": {}}
        self.agent_definition = {"asst_demo123": {"kind": "prompt", "model": "gpt-4.1-mini"}}
        self.sp_enabled = {"11111111-1111-1111-1111-111111111111": True}
        self.resource_tags = {}

    def _agent_body(self, agent_id):
        return {
            "id": agent_id,
            "state": self.agent_state.get(agent_id, "enabled"),
            "versions": {
                "latest": {
                    "definition": dict(self.agent_definition.get(agent_id, {})),
                    "metadata": dict(self.agent_metadata.get(agent_id, {})),
                }
            },
        }

    # --- generic dispatch used by all three fake `requests` shims ---
    def get(self, url, **kwargs):
        if "/agents/" in url:
            agent_id = url.rsplit("/agents/", 1)[1]
            return FakeResponse(self._agent_body(agent_id))
        if "/servicePrincipals/" in url:
            sp_id = url.rsplit("/servicePrincipals/", 1)[1]
            return FakeResponse({"id": sp_id, "accountEnabled": self.sp_enabled.get(sp_id, True)})
        if "/tags/default" in url:
            return FakeResponse({"properties": {"tags": dict(self.resource_tags)}})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, **kwargs):
        tail = url.rsplit("/agents/", 1)[1]
        # Native state action: /agents/{id}:disable | :enable
        if tail.endswith(":disable") or tail.endswith(":enable"):
            agent_id, verb = tail.rsplit(":", 1)
            self.agent_state[agent_id] = "disabled" if verb == "disable" else "enabled"
            return FakeResponse(self._agent_body(agent_id))
        # Fallback path: publish a new version (definition + metadata)
        agent_id = tail
        self.agent_metadata[agent_id] = dict(kwargs["json"]["metadata"])
        return FakeResponse(self._agent_body(agent_id))

    def patch(self, url, **kwargs):
        if "/servicePrincipals/" in url:
            sp_id = url.rsplit("/servicePrincipals/", 1)[1]
            self.sp_enabled[sp_id] = kwargs["json"]["accountEnabled"]
            return FakeResponse({})
        if "/tags/default" in url:
            self.resource_tags.update(kwargs["json"]["properties"]["tags"])
            return FakeResponse({})
        raise AssertionError(f"unexpected PATCH {url}")


class BlockAgentHarness(unittest.TestCase):
    def setUp(self):
        self.azure = FakeAzure()
        self.config = load_config()
        patches = []
        for module in ("foundry", "graph", "arm_tag"):
            patches.append(mock.patch(f"blockagent.mechanisms.{module}.requests", self.azure))
            patches.append(mock.patch(f"blockagent.mechanisms.{module}.get_token", return_value="fake-token"))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _load(self, name):
        return json.loads((SAMPLES / name).read_text())

    def test_parse_common_alert(self):
        alert = parse_budget_alert(self._load("common_alert.json"))
        self.assertEqual(alert.agent_id, "asst_demo123")
        self.assertEqual(alert.spend, 128.55)
        self.assertEqual(alert.budget, 100.0)
        self.assertEqual(alert.action, "block")

    def test_block_then_unblock_all_mechanisms(self):
        block_summary = dispatch(parse_budget_alert(self._load("simplified_block.json")), self.config)
        self.assertTrue(block_summary["allSucceeded"], block_summary)
        self.assertEqual(set(block_summary["mechanisms"]), {"foundry", "graph", "tag"})

        # State reflects a block
        self.assertEqual(self.azure.agent_state["asst_demo123"], "disabled")
        self.assertFalse(self.azure.sp_enabled["11111111-1111-1111-1111-111111111111"])
        self.assertEqual(self.azure.resource_tags["MS-AOAI-Feature-Assistants"], "Disabled")

        # Every mechanism reports itself reversible
        self.assertTrue(all(r["reversible"] for r in block_summary["results"]))

        # Now unblock and confirm prior state is restored
        unblock_summary = dispatch(parse_budget_alert(self._load("simplified_unblock.json")), self.config)
        self.assertTrue(unblock_summary["allSucceeded"], unblock_summary)
        self.assertEqual(self.azure.agent_state["asst_demo123"], "enabled")
        self.assertTrue(self.azure.sp_enabled["11111111-1111-1111-1111-111111111111"])
        self.assertEqual(self.azure.resource_tags["MS-AOAI-Feature-Assistants"], "Enabled")

    def test_single_mechanism_selection(self):
        payload = {"agentId": "asst_demo123", "mechanism": "graph"}
        summary = dispatch(parse_budget_alert(payload), self.config)
        self.assertEqual(summary["mechanisms"], ["graph"])
        self.assertTrue(summary["allSucceeded"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
