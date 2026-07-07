"""Configuration loading for the block-agent function.

All configuration comes from environment variables (Function App settings).
No secrets are hard-coded; for local development an app registration can be
supplied, but in Azure a Managed Identity is expected.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class AgentTarget:
    """Resolves an alerting agent id to the identifiers each mechanism needs."""

    agent_id: str
    foundry_agent_id: Optional[str] = None
    service_principal_id: Optional[str] = None


@dataclass
class Config:
    subscription_id: str
    resource_group: str
    foundry_account_name: str

    foundry_project_endpoint: str
    foundry_api_version: str

    graph_scope: str
    default_block_mechanism: str

    tenant_id: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]

    agent_target_map: Dict[str, AgentTarget] = field(default_factory=dict)

    @property
    def foundry_account_resource_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{self.foundry_account_name}"
        )

    def resolve_target(self, agent_id: str) -> AgentTarget:
        """Return the mapped target, falling back to using the agent id directly."""
        if agent_id in self.agent_target_map:
            return self.agent_target_map[agent_id]
        # Sensible fallback: assume the alert's agent id is also the Foundry agent id.
        return AgentTarget(agent_id=agent_id, foundry_agent_id=agent_id)


def _parse_agent_map(raw: str) -> Dict[str, AgentTarget]:
    if not raw:
        return {}
    data = json.loads(raw)
    targets: Dict[str, AgentTarget] = {}
    for agent_id, entry in data.items():
        targets[agent_id] = AgentTarget(
            agent_id=agent_id,
            foundry_agent_id=entry.get("foundry_agent_id", agent_id),
            service_principal_id=entry.get("service_principal_id"),
        )
    return targets


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        subscription_id=os.environ.get("AZURE_SUBSCRIPTION_ID", ""),
        resource_group=os.environ.get("AZURE_RESOURCE_GROUP", ""),
        foundry_account_name=os.environ.get("FOUNDRY_ACCOUNT_NAME", ""),
        foundry_project_endpoint=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""),
        foundry_api_version=os.environ.get("FOUNDRY_API_VERSION", "v1"),
        graph_scope=os.environ.get("GRAPH_SCOPE", "https://graph.microsoft.com/.default"),
        default_block_mechanism=os.environ.get("DEFAULT_BLOCK_MECHANISM", "foundry").lower(),
        tenant_id=os.environ.get("AZURE_TENANT_ID") or None,
        client_id=os.environ.get("AZURE_CLIENT_ID") or None,
        client_secret=os.environ.get("AZURE_CLIENT_SECRET") or None,
        agent_target_map=_parse_agent_map(os.environ.get("AGENT_TARGET_MAP", "")),
    )
