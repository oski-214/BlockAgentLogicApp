"""Mechanism A - Azure AI Foundry Agent Service REST API (modern /agents API).

Blocks an agent on the target Foundry account **non-destructively and reversibly**.

Foundry agents published in a Foundry project live in the modern **Foundry
Agent Service** (persistent agents at ``/agents`` with ``api-version=v1``). Each
agent has a first-class ``state`` field (``enabled`` / ``disabled``) and versions
carrying a full ``definition`` object.

This mechanism uses the **native state action** as the primary block:

    POST /agents/{id}:disable?api-version=v1   -> state = "disabled"
    POST /agents/{id}:enable?api-version=v1    -> state = "enabled"

This is the strongest and cleanest block: it is enforced by the service itself
(not a mere metadata hint), fully reversible, and deletes nothing.

If the deployment targets an older API surface that does not expose the state
actions (``404``/``405``), we transparently fall back to the legacy behaviour: a
reversible ``blocked`` flag written into the agent's ``metadata`` by publishing a
new version that **preserves the existing ``definition``** (the modern API rejects
metadata-only updates with ``400 required: definition``). That flag is advisory
and must be enforced by the calling gateway (e.g. APIM) or client.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..auth import FOUNDRY_SCOPE, get_token
from ..config import AgentTarget, Config
from .base import BlockResult

MECHANISM = "foundry"
BLOCKED_KEY = "blocked"
REASON_KEY = "blocked_reason"

STATE_ENABLED = "enabled"
STATE_DISABLED = "disabled"


def _base_url(config: Config) -> str:
    return config.foundry_project_endpoint.rstrip("/")


def _headers(config: Config, credential=None) -> Dict[str, str]:
    token = get_token(config, FOUNDRY_SCOPE, credential)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _params(config: Config) -> Dict[str, str]:
    return {"api-version": config.foundry_api_version}


def _raise_with_body(resp: requests.Response) -> None:
    """Like ``raise_for_status`` but include the response body in the message.

    Foundry data-plane errors (e.g. ``403``) carry the real reason in the body
    (missing role/action, wrong token audience, ...). The default requests
    exception only shows the status code, which hides that detail.
    """
    if resp.status_code >= 400:
        body = (resp.text or "").strip()
        raise requests.HTTPError(
            f"{resp.status_code} {resp.reason} for {resp.url} :: {body[:800]}",
            response=resp,
        )


def _get_agent(config: Config, agent_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    url = f"{_base_url(config)}/agents/{agent_id}"
    resp = requests.get(url, headers=headers, params=_params(config), timeout=30)
    _raise_with_body(resp)
    return resp.json()


def _state_action(config: Config, agent_id: str, verb: str, headers: Dict[str, str]) -> requests.Response:
    """Invoke the native ``:enable`` / ``:disable`` action endpoint.

    Returns the raw response so the caller can inspect the status code and decide
    whether to fall back (on 404/405) without treating it as a hard failure.
    """
    url = f"{_base_url(config)}/agents/{agent_id}:{verb}"
    return requests.post(url, headers=headers, params=_params(config), json={}, timeout=30)


def _latest_definition(agent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    latest = (agent.get("versions") or {}).get("latest") or {}
    return latest.get("definition")


def _latest_metadata(agent: Dict[str, Any]) -> Dict[str, Any]:
    latest = (agent.get("versions") or {}).get("latest") or {}
    return dict(latest.get("metadata") or {})


def _publish_metadata_version(
    config: Config,
    agent_id: str,
    definition: Dict[str, Any],
    metadata: Dict[str, Any],
    headers: Dict[str, str],
) -> Dict[str, Any]:
    """Fallback: publish a new agent version preserving ``definition``.

    The modern API rejects metadata-only updates, so we must resend the full
    ``definition`` when writing the reversible ``blocked`` flag into metadata.
    """
    url = f"{_base_url(config)}/agents/{agent_id}"
    resp = requests.post(
        url,
        headers=headers,
        params=_params(config),
        json={"definition": definition, "metadata": metadata},
        timeout=30,
    )
    _raise_with_body(resp)
    return resp.json()


def _apply(config: Config, target: AgentTarget, block: bool, reason: str, credential=None) -> BlockResult:
    action = "block" if block else "unblock"
    agent_id = target.foundry_agent_id or target.agent_id
    if not agent_id:
        return BlockResult(MECHANISM, action, success=False, detail="No Foundry agent id resolved for target")

    headers = _headers(config, credential)
    current = _get_agent(config, agent_id, headers)
    prev_state = current.get("state")

    verb = "disable" if block else "enable"
    resp = _state_action(config, agent_id, verb, headers)

    if resp.status_code in (404, 405):
        # Older API surface without native state actions -> metadata-flag fallback.
        return _apply_metadata_fallback(config, agent_id, current, block, reason, headers)

    _raise_with_body(resp)

    after = _get_agent(config, agent_id, headers)
    new_state = after.get("state")
    expected = STATE_DISABLED if block else STATE_ENABLED
    return BlockResult(
        MECHANISM,
        action,
        success=(new_state == expected),
        reversible=True,
        detail=(
            f"Native state action :{verb} on Foundry agent '{agent_id}' "
            f"-> state={new_state} (was {prev_state})"
        ),
        previous_state={"state": prev_state},
    )


def _apply_metadata_fallback(
    config: Config,
    agent_id: str,
    current: Dict[str, Any],
    block: bool,
    reason: str,
    headers: Dict[str, str],
) -> BlockResult:
    action = "block" if block else "unblock"
    definition = _latest_definition(current)
    if not definition:
        return BlockResult(
            MECHANISM,
            action,
            success=False,
            detail=f"Cannot fall back to metadata flag: no definition found for agent '{agent_id}'",
        )

    metadata = _latest_metadata(current)
    previous = {BLOCKED_KEY: metadata.get(BLOCKED_KEY, "false")}

    metadata[BLOCKED_KEY] = "true" if block else "false"
    if block:
        metadata[REASON_KEY] = reason
    else:
        metadata.pop(REASON_KEY, None)

    _publish_metadata_version(config, agent_id, definition, metadata, headers)
    return BlockResult(
        MECHANISM,
        action,
        success=True,
        reversible=True,
        detail=(
            f"Native state action unavailable; published new version with "
            f"metadata.{BLOCKED_KEY}={metadata[BLOCKED_KEY]} on Foundry agent "
            f"'{agent_id}' (advisory flag, enforce at gateway)"
        ),
        previous_state=previous,
    )


def block(config: Config, target: AgentTarget, reason: str = "budget exceeded", credential=None) -> BlockResult:
    return _apply(config, target, block=True, reason=reason, credential=credential)


def unblock(config: Config, target: AgentTarget, credential=None) -> BlockResult:
    return _apply(config, target, block=False, reason="", credential=credential)
