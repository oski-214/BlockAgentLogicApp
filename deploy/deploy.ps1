<#
.SYNOPSIS
  One-shot deploy of the "block agent on budget" Azure Function, including the
  least-privilege role assignments for mechanisms A (Foundry) and C (ARM tag).

.DESCRIPTION
  Steps performed:
    1. Deploy infra (storage + Function App + managed identity) from main.bicep.
    2. Grant the Function App's managed identity:
         - "Azure AI Developer" + "Cognitive Services User" on agent-verse-resource (mechanism A)
         - "Tag Contributor"     on agent-verse-resource   (mechanism C)
    3. Publish the Python code with 'func azure functionapp publish'.
  Mechanism B (Graph Application.ReadWrite.All) needs Global Admin consent and is
  handled separately by grant-graph-permission.ps1.

.PREREQUISITES
  - az login   (with rights to create resources and assign roles)
  - Azure Functions Core Tools v4  (func)
  - Edit deploy/main.parameters.json first.

.EXAMPLE
  ./deploy/deploy.ps1 -ResourceGroup rg-block-agent -Location westeurope
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

Write-Host "==> Deploying infrastructure (main.bicep)" -ForegroundColor Cyan
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

Write-Host "==> Assigning data-plane roles (mechanism A: Foundry Agent Service)" -ForegroundColor Cyan
# "Azure AI Developer" alone is NOT enough for the modern /agents data plane:
# the native :disable/:enable state actions require the
# Microsoft.CognitiveServices/*/agents data-actions, which "Cognitive Services
# User" grants. We assign both so mechanism A works end-to-end from the MI
# (no Global Admin needed).
az role assignment create `
  --assignee-object-id $principalId --assignee-principal-type ServicePrincipal `
  --role "Azure AI Developer" --scope $foundryId --only-show-errors | Out-Null
az role assignment create `
  --assignee-object-id $principalId --assignee-principal-type ServicePrincipal `
  --role "Cognitive Services User" --scope $foundryId --only-show-errors | Out-Null

Write-Host "==> Assigning tag role (mechanism C: Tag Contributor)" -ForegroundColor Cyan
az role assignment create `
  --assignee-object-id $principalId --assignee-principal-type ServicePrincipal `
  --role "Tag Contributor" --scope $foundryId --only-show-errors | Out-Null

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
Write-Host "Mechanism B (Graph) still needs admin consent. Run:" -ForegroundColor Yellow
Write-Host "  ./deploy/grant-graph-permission.ps1 -PrincipalId $principalId"
