# Block agent on budget exceeded — Azure Functions POC

Proof-of-concept that blocks an Azure AI Foundry agent (on the
`agent-verse-resource` account) when a budget is exceeded, using an Azure
Function triggered by an Azure Cost Management budget alert.

It compares **three reversible block mechanisms** so we can decide which is the
right long-term approach.

---

## 1. Feasibility findings (read this first)

**Can we automate the Microsoft 365 Admin Center "Block agent" button when a
budget is exceeded?**

- **Not literally.** The Admin Center *Agents & connectors* "Block" action has
  **no documented public automation API**. The unified **Agent 365** registry
  (which does inventory Foundry agents alongside Copilot Studio agents in the
  Admin Center) is brand new / preview and is not a stable automation surface.
- **An equivalent automated block IS feasible** and is what this POC implements.
- **Budget granularity caveat:** Azure Cost Management budgets scope to a
  subscription / resource group / resource / tag — **not to a single agent**
  inside `agent-verse-resource`. True per-agent budgets require custom token
  metering (App Insights / Log Analytics), which is noted as future work.

**"Is it possible only for agent builder?"** The closest true equivalent to the
Admin Center block is disabling the agent's **Entra service principal**
(mechanism **B**), which applies to agent-builder / published agents. Foundry
agents are best handled by the Foundry REST API (mechanism **A**).

### The three mechanisms (all reversible, never destructive)

| # | Mechanism | Block action | Unblock action | Scope | Notes |
|---|-----------|--------------|----------------|-------|-------|
| A | **Foundry REST API** | set `metadata.blocked=true` on the agent | set `metadata.blocked=false` | single agent | Foundry has no first-class per-agent "disabled" state; the flag is enforced by the calling gateway/client. Never deletes. |
| B | **Entra Graph** | `servicePrincipal accountEnabled=false` | `accountEnabled=true` | single agent identity | Closest equivalent to the Admin Center block. No SP/grant deletion. |
| C | **ARM tag** | tag `MS-AOAI-Feature-Assistants=Disabled` | tag `=Enabled` | **whole resource** | Blunt: disables *all* classic assistants on `agent-verse-resource`. For comparison only. |

> **Hard rule:** No mechanism ever deletes an agent, service principal, or
> permission grant. Every block is reversible and captures the prior state.

---

## 2. Architecture

```
Azure Cost Management Budget (scope: agent-verse-resource / its RG)
        │  threshold reached
        ▼
   Action Group (webhook, common alert schema)
        │  budget alert JSON
        ▼
   Azure Function  POST /api/budget-alert   (this repo)
        │  parse alert → resolve agent → dispatch
        ├─ A) Foundry REST   (metadata.blocked)
        ├─ B) Entra Graph     (accountEnabled=false)
        └─ C) ARM tag         (MS-AOAI-Feature-Assistants=Disabled)
```

---

## 3. Project layout

```
function_app.py            # Azure Functions v2 entrypoint (HTTP routes)
host.json                  # Functions host config
requirements.txt           # Python dependencies
local.settings.json.example# Copy to local.settings.json for local dev
blockagent/
  config.py                # Env-driven config + agentId→targets mapping
  auth.py                  # Managed Identity / app-registration token helpers
  budget_alert.py          # Parses common-alert-schema OR simplified payloads
  dispatcher.py            # Routes alert → mechanism(s), block/unblock
  mechanisms/
    base.py                # BlockResult
    foundry.py             # Mechanism A
    graph.py               # Mechanism B
    arm_tag.py             # Mechanism C
samples/                   # Example alert payloads
tests/test_harness.py      # Offline test: block → verify → unblock restores
```

---

## 4. Endpoints

| Method | Route | Auth | Purpose |
|--------|-------|------|---------|
| `POST` | `/api/budget-alert` | function key | Block (or unblock) driven by the alert payload |
| `GET`  | `/api/health` | anonymous | Liveness + list mechanisms |

### Request payload shapes

Common Alert Schema (real budget alert) — see `samples/common_alert.json`.

Simplified / manual (testing) — see `samples/simplified_block.json`:

```json
{ "agentId": "asst_demo123", "spend": 128.55, "budget": 100,
  "action": "block", "mechanism": "all" }
```

- `action`: `block` (default) or `unblock`.
- `mechanism`: `foundry` | `graph` | `tag` | `all` (defaults to
  `DEFAULT_BLOCK_MECHANISM`).
- Agent id resolution order: `agentId` field → `alertContext.AgentId` → a budget
  named `budget-<agentId>`.

---

## 5. Configuration (Function App settings)

See `local.settings.json.example`. Key settings:

| Setting | Purpose |
|---------|---------|
| `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `FOUNDRY_ACCOUNT_NAME` | Locate `agent-verse-resource` (mechanism C) |
| `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_API_VERSION` | Foundry data-plane (mechanism A) |
| `GRAPH_SCOPE` | Graph audience (mechanism B) |
| `AGENT_TARGET_MAP` | JSON: `agentId → { foundry_agent_id, service_principal_id }` |
| `DEFAULT_BLOCK_MECHANISM` | Fallback mechanism when the alert doesn't specify one |
| `AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET` | **Local dev only.** In Azure use a Managed Identity — leave blank. |

### Required permissions (least privilege)

- **Mechanism A (Foundry):** data-plane role on `agent-verse-resource`
  (e.g. *Azure AI Developer* / *Cognitive Services User*) allowing agent update.
- **Mechanism B (Graph):** application permission `Application.ReadWrite.All`
  (to PATCH `servicePrincipal.accountEnabled`).
- **Mechanism C (ARM tag):** `Microsoft.Resources/tags/write` on the resource
  (e.g. *Tag Contributor* or *Contributor* scoped to `agent-verse-resource`).

Grant these to the Function App's **system-assigned Managed Identity**.

---

## 6. Run & test locally

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Offline test: blocks then verifies unblock restores prior state (no Azure needed)
.venv\Scripts\python.exe -m tests.test_harness
```

To run the Function host locally you also need
[Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local):

```powershell
copy local.settings.json.example local.settings.json   # then fill in values
func start
# in another shell:
curl -X POST http://localhost:7071/api/budget-alert -H "Content-Type: application/json" -d "@samples/simplified_block.json"
```

---

## 7. Wire up the real budget trigger

1. Create an **Action Group** with a **Webhook** action pointing at
   `https://<app>.azurewebsites.net/api/budget-alert?code=<function-key>`
   and enable the **common alert schema**.
2. Create a **Cost Management budget** scoped to `agent-verse-resource` (or its
   resource group). Name it `budget-<agentId>` so the agent id is carried in the
   alert, or add the agent id via the alert context.
3. Add the Action Group to the budget's alert conditions (e.g. at 90%/100%).

> Reminder: budgets can't target one agent inside the resource. For per-agent
> enforcement, meter tokens per agent and drive `POST /api/budget-alert`
> yourself from a scheduled query.

---

## 8. Unblocking

Send the same payload with `"action": "unblock"` (see
`samples/simplified_unblock.json`). Each mechanism restores the prior state:
Foundry `metadata.blocked=false`, Graph `accountEnabled=true`, ARM tag
`=Enabled`. Nothing is ever deleted.
