# Deployment (`deploy/`)

Infrastructure as code (Bicep) + scripts to deploy the **whole** solution with
least privilege. The full deployment guide is in the root
**[`README.md`](../README.md)**; this file only describes the contents of this
folder.

## Contents

| File | What it does |
|------|--------------|
| `main.bicep` | Creates **everything** from scratch: Foundry account (`AIServices`) + project, storage (identity-based), **Flex Consumption** Function App (Python 3.11) + managed identity, Log Analytics + Application Insights, **Action Group** (webhook → `/api/budget-alert`) + **metric alert** (`TotalTokens`), and **all role assignments** (mechanisms A and C + storage). It does **not** create a model or an agent. |
| `main.parameters.json` | Generic parameters (`CHANGEME` placeholders, empty `agentTargetMap`). Fill in unique names. |
| `deploy.ps1` | `what-if` → deploy the Bicep → publish the code (`func ... --python`). |
| `grant-graph-permission.ps1` | Grants the Mechanism B Graph permission (needs **Global Admin**). |

## Permissions granted by the Bicep (least privilege)

| Mechanism | Permission | Scope |
|-----------|------------|-------|
| A – Foundry | `Azure AI Developer` + `Cognitive Services User` | Foundry account |
| C – ARM tag | `Tag Contributor` | Foundry account |
| Runtime | `Storage Blob Data Owner` + `Storage Queue Data Contributor` | storage |
| B – Graph | `Application.ReadWrite.All` (**outside the Bicep** → `grant-graph-permission.ps1`, Global Admin) | tenant (Graph) |

> **🔑 Mechanism A:** `Azure AI Developer` alone does **not** cover the agents
> data-plane (`.../agents/*`) → `403`. That is why the Bicep also assigns
> `Cognitive Services User`. The block uses the agent's **native state**
> (`POST /agents/{id}:disable` / `:enable`, `api-version=v1`), *enforced* by the
> service, run by the managed identity **without Global Admin**.

## Quick usage

```powershell
az login
# 1) Edit main.parameters.json (globally unique names).
# 2) Validate without deploying:
az deployment group what-if -g rg-block-agent `
  --template-file deploy/main.bicep --parameters "@deploy/main.parameters.json"
# 3) Deploy + publish:
./deploy/deploy.ps1 -ResourceGroup rg-block-agent -Location swedencentral
# 4) (Optional, Global Admin) Mechanism B:
./deploy/grant-graph-permission.ps1 -PrincipalId <managed-identity-objectId>
```

After deploying, create the model and the agent in the Foundry portal and fill in
`AGENT_TARGET_MAP` (see [`README.md`](../README.md), "Manual steps in Foundry").

## Tenant policy note

If your tenant **forbids shared-key authentication on Storage** and **disables SCM
basic auth**, the **classic Consumption (Y1) plan does not work** (its content share
needs keys). That is why the Bicep uses **Flex Consumption** with identity-based
storage (`AzureWebJobsStorage__accountName` + `__credential=managedidentity`,
`allowSharedKeyAccess:false`).
