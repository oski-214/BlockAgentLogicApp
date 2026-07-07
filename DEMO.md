# Demo — Block an agent when its budget is exceeded

Copy-paste guide to showcase the flow. Everything is **reversible and
non-destructive**: every block can be undone and no resource is deleted.

> **In one sentence:** when an agent's token usage exceeds a threshold, an alert
> calls this Function and it **disables the agent**. It is the automated equivalent
> of the M365 Admin Center *"Block agent"* button (no public API).

This assumes you already deployed the solution with the Bicep (see
[`README.md`](README.md)): the Foundry account and project, Function App, Action
Group, and metric alert already exist.

---

## 0. Variables (fill in once)

Open **PowerShell**, edit the `<...>` values with those of **your** deployment, and
paste the block:

```powershell
az login

# --- Fill in with your values ---
$FunctionApp   = "<FUNCTION_APP>"          # e.g. fa-blockagent-myorg
$Rg            = "<RG>"                     # Function RG, e.g. rg-block-agent
$FoundryAcct   = "<FOUNDRY_ACCOUNT>"        # Foundry account, e.g. aif-blockagent-myorg
$Project       = "<PROJECT>"               # project, e.g. block-agent-project
$AgentId       = "<AGENT_ID>"              # agent ID you created in Foundry
$SubId         = "<SUBSCRIPTION_ID>"
# --------------------------------

# Function key and endpoint URL
$key = az functionapp keys list --name $FunctionApp --resource-group $Rg `
  --query "functionKeys.default" -o tsv
if (-not $key) { throw "Could not retrieve the function key" }
$Url = "https://$FunctionApp.azurewebsites.net/api/budget-alert?code=$key"

# Foundry project data-plane endpoint and account resource id
$FoundryEp = "https://$FoundryAcct.services.ai.azure.com/api/projects/$Project"
$Rid = "/subscriptions/$SubId/resourceGroups/$Rg/providers/Microsoft.CognitiveServices/accounts/$FoundryAcct"

"URL: $Url"
"Foundry endpoint: $FoundryEp"
```

Quick health check (the host is alive and loaded the mechanisms):

```powershell
Invoke-RestMethod -Uri "https://$FunctionApp.azurewebsites.net/api/health"
# → {"status":"ok","mechanisms":["foundry","graph","tag"]}
```

---

## 1. Foundry preparation (model + agent)

The Bicep creates the Foundry **account and project**, but **not** the model or the
agent. Do this once in the portal:

1. **Azure AI Foundry portal** → your project (`<PROJECT>`).
2. **Deploy a model** (e.g. `gpt-4o-mini`).
3. **Create an agent** with that model.
4. **Copy the agent ID** → it is the `$AgentId` value above.
5. **Map the agent** in the Function (so it resolves the alert's id):

   ```powershell
   $map = @{ $AgentId = @{ foundry_agent_id = $AgentId } } | ConvertTo-Json -Compress
   az functionapp config appsettings set --name $FunctionApp --resource-group $Rg `
     --settings AGENT_TARGET_MAP=$map | Out-Null
   ```

6. **Budget/alert:** the metric alert `budget-<FOUNDRY_ACCOUNT>` already exists
   (created by the Bicep, `TotalTokens > threshold` on the Foundry account) and is
   wired to the Action Group → Function. Nothing else to create.

---

## 2. ⭐ Star scenario — Real end-to-end trigger

**The convincing scenario:** you saturate the agent from the playground and, within
a few minutes, it gets blocked **on its own** — without calling the Function by hand
— exactly like production when the budget fires.

```text
Foundry playground (you saturate the agent with a large prompt)
        │  thousands of TotalTokens are generated
        ▼
Metric alert  budget-<FOUNDRY_ACCOUNT>  (TotalTokens > threshold, 1-min window)
        │  monitorCondition = Fired
        ▼
Action Group  (webhook, common schema)
        │  POST of the alert payload
        ▼
Azure Function  →  default mechanism (foundry)  →  POST /agents/<AGENT_ID>:disable  →  state = "disabled"
        │
        ▼
Agent blocked automatically
```

### 2.1 State BEFORE

```powershell
$tok = az account get-access-token --scope "https://ai.azure.com/.default" --query accessToken -o tsv
Invoke-RestMethod -Uri "$FoundryEp/agents/$AgentId?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } | Select-Object id, state
# → state = enabled
```

### 2.2 Saturate the agent from the Foundry portal

Open the agent's **playground** and send this **saturation prompt** (it generates
thousands of tokens, well above the threshold). If needed, send it 2–3 times in a
row within the same 1-minute window:

```text
Write a technical essay of at least 2000 words that explains in depth,
step by step and with examples, the complete architecture of an automatic
AI-agent budget-blocking system on Azure: include Azure Functions,
Azure Monitor, action groups, Cost Management, Microsoft Graph and ARM.
Develop each section in the greatest possible detail, add pros,
cons, alternatives and an extensive final summary. Do not omit anything.
```

### 2.3 Wait for the alert to fire (~1–5 min)

