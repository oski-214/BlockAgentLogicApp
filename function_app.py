"""Azure Functions entrypoint (Python v2 programming model).

Receives an Azure Cost Management budget alert (via an Action Group webhook) and
blocks the offending agent on ``agent-verse-resource``. The same endpoint also
handles the ``unblock`` direction so a blocked agent can be restored.

Endpoints:
  POST /api/budget-alert   -> block (or unblock) driven by the alert payload
  GET  /api/health         -> liveness probe
"""
from __future__ import annotations

import json
import logging

import azure.functions as func

from blockagent.budget_alert import parse_budget_alert
from blockagent.config import load_config
from blockagent.dispatcher import available_mechanisms, dispatch

app = func.FunctionApp()
logger = logging.getLogger("blockagent.function")


@app.route(route="budget-alert", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def budget_alert(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return _json({"error": "Request body must be valid JSON"}, status=400)

    try:
        alert = parse_budget_alert(payload)
    except ValueError as exc:
        return _json({"error": str(exc)}, status=400)

    if not alert.agent_id:
        return _json(
            {
                "error": "Could not determine an agent id from the alert. Provide 'agentId', "
                "or name the budget 'budget-<agentId>', or set alertContext.AgentId.",
            },
            status=422,
        )

    config = load_config()
    try:
        summary = dispatch(alert, config)
    except ValueError as exc:
        return _json({"error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error handling budget alert")
        return _json({"error": "internal error", "detail": str(exc)}, status=500)

    status = 200 if summary.get("allSucceeded") else 207
    return _json(summary, status=status)


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json({"status": "ok", "mechanisms": available_mechanisms()})


def _json(body: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body, indent=2, default=str),
        status_code=status,
        mimetype="application/json",
    )
