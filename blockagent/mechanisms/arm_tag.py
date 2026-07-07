"""Mechanism C - ARM tag on the Foundry (Cognitive Services) account.

Sets the documented feature tag ``MS-AOAI-Feature-Assistants=Disabled`` on
the target Foundry account, which blocks create/update/run operations for classic
assistants on that account. This is deliberately **blunt**: it affects every
classic assistant on the resource, not a single agent. Included only so the POC
can compare it against the per-agent mechanisms.

Fully reversible: unblock sets the tag back to ``Enabled``. Existing agents are
never deleted or modified.
"""
from __future__ import annotations

from typing import Any, Dict

import requests

from ..auth import ARM_SCOPE, get_token
from ..config import AgentTarget, Config
from .base import BlockResult

MECHANISM = "tag"
ARM_BASE = "https://management.azure.com"
TAG_KEY = "MS-AOAI-Feature-Assistants"
TAG_API_VERSION = "2022-09-01"


def _headers(config: Config, credential=None) -> Dict[str, str]:
    token = get_token(config, ARM_SCOPE, credential)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _tags_url(config: Config) -> str:
    return f"{ARM_BASE}{config.foundry_account_resource_id}/providers/Microsoft.Resources/tags/default"


def _get_tags(config: Config, headers: Dict[str, str]) -> Dict[str, Any]:
    resp = requests.get(_tags_url(config), headers=headers, params={"api-version": TAG_API_VERSION}, timeout=30)
    resp.raise_for_status()
    return (resp.json().get("properties") or {}).get("tags") or {}


def _merge_tag(config: Config, value: str, headers: Dict[str, str]) -> None:
    body = {"operation": "Merge", "properties": {"tags": {TAG_KEY: value}}}
    resp = requests.patch(_tags_url(config), headers=headers, params={"api-version": TAG_API_VERSION}, json=body, timeout=30)
    resp.raise_for_status()


def _apply(config: Config, block: bool, credential=None) -> BlockResult:
    action = "block" if block else "unblock"
    headers = _headers(config, credential)
    existing = _get_tags(config, headers)
    previous = {TAG_KEY: existing.get(TAG_KEY, "Enabled")}

    value = "Disabled" if block else "Enabled"
    _merge_tag(config, value, headers)
    return BlockResult(
        MECHANISM,
        action,
        success=True,
        reversible=True,
        detail=f"Set tag {TAG_KEY}={value} on '{config.foundry_account_name}' (affects all classic assistants)",
        previous_state=previous,
    )


def block(config: Config, target: AgentTarget, reason: str = "budget exceeded", credential=None) -> BlockResult:
    return _apply(config, block=True, credential=credential)


def unblock(config: Config, target: AgentTarget, credential=None) -> BlockResult:
    return _apply(config, block=False, credential=credential)
