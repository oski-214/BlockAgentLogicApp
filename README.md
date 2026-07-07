# Block an agent when its budget is exceeded — Azure Function

A solution that **automatically blocks an Azure AI Foundry agent when a token
budget is exceeded**, with no manual intervention. It is the automated equivalent
of the Microsoft 365 Admin Center **"Block agent"** button (which today has **no**
public API).

When an agent's token spend/usage crosses the threshold, an Azure Monitor alert
calls an Azure Function that **disables the agent** through one of three reversible
mechanisms.

> 🎬 Want to see the scenarios in action? Go to **[`DEMO.md`](DEMO.md)**.
> To test the logic without deploying anything, see **[`TESTING.md`](TESTING.md)**.

---

## 1. Can the Admin Center "Block agent" button be automated?

- **Not literally.** The Admin Center *Agents & connectors* *Block* action has
  **no documented public API**. The unified **Agent 365** registry is in preview
  and is not a stable automation surface.
- **An equivalent, automated block IS possible**, which is what this repo
  implements.
- **Budget granularity:** *Cost Management* budgets are scoped to a subscription /
  resource group / resource / tag, **not to a single agent**. That is why we use a
  **metric alert on the Foundry account's `TotalTokens`** (closer to the agent's
  actual usage). Strictly per-agent budgets would require per-agent token metering
  (App Insights / Log Analytics) — noted as future work.

---

## 2. The three mechanisms (all reversible, never destructive)

| # | Mechanism | Block | Unblock | Scope | Note |
|---|-----------|-------|---------|-------|------|
| **A** | **Foundry native state** | `POST /agents/{id}:disable` → `state=disabled` | `:enable` → `state=enabled` | single agent | **Recommended**. Enforced by the service itself. The managed identity does it **without Global Admin**. |
| **B** | **Entra identity** | `servicePrincipal accountEnabled=false` | `accountEnabled=true` | agent identity | Cuts access at the identity level. For Foundry agent identities (preview) it **requires Global Admin**. |
| **C** | **ARM tag** | tag `MS-AOAI-Feature-Assistants=Disabled` | tag `=Enabled` | **whole account** | Blunt: affects *all* classic assistants on the account. For comparison only. |

> **Hard rule:** no mechanism ever deletes the agent, its identity, or permission
> grants. Every block is reversible and captures the previous state.

### Mechanism A: native state (not a metadata flag)

Agents in the **Foundry Agent Service** (modern `/agents` API, `api-version=v1`)
have a first-class `state` field (`enabled`/`disabled`). The primary block uses the
**native state actions**:

```
POST {project-endpoint}/agents/{id}:disable?api-version=v1   → state = "disabled"
POST {project-endpoint}/agents/{id}:enable?api-version=v1    → state = "enabled"
```

If the environment targeted an older API without these actions (`404`/`405`), there
is a **fallback** that publishes a new version with `metadata.blocked=true`
**preserving the `definition`** (the modern API rejects metadata-only updates with
`400 required: definition`). That flag is advisory (a gateway/client must enforce
it); the primary, tested path is the native state.

### Classic Assistant or New Agent? How to tell them apart

| | **Classic Assistant** | **New Agent (Agent Service)** |
|--|----------------------|-------------------------------|
| API | `/assistants` (OpenAI Assistants style) | `/agents` with `api-version=v1` |
| State | no `state`; only `metadata` | has `state` (`enabled`/`disabled`) and `versions` with `definition` |
| Block A | `metadata.blocked` flag (advisory) | native `:disable`/`:enable` action (enforced) |
| Entra identity | regular service principal | `servicePrincipal` of type `agentIdentity` (preview, more protected) |

Quick rule: **if `GET /agents/{id}?api-version=v1` returns `state` and `versions`,
it is a New Agent**. If it only exists under `/assistants` and has no `state`, it is
classic.

---

## 3. Architecture

```
Azure Monitor metric alert  (scope: Foundry account, metric TotalTokens)
        │  threshold exceeded
        ▼
   Action Group (webhook, common alert schema)
        │  alert JSON
        ▼
   Azure Function   POST /api/budget-alert   (this repo)
        │  parse alert → resolve agent → dispatch
        ├─ A) Foundry REST   (:disable / :enable)   ← recommended
        ├─ B) Entra Graph     (accountEnabled=false)
        └─ C) ARM tag         (MS-AOAI-Feature-Assistants=Disabled)
```

The Function uses its **system-assigned managed identity** to request tokens and
call each API. No secrets live in the code.

---

## 4. What the Bicep deploys (everything from scratch)

`deploy/main.bicep` creates **all** the infrastructure, generic and self-contained:

| Resource | Purpose |
|----------|---------|
| **Azure AI Foundry account** (`Microsoft.CognitiveServices/accounts`, kind `AIServices`) + **project** | Hosts the agents. `allowProjectManagement` + subdomain for the `<name>.services.ai.azure.com` endpoint. It does **not** deploy a model or an agent (you do that). |
| **Storage** (no shared key, identity-based) | Deployment package + runtime state |
| **Flex Consumption plan + Function App (Python 3.11)** + **managed identity** | Runs the blocking logic |
| **Log Analytics + Application Insights** | Function traces/telemetry |
| **Action Group** (webhook → `/api/budget-alert`, common alert schema) | Bridges alert → Function |
| **Metric alert** (`TotalTokens` on the Foundry account) | Fires the block when the threshold is exceeded |
| **Role assignments** (mechanisms A and C + storage) | Least privilege, already in the template |

