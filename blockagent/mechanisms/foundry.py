"""Mechanism A - Azure AI Foundry REST API.

Blocks an agent on ``agent-verse-resource`` **non-destructively**: the agent is
never deleted. Instead we set a reversible ``blocked`` flag in the agent's
``metadata`` (and record the reason). Unblock clears the flag.

Foundry has no first-class per-agent "disabled" state, so this metadata flag is
how the POC represents a block; enforcement is expected at the calling gateway
(e.g. APIM) or client, which checks ``metadata.blocked`` before invoking the
agent. The important property for this POC is that the operation is fully
reversible and touches only metadata.
"""
from __future__ import annotations

from typing import Any, Dict

import requests

from ..auth import FOUNDRY_SCOPE, get_token
from ..config import AgentTarget, Config
from .base import BlockResult

MECHANISM = "foundry"
BLOCKED_KEY = "blocked"
REASON_KEY = "blocked_reason"


def _base_url(config: Config) -> str:
    return config.foundry_project_endpoint.rstrip("/")


def _headers(config: Config, credential=None) -> Dict[str, str]:
    token = get_token(config, FOUNDRY_SCOPE, credential)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_agent(config: Config, agent_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    url = f"{_base_url(config)}/agents/{agent_id}"
    resp = requests.get(url, headers=headers, params={"api-version": config.foundry_api_version}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _update_metadata(config: Config, agent_id: str, metadata: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    # OpenAI-compatible assistants/agents API modifies via POST to the agent id.
    url = f"{_base_url(config)}/agents/{agent_id}"
    resp = requests.post(
        url,
        headers=headers,
        params={"api-version": config.foundry_api_version},
        json={"metadata": metadata},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _apply(config: Config, target: AgentTarget, block: bool, reason: str, credential=None) -> BlockResult:
    action = "block" if block else "unblock"
    agent_id = target.foundry_agent_id or target.agent_id
    if not agent_id:
        return BlockResult(MECHANISM, action, success=False, detail="No Foundry agent id resolved for target")

    headers = _headers(config, credential)
    current = _get_agent(config, agent_id, headers)
    metadata: Dict[str, Any] = dict(current.get("metadata") or {})
    previous = {BLOCKED_KEY: metadata.get(BLOCKED_KEY, "false")}

    metadata[BLOCKED_KEY] = "true" if block else "false"
    if block:
        metadata[REASON_KEY] = reason
    else:
        metadata.pop(REASON_KEY, None)

    _update_metadata(config, agent_id, metadata, headers)
    return BlockResult(
        MECHANISM,
        action,
        success=True,
        reversible=True,
        detail=f"Set metadata.{BLOCKED_KEY}={metadata[BLOCKED_KEY]} on Foundry agent '{agent_id}'",
        previous_state=previous,
    )


def block(config: Config, target: AgentTarget, reason: str = "budget exceeded", credential=None) -> BlockResult:
    return _apply(config, target, block=True, reason=reason, credential=credential)


def unblock(config: Config, target: AgentTarget, credential=None) -> BlockResult:
    return _apply(config, target, block=False, reason="", credential=credential)