Azure Monitor evaluates the metric every minute. When `TotalTokens` exceeds the
threshold, the alert goes to **Fired**, calls the Action Group, and it calls the
Function. In the portal: **Monitor → Alerts** → you'll see
`budget-<FOUNDRY_ACCOUNT>` in `Fired`.

### 2.4 Verify the agent blocked ITSELF

```powershell
Invoke-RestMethod -Uri "$FoundryEp/agents/$AgentId?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } | Select-Object id, state
# → state = disabled
```

**Definitive visual proof:** go back to the playground and try to use the agent →
the service no longer serves it. It is not a flag: it is the native state
*enforced* by Foundry.

Trace that the Function ran (App Insights):

```powershell
az monitor app-insights query --app $FunctionApp --resource-group $Rg `
  --analytics-query "requests | where timestamp > ago(15m) | where name contains 'budget-alert' | project timestamp, resultCode | order by timestamp desc" `
  -o table
```

> **Message:** nobody touched anything after sending the prompt. The agent blocked
> itself because its usage fired the alert. In production, that same mechanism is
> tied to the agent's real **cost budget**.

### 2.5 Unblock (revert after the demo)

The block **does not undo itself** when the alert resolves (by design — the admin
decides when to re-enable):

```powershell
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"foundry`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
Start-Sleep 5
Invoke-RestMethod -Uri "$FoundryEp/agents/$AgentId?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } | Select-Object id, state
# → state = enabled
```

> **Budget vs. metric alert:** a Cost Management budget bills with hours of delay,
> so it is not suitable for a live demo. That is why the trigger uses the **metric
> alert** on `TotalTokens` (fires in 1–5 min). The blocking logic is identical.

---

## 3. Direct native block (mechanism A, without waiting for the alert)

Same mechanism as the star scenario, but invoking the Function by hand — handy to
show it instantly.

```powershell
# BLOCK
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"foundry`",`"action`":`"block`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
# → success=true, detail: "Native state action :disable ... -> state=disabled (was enabled)"

# UNBLOCK
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"foundry`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
# → state back to enabled
```

The Function's **managed identity runs it, without Global Admin**, and the service
itself *enforces* it. This is the recommended block for Foundry agents.

---

## 4. (Secondary) Mechanism C — ARM tag

A **blunt** account-level block: it sets `MS-AOAI-Feature-Assistants=Disabled` on the
Foundry account, which disables **all** classic assistants on that account. For
comparison only.

```powershell
# State BEFORE (tag absent or Enabled)
az tag list --resource-id $Rid --query "properties.tags" -o json

# BLOCK (tag mechanism only)
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"tag`",`"action`":`"block`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results

az tag list --resource-id $Rid --query "properties.tags.\"MS-AOAI-Feature-Assistants\"" -o tsv
# → Disabled

# UNBLOCK
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"tag`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
# → tag back to Enabled
```

---

## 5. (Secondary) Mechanism B — Entra identity

Disables the agent's `servicePrincipal` (`accountEnabled=false`), cutting its access
at the identity level.

> ⚠️ For **Foundry agent identities** (type `agentIdentity`, preview), this
> operation **requires Global Admin**: the managed identity gets `403` even with
> `Application.ReadWrite.All`. For **classic agents** backed by a regular service
> principal, the managed identity with `Application.ReadWrite.All` is enough.

```powershell
$SpUrl = "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentId?`$select=displayName,accountEnabled"
$gtok  = az account get-access-token --scope "https://graph.microsoft.com/.default" --query accessToken -o tsv

# State BEFORE
Invoke-RestMethod -Uri $SpUrl -Headers @{ Authorization = "Bearer $gtok" }

# BLOCK (graph mechanism only)
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"graph`",`"action`":`"block`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results

# UNBLOCK
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"graph`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
```

---

## 6. (Secondary) Error handling

```powershell
# No agent → 422
try {
  Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body '{}'
} catch { $_.Exception.Response.StatusCode.value__ }   # → 422

# Invalid mechanism → 400
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"nope`",`"action`":`"block`"}"
try {
  Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body
} catch { $_.Exception.Response.StatusCode.value__ }   # → 400
```

---

## Summary

| Scenario | Mechanism | What it shows |
|----------|-----------|---------------|
| ⭐ Star (2) | A – native state | **Automatic** block from token saturation, touching nothing |
| Direct (3) | A – native state | The same block, invoked by hand |
| Tag (4) | C – ARM tag | Blunt account-level block (comparison) |
| Identity (5) | B – Entra | Identity-level cut (requires GA for Foundry agents) |
| Errors (6) | — | Input validation (`422`/`400`) |

---

## Final cleanup

Leave the agent **enabled** after the demo:

```powershell
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"foundry`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results

$tok = az account get-access-token --scope "https://ai.azure.com/.default" --query accessToken -o tsv
Invoke-RestMethod -Uri "$FoundryEp/agents/$AgentId?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } | Select-Object id, state
# → state = enabled
```

If you tried Mechanism C, check the tag is back to `Enabled`; if you tried B, that
`accountEnabled=true`.