> **Mechanism B (Graph `Application.ReadWrite.All`)** is **out** of the Bicep: it
> needs **Global Admin** consent → granted with
> `deploy/grant-graph-permission.ps1`.

---

## 5. Prerequisites

- `az login` with rights to create resources and **assign roles**.
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local) (`func`).
- Bicep (`az bicep`).

---

## 6. Deployment

### 6.1 Fill in the parameters

Edit `deploy/main.parameters.json` with **globally unique names** (Function App,
storage, and Foundry account must be unique):

```jsonc
{
  "functionAppName":    { "value": "fa-blockagent-myorg" },
  "storageAccountName": { "value": "stblkagentmyorg" },
  "foundryAccountName": { "value": "aif-blockagent-myorg" },
  "foundryProjectName": { "value": "block-agent-project" },
  "foundryApiVersion":  { "value": "v1" },
  "agentTargetMap":     { "value": "{}" },
  "defaultBlockMechanism": { "value": "foundry" },
  "budgetTokenThreshold":  { "value": 1000 }
}
```

`agentTargetMap` starts **empty** (`{}`): you fill it in after creating the agent.

### 6.2 Validate with what-if (deploys nothing)

```powershell
az group create --name rg-block-agent --location swedencentral

az deployment group what-if `
  --resource-group rg-block-agent `
  --template-file deploy/main.bicep `
  --parameters "@deploy/main.parameters.json"
