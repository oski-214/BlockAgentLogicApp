# Testing guide (how to test each mechanism)

This guide explains, step by step, how to test the "block agent when the budget is
exceeded" POC and verify that **each of the three mechanisms** works and is
**reversible**. There are three test levels, from least to most effort:

1. **Offline test** — no Azure, in seconds. Validates all the logic.
2. **Local test with the Functions host** — real HTTP calls with `curl`.
3. **End-to-end test on Azure** — with real resources and a real budget alert.

> Feasibility reminder: the M365 Admin Center "Block" button has **no public API**,
> so we test the automated equivalent. Also, Azure budgets **cannot** be scoped to a
> single agent inside the Foundry account (only to resource / resource group / tag).

---

## 0. Prerequisites

```powershell
# From the repo root
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For levels 2 and 3 you also need:

- **Azure Functions Core Tools v4** (`func`) — to run the local host.
- **Azure CLI** (`az`) — to create/manage resources and test permissions.

---

## 1. Offline test (recommended to start)

Does not touch Azure: it simulates the three planes (Foundry, Graph and ARM) in
memory and replays the example payloads through the real parser and dispatcher. It
verifies that a block is applied and that the **unblock restores the previous
state**.

```powershell
.venv\Scripts\python.exe -m tests.test_harness
```

Expected output:

```
test_block_then_unblock_all_mechanisms ... ok
test_parse_common_alert ... ok
test_single_mechanism_selection ... ok
----------------------------------------------------------------------
Ran 3 tests in 0.0XXs
OK
```

What each test proves:

| Test | What it validates |
|------|-------------------|
| `test_parse_common_alert` | A real alert (Common Alert Schema) is parsed correctly: `agentId`, spend, budget and action. |
| `test_block_then_unblock_all_mechanisms` | The 3 mechanisms **block** (Foundry native state, `accountEnabled=false`, tag `Disabled`) and then **unblock** restores each previous state. |
| `test_single_mechanism_selection` | A single mechanism can be run (e.g. only `graph`). |

### Run a single mechanism from the terminal (offline)

```powershell
.venv\Scripts\python.exe -c "import tests.test_harness as t; import unittest; unittest.main(module=t, argv=['x','BlockAgentHarness.test_single_mechanism_selection'], exit=False)"
```

---

## 2. Local test with the Azure Functions host

Runs the real Function and you send payloads with `curl`. You can do it two ways
depending on whether you want to touch Azure.

### 2.1 Prepare configuration

```powershell
copy local.settings.json.example local.settings.json
```

Edit `local.settings.json`:

- To **only test the HTTP flow + parsing + dispatch** without real credentials, you
  don't need to fill anything else (the Azure calls fail in a controlled way and you
  see the per-mechanism error in the response).
- To **test against real Azure** from your machine, fill in `AZURE_TENANT_ID`,
  `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (a dev app registration) and the
  `AGENT_TARGET_MAP` with real ids.

### 2.2 Start the host

```powershell
func start
```

Check it's alive:

```powershell
curl http://localhost:7071/api/health
# {"status":"ok","mechanisms":["foundry","graph","tag"]}
```

### 2.3 Send a block

```powershell
curl -X POST http://localhost:7071/api/budget-alert `
  -H "Content-Type: application/json" `
  -d "@samples/simplified_block.json"
```

Response (per-mechanism summary):

```json
{
  "action": "block",
  "agentId": "asst_demo123",
  "mechanisms": ["foundry", "graph", "tag"],
  "results": [
    { "mechanism": "foundry", "success": true, "reversible": true, "detail": "..." },
    { "mechanism": "graph",   "success": true, "reversible": true, "detail": "..." },
    { "mechanism": "tag",     "success": true, "reversible": true, "detail": "..." }
  ],
  "allSucceeded": true
}
```

### 2.4 Unblock (revert)

```powershell
curl -X POST http://localhost:7071/api/budget-alert `
  -H "Content-Type: application/json" `
  -d "@samples/simplified_unblock.json"
```

### 2.5 Test mechanisms individually

Change the body's `mechanism` field to `foundry`, `graph`, `tag` or `all`:

```powershell
curl -X POST http://localhost:7071/api/budget-alert `
  -H "Content-Type: application/json" `
  -d '{ "agentId": "asst_demo123", "mechanism": "graph", "action": "block" }'
```

