"""Mechanism B - Microsoft Entra (Graph) service principal disable.

This is the closest programmatic equivalent to the Microsoft 365 Admin Center
"Block" action for agent-builder / published agents: the agent is backed by an
Entra service principal, and blocking it sets ``accountEnabled=false`` so it can
no longer sign in or obtain tokens. Fully reversible via ``accountEnabled=true``.

Non-destructive: the service principal is never deleted, and no permission
grants or app role assignments are removed.
"""
from __future__ import annotations

from typing import Any, Dict

import requests

from ..auth import get_token
from ..config import AgentTarget, Config
from .base import BlockResult

MECHANISM = "graph"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _headers(config: Config, credential=None) -> Dict[str, str]:
    token = get_token(config, config.graph_scope, credential)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_sp(sp_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE}/servicePrincipals/{sp_id}"
    resp = requests.get(url, headers=headers, params={"$select": "id,displayName,accountEnabled"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _patch_enabled(sp_id: str, enabled: bool, headers: Dict[str, str]) -> None:
    url = f"{GRAPH_BASE}/servicePrincipals/{sp_id}"
    resp = requests.patch(url, headers=headers, json={"accountEnabled": enabled}, timeout=30)
    resp.raise_for_status()


def _apply(config: Config, target: AgentTarget, block: bool, credential=None) -> BlockResult:
    action = "block" if block else "unblock"
    sp_id = target.service_principal_id
    if not sp_id:
        return BlockResult(
            MECHANISM,
            action,
            success=False,
            detail="No service_principal_id mapped for this agent",
        )

    headers = _headers(config, credential)
    current = _get_sp(sp_id, headers)
    previous = {"accountEnabled": current.get("accountEnabled")}

    _patch_enabled(sp_id, enabled=(not block), headers=headers)
    return BlockResult(
        MECHANISM,
        action,
        success=True,
        reversible=True,
        detail=f"Set accountEnabled={not block} on service principal '{sp_id}'",
        previous_state=previous,
    )


def block(config: Config, target: AgentTarget, reason: str = "budget exceeded", credential=None) -> BlockResult:
    return _apply(config, target, block=True, credential=credential)


def unblock(config: Config, target: AgentTarget, credential=None) -> BlockResult:
    return _apply(config, target, block=False, credential=credential)