```

Check that the list of resources to create matches expectations.

### 6.3 Deploy and publish the code

```powershell
./deploy/deploy.ps1 -ResourceGroup rg-block-agent -Location swedencentral
```

The script deploys the Bicep (infra + roles) and publishes the Python code
(`func azure functionapp publish <app> --python`). Health check:

```
GET https://<functionAppName>.azurewebsites.net/api/health
→ {"status":"ok","mechanisms":["foundry","graph","tag"]}
```

### 6.4 (Optional) Mechanism B — Graph consent

```powershell
./deploy/grant-graph-permission.ps1 -PrincipalId <managed-identity-objectId>
```

Requires **Global Admin**. The `objectId` comes from the deployment outputs
(`managedIdentityPrincipalId`).

---

## 7. Manual steps in Foundry (after deploying)

The Bicep creates the Foundry **account and project**, but **not** the model or the
agent. Create them in the Foundry portal:

1. **Deploy a model** in the Foundry account (e.g. `gpt-4o-mini`).
2. **Create an agent** in the project using that model.
3. **Copy the agent ID**.
4. **Fill in `AGENT_TARGET_MAP`** to map the id that arrives in the alert to the
   Foundry agent, and apply it to the Function App:

   ```jsonc
   {
     "<AGENT_ID>": {
       "foundry_agent_id": "<AGENT_ID>",
       "service_principal_id": "<optional, mechanism B only>"
     }
   }
   ```

   ```powershell
   az functionapp config appsettings set `
     --name <functionAppName> --resource-group rg-block-agent `
     --settings AGENT_TARGET_MAP='<one-line-json>'
   ```

   > If you map nothing, the Function assumes the alert's id **is** the
   > `foundry_agent_id` (fallback in `config.py`).

5. **Adjust the budget/alert.** The metric alert `budget-<foundryAccountName>`
   already exists (created by the Bicep). Change the threshold with
   `budgetTokenThreshold` or directly in the portal for another demo value.

---

## 8. How it all works (permissions and communication)

### 8.1 Flow of a block

1. Token usage exceeds the threshold → the **metric alert** fires.
2. The alert invokes the **Action Group** → webhook `POST /api/budget-alert` (with
   the *common alert schema*).
3. The Function parses the alert, resolves the agent (`AGENT_TARGET_MAP` /
   `alertContext.AgentId` / a budget named `budget-<agentId>`) and **dispatches** to
   the mechanism (by default `DEFAULT_BLOCK_MECHANISM=foundry`).
4. The mechanism requests a **token** with the managed identity and calls the
   relevant API. The agent ends up `disabled`.

### 8.2 Permissions (least privilege)

| Mechanism | Permission | Scope | Granted by |
|-----------|------------|-------|------------|
| A – Foundry | `Azure AI Developer` **+** `Cognitive Services User` | Foundry account | Bicep |
| C – ARM tag | `Tag Contributor` | Foundry account | Bicep |
| B – Graph | `Application.ReadWrite.All` | tenant (Graph) | `grant-graph-permission.ps1` (**Global Admin**) |
| Runtime | `Storage Blob Data Owner` + `Storage Queue Data Contributor` | storage | Bicep |

> **🔑 Key detail for Mechanism A:** `Azure AI Developer` alone does **not** cover
> the agents data-plane (`Microsoft.CognitiveServices/*/agents/*`) → it returns
> `403 does not have permissions for .../agents/read`. That is why
> **`Cognitive Services User` is also required**. After assigning it, the data plane
> takes **2–5 min** to propagate.

The 6 agent data-actions are: `agents/read`, `/write`, `/delete`,
`/state/disable/action`, `/state/enable/action`,
`/endpoints/UserIdentityImpersonation/action`. Mechanism A only needs
`read` + `state/disable/action` + `state/enable/action` (+`write` for the fallback).

### 8.3 Token flow (managed identity → Entra → RBAC)

1. `DefaultAzureCredential` requests the token from **IMDS** (the internal instance
   metadata endpoint, `169.254.169.254`).
2. IMDS talks to **Entra ID**, which returns a **signed JWT** for the *audience* of
   the requested scope:
   - Foundry: `https://ai.azure.com/.default`
   - ARM (tag): `https://management.azure.com/.default`
   - Graph: `https://graph.microsoft.com/.default`
3. The Function makes the **direct HTTPS POST** to the service endpoint with that
   token (it does not go "through the identity").
4. **Azure RBAC is evaluated at the resource, per call** (it does not travel in the
   token): ARM checks whether the identity has the required data-action. That is why
   a role failure is a `403` from the resource, not a token problem. (Graph *app
   roles* do travel inside the token.)

> **Mechanism B** disables the agent's **Entra identity**, not Foundry itself: if
> the agent were a classic assistant backed by a regular SP, setting
> `accountEnabled=false` cuts its sign-in and therefore its access.

---

## 9. Endpoints and payloads

| Method | Route | Auth | Purpose |
|--------|-------|------|---------|
| `POST` | `/api/budget-alert` | function key | Block/unblock based on the payload |
| `GET`  | `/api/health` | anonymous | Liveness + list of mechanisms |

Simplified payload (manual testing, see `samples/simplified_block.json`):

```json
{ "agentId": "<AGENT_ID>", "spend": 128.55, "budget": 100,
  "action": "block", "mechanism": "foundry" }
```

- `action`: `block` (default) or `unblock`.
- `mechanism`: `foundry` | `graph` | `tag` | `all` (defaults to
  `DEFAULT_BLOCK_MECHANISM`).
- Agent resolution: `agentId` field → `alertContext.AgentId` → a budget named
  `budget-<agentId>`.

Real format: *Common Alert Schema*, see `samples/common_alert.json`.

---

## 10. Configuration (App Settings)

See `local.settings.json.example`. Key settings:

| Setting | Purpose |
|---------|---------|
| `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `FOUNDRY_ACCOUNT_NAME` | Locate the Foundry account (Mechanism C) |
| `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_API_VERSION` (`v1`) | Foundry data plane (Mechanism A) |
| `GRAPH_SCOPE` | Graph audience (Mechanism B) |
| `AGENT_TARGET_MAP` | JSON `agentId → { foundry_agent_id, service_principal_id }` |
| `DEFAULT_BLOCK_MECHANISM` | Default mechanism when the alert doesn't specify one |
| `AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET` | **Local dev only.** In Azure the managed identity is used — leave blank. |

The Bicep fills in all of these settings automatically (except `AGENT_TARGET_MAP`,
which you set after creating the agent).

---

## 11. Run and test locally

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Offline test: blocks, verifies, and checks that unblock restores state (no Azure)
.venv\Scripts\python.exe -m unittest discover -s tests
```

To run the host locally you need Azure Functions Core Tools:

```powershell
copy local.settings.json.example local.settings.json   # fill in the values
func start
# in another shell:
curl -X POST http://localhost:7071/api/budget-alert -H "Content-Type: application/json" -d "@samples/simplified_block.json"
```

---

## 12. Unblocking

A block is **never** reverted automatically (there is no timer; a "Resolved" alert
does not re-enable). To unblock, send the same payload with `"action": "unblock"`
(see `samples/simplified_unblock.json`) or, for Mechanism A, re-enable the agent's
`state` from the Foundry portal. Each mechanism restores the previous state
(`state=enabled`, `accountEnabled=true`, tag `=Enabled`). Nothing is deleted.

---

## 13. Repo layout

```
function_app.py             # Azure Functions v2 entrypoint (HTTP routes)
host.json                   # Host config
requirements.txt            # Python dependencies
local.settings.json.example # Copy to local.settings.json for local dev
blockagent/
  config.py                 # Env-driven config + agentId→targets mapping
  auth.py                   # Managed-identity / app-registration tokens
  budget_alert.py           # Parses common-alert-schema or simplified payload
  dispatcher.py             # Routes alert → mechanism(s), block/unblock
  mechanisms/
    base.py                 # BlockResult
    foundry.py              # Mechanism A (native state)
    graph.py                # Mechanism B (Entra identity)
    arm_tag.py              # Mechanism C (ARM tag)
samples/                    # Example payloads
tests/test_harness.py       # Offline test: block → verify → unblock restores
deploy/                     # Bicep + scripts (see deploy/README.md)
```
