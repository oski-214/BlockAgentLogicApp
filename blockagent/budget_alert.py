"""Parsing of Azure Cost Management budget alert payloads.

Budget alerts reach us in one of two shapes:

1. Common Alert Schema (when the budget's Action Group uses a webhook with the
   common schema enabled) -> ``{"schemaId": "...CommonAlertSchema", "data": {...}}``.
2. A simplified/manual payload used for the POC and local testing ->
   ``{"agentId": "...", "budgetName": "...", "spend": 123, "budget": 100,
     "action": "block"|"unblock", "mechanism": "foundry"|"graph"|"tag"}``.

The parser is deliberately tolerant so the same function handles a real alert
and a hand-crafted test payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class BudgetAlert:
    agent_id: Optional[str]
    budget_name: Optional[str]
    spend: Optional[float]
    budget: Optional[float]
    # "block" (default) or "unblock" so the same endpoint can restore an agent.
    action: str = "block"
    # Optional per-alert override of the block mechanism.
    mechanism: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    @property
    def is_unblock(self) -> bool:
        return self.action.lower() == "unblock"


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_agent_id(*candidates: Any) -> Optional[str]:
    """Return the first non-empty string candidate."""
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def parse_budget_alert(payload: Dict[str, Any]) -> BudgetAlert:
    """Parse either a common-alert-schema or a simplified budget payload."""
    if not isinstance(payload, dict):
        raise ValueError("Alert payload must be a JSON object")

    # ---- Common Alert Schema -------------------------------------------------
    schema_id = str(payload.get("schemaId", ""))
    if "data" in payload and ("CommonAlertSchema" in schema_id or "essentials" in payload.get("data", {})):
        data = payload["data"]
        essentials = data.get("essentials", {}) or {}
        alert_context = data.get("alertContext", {}) or {}

        budget_name = essentials.get("alertRule") or alert_context.get("BudgetName")
        spend = _to_float(alert_context.get("SpendingAmount") or alert_context.get("CurrentSpend"))
        budget = _to_float(alert_context.get("BudgetThreshold") or alert_context.get("BudgetAmount"))

        # The agent id is conveyed either in the alert context (custom field),
        # the budget name (e.g. "budget-<agentId>"), or a tag/filter value.
        agent_id = _extract_agent_id(
            alert_context.get("AgentId"),
            _agent_id_from_budget_name(budget_name),
        )

        return BudgetAlert(
            agent_id=agent_id,
            budget_name=budget_name,
            spend=spend,
            budget=budget,
            action=str(alert_context.get("Action", "block")),
            mechanism=alert_context.get("Mechanism"),
            raw=payload,
        )

    # ---- Simplified / manual payload ----------------------------------------
    agent_id = _extract_agent_id(
        payload.get("agentId"),
        payload.get("agent_id"),
        _agent_id_from_budget_name(payload.get("budgetName") or payload.get("budget_name")),
    )
    return BudgetAlert(
        agent_id=agent_id,
        budget_name=payload.get("budgetName") or payload.get("budget_name"),
        spend=_to_float(payload.get("spend")),
        budget=_to_float(payload.get("budget")),
        action=str(payload.get("action", "block")),
        mechanism=payload.get("mechanism"),
        raw=payload,
    )


def _agent_id_from_budget_name(budget_name: Optional[str]) -> Optional[str]:
    """Convention: budgets named ``budget-<agentId>`` encode the agent id."""
    if not budget_name:
        return None
    prefix = "budget-"
    if budget_name.startswith(prefix) and len(budget_name) > len(prefix):
        return budget_name[len(prefix):]
    return None
