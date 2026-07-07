<#
.SYNOPSIS
  One-shot deploy of the "block agent on budget" solution.

.DESCRIPTION
  Steps performed:
    1. Deploy all infrastructure from main.bicep. This now creates EVERYTHING:
       the Azure AI Foundry account + project, storage, Function App + managed
       identity, Log Analytics + Application Insights, the Action Group and the
       budget metric alert, and ALL role assignments for mechanisms A
       (Azure AI Developer + Cognitive Services User) and C (Tag Contributor).
    2. Publish the Python code with 'func azure functionapp publish'.

  Mechanism B (Graph Application.ReadWrite.All) needs Global Admin consent and
  is handled separately by grant-graph-permission.ps1.

  NOTE: the model deployment and the agent are NOT created by this template.
  After deploying, open the Foundry portal, deploy a model, create an agent,
  copy its agent id, and put it in AGENT_TARGET_MAP (see README.md).

.PREREQUISITES
  - az login   (with rights to create resources and assign roles)
  - Azure Functions Core Tools v4  (func)
  - Edit deploy/main.parameters.json first (unique names).

.EXAMPLE
  ./deploy/deploy.ps1 -ResourceGroup rg-block-agent -Location swedencentral
#>
param(
  [Parameter(Mandatory = $true)][string]$ResourceGroup,
  [Parameter(Mandatory = $true)][string]$Location,
  [string]$ParametersFile = "$PSScriptRoot/main.parameters.json"
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent

Write-Host "==> Ensuring resource group '$ResourceGroup' exists" -ForegroundColor Cyan
az group create --name $ResourceGroup --location $Location --only-show-errors | Out-Null

Write-Host "==> Validating template (what-if)" -ForegroundColor Cyan
az deployment group what-if `
  --resource-group $ResourceGroup `
  --template-file "$PSScriptRoot/main.bicep" `
  --parameters "@$ParametersFile"

Write-Host "==> Deploying infrastructure (main.bicep)" -ForegroundColor Cyan
# Creates Foundry account+project, storage, Function App + MI, App Insights,
# action group + budget alert, and all role assignments (mechanisms A & C).
$deployment = az deployment group create `
  --resource-group $ResourceGroup `
  --template-file "$PSScriptRoot/main.bicep" `
  --parameters "@$ParametersFile" `
  --query properties.outputs -o json | ConvertFrom-Json

$functionAppName = $deployment.functionAppName.value
$principalId     = $deployment.managedIdentityPrincipalId.value
$foundryId       = $deployment.foundryAccountResourceId.value
Write-Host "    Function App: $functionAppName"
Write-Host "    Identity:     $principalId"
Write-Host "    Foundry res:  $foundryId"
Write-Host "    Project ep:   $($deployment.foundryProjectEndpoint.value)"

Write-Host "==> Publishing function code" -ForegroundColor Cyan
Push-Location $repoRoot
try {
  func azure functionapp publish $functionAppName --python
} finally {
  Pop-Location
}

Write-Host ""
Write-Host "DONE. Health check:" -ForegroundColor Green
Write-Host "  https://$($deployment.functionAppDefaultHostName.value)/api/health"
Write-Host ""
Write-Host "Next (manual, in the Foundry portal):" -ForegroundColor Yellow
Write-Host "  1. Deploy a model and create an agent in the Foundry project."
Write-Host "  2. Copy the agent id and add it to AGENT_TARGET_MAP (see README.md)."
Write-Host ""
Write-Host "Mechanism B (Graph) still needs admin consent. Run:" -ForegroundColor Yellow
Write-Host "  ./deploy/grant-graph-permission.ps1 -PrincipalId $principalId"
