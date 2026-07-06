"""Dispatcher: routes a parsed budget alert to the chosen block mechanism(s).

Supports both the ``block`` and ``unblock`` directions, and can run a single
mechanism (foundry | graph | tag) or ``all`` mechanisms for POC comparison.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from .budget_alert import BudgetAlert
from .config import AgentTarget, Config
from .mechanisms import arm_tag, foundry, graph
from .mechanisms.base import BlockResult

logger = logging.getLogger("blockagent.dispatcher")

_MECHANISMS: Dict[str, Any] = {
    foundry.MECHANISM: foundry,
    graph.MECHANISM: graph,
    arm_tag.MECHANISM: arm_tag,
}


def available_mechanisms() -> List[str]:
    return list(_MECHANISMS.keys())


def _selected_mechanisms(alert: BudgetAlert, config: Config) -> List[str]:
    requested = (alert.mechanism or config.default_block_mechanism or "foundry").lower()
    if requested == "all":
        return available_mechanisms()
    if requested not in _MECHANISMS:
        raise ValueError(
            f"Unknown mechanism '{requested}'. Valid: {', '.join(available_mechanisms())} or 'all'"
        )
    return [requested]


def dispatch(alert: BudgetAlert, config: Config, credential=None) -> Dict[str, Any]:
    """Execute the block/unblock for the alert and return a JSON-serialisable summary."""
    if not alert.agent_id:
        raise ValueError("Could not determine agent id from the budget alert")

    target: AgentTarget = config.resolve_target(alert.agent_id)
    mechanisms = _selected_mechanisms(alert, config)
    reason = _reason(alert)

    results: List[Dict[str, Any]] = []
    for name in mechanisms:
        module = _MECHANISMS[name]
        op: Callable[..., BlockResult] = module.unblock if alert.is_unblock else module.block
        try:
            if alert.is_unblock:
                result = op(config, target, credential=credential)
            else:
                result = op(config, target, reason=reason, credential=credential)
        except Exception as exc:  # noqa: BLE001 - surface per-mechanism failure without aborting others
            logger.exception("Mechanism '%s' failed", name)
            result = BlockResult(name, "unblock" if alert.is_unblock else "block", success=False, detail=str(exc))
        results.append(result.to_dict())

    return {
        "action": "unblock" if alert.is_unblock else "block",
        "agentId": alert.agent_id,
        "budgetName": alert.budget_name,
        "spend": alert.spend,
        "budget": alert.budget,
        "mechanisms": mechanisms,
        "results": results,
        "allSucceeded": all(r["success"] for r in results),
    }


def _reason(alert: BudgetAlert) -> str:
    parts = ["budget exceeded"]
    if alert.budget_name:
        parts.append(f"budget={alert.budget_name}")
    if alert.spend is not None and alert.budget is not None:
        parts.append(f"spend={alert.spend}/{alert.budget}")
    return "; ".join(parts)