### 2.6 Test the real alert format (Common Alert Schema)

```powershell
curl -X POST http://localhost:7071/api/budget-alert `
  -H "Content-Type: application/json" `
  -d "@samples/common_alert.json"
```

### 2.7 Error cases worth testing

| Case | How | Expected result |
|------|-----|-----------------|
| No `agentId` | Send `{}` | `422` with a message that the agent could not be determined |
| Invalid JSON | Send non-JSON text | `400` "Request body must be valid JSON" |
| Unknown mechanism | `"mechanism": "foo"` | `400` with the list of valid mechanisms |
| Partial failure | A mechanism without permissions | `207` and `allSucceeded=false`, with the error on that mechanism |

---

## 3. End-to-end test on Azure (optional, the most realistic)

### 3.1 Deploy the Function

Use the Bicep (see [`README.md`](README.md) / [`deploy/README.md`](deploy/README.md))
to create everything, or deploy manually:

```powershell
# Create the Function App (consumption plan, Python 3.11 runtime) and deploy
az functionapp create --resource-group rg-block-agent --consumption-plan-location swedencentral `
  --runtime python --runtime-version 3.11 --functions-version 4 `
  --name fa-block-agent --storage-account <storageaccount> --os-type Linux
func azure functionapp publish fa-block-agent
```

### 3.2 Identity and permissions

```powershell
# System-assigned managed identity
az functionapp identity assign --name fa-block-agent --resource-group rg-block-agent
```

The mechanism A and C roles, plus the storage ones, are **already granted by the
Bicep** on the Foundry account (least privilege):

- **Mechanism A (Foundry):** `Azure AI Developer` **+** `Cognitive Services User`
  on the Foundry account. `Azure AI Developer` alone does **not** cover the agents
  data-plane (`.../agents/*`) → returns `403`; that is why `Cognitive Services User`
  is also required. After assigning them, the data plane takes 2–5 min.
- **Mechanism B (Graph):** application permission `Application.ReadWrite.All`
  (outside the Bicep, granted by `grant-graph-permission.ps1` — needs Global Admin).
- **Mechanism C (tag):** `Tag Contributor` on the Foundry account.

### 3.3 Configure the App Settings

Upload the same keys from `local.settings.json.example` as *Application settings*
(without `AZURE_CLIENT_SECRET`: in Azure the managed identity is used).

### 3.4 Wire up the budget alert

The **Action Group** and the **metric alert** (`TotalTokens` on the Foundry account)
are **already created by the Bicep**, wired to the `/api/budget-alert` endpoint. You
just need to adjust the threshold (`budgetTokenThreshold`) if you want another value.
If you prefer a real Cost Management budget, create it on the Foundry account (or its
RG), name it `budget-<agentId>` and point it at the same Action Group.

### 3.5 Verify the result in the portal

- **Foundry:** the agent has `state=disabled` (native-state block).
- **Graph/Entra:** the service principal shows "Enabled for users to sign in = No"
  (`accountEnabled=false`).
- **Tag:** the Foundry account has `MS-AOAI-Feature-Assistants=Disabled`.

To revert, resend the alert with `"action": "unblock"`.

---

## 4. Summary table: what each level tests

| Level | Touches Azure | What it checks | Main command |
|-------|---------------|----------------|--------------|
| 1. Offline | No | Parsing + dispatch + reversibility of the 3 mechanisms | `python -m tests.test_harness` |
| 2. Local host | Optional | Real HTTP flow, status codes, mechanism selection | `func start` + `curl` |
| 3. Azure E2E | Yes | Real block/unblock and budget trigger | `func azure functionapp publish` + alert |

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ModuleNotFoundError` | venv without dependencies | `pip install -r requirements.txt` |
| `401/403` on a mechanism | Missing identity permissions | Review the roles in section 3.2 |
| `422 no agent id` | The alert carries no id | Use `agentId`, `alertContext.AgentId` or name the budget `budget-<agentId>` |
| `allSucceeded=false` (`207`) | One mechanism failed but others didn't | Check the `results[].detail` field of that mechanism |
| The agent keeps responding after the Foundry block | Missing `Cognitive Services User` role (the native action returns `403`) or the data plane hasn't propagated yet | Assign `Cognitive Services User` on the Foundry account and wait 2–5 min; check `state=disabled` |
