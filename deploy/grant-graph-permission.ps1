<#
.SYNOPSIS
  Grants the Function App's managed identity the Microsoft Graph application
  permission required by mechanism B (disable a service principal).

.DESCRIPTION
  Assigns the 'Application.ReadWrite.All' app role on the Microsoft Graph service
  principal to the given managed identity. This is admin consent and therefore
  REQUIRES a Global Administrator (or Privileged Role Administrator) to run it.

  Mechanism B needs 'Application.ReadWrite.All' because it PATCHes
  servicePrincipal.accountEnabled. If you prefer not to grant this tenant-wide
  permission, run only mechanisms A and C and skip this script.

.EXAMPLE
  ./deploy/grant-graph-permission.ps1 -PrincipalId <managed-identity-objectId>
#>
param(
  [Parameter(Mandatory = $true)][string]$PrincipalId,
  [string]$GraphAppRole = 'Application.ReadWrite.All'
)

$ErrorActionPreference = 'Stop'

# Microsoft Graph well-known app id.
$graphAppId = '00000003-0000-0000-c000-000000000000'

Write-Host "==> Resolving Microsoft Graph service principal" -ForegroundColor Cyan
$graphSpId = az ad sp show --id $graphAppId --query id -o tsv

Write-Host "==> Resolving app role id for '$GraphAppRole'" -ForegroundColor Cyan
$appRoleId = az ad sp show --id $graphAppId `
  --query "appRoles[?value=='$GraphAppRole'].id | [0]" -o tsv

if (-not $appRoleId) { throw "Could not find app role '$GraphAppRole' on Microsoft Graph." }

Write-Host "==> Assigning app role to managed identity $PrincipalId" -ForegroundColor Cyan
$body = @{ principalId = $PrincipalId; resourceId = $graphSpId; appRoleId = $appRoleId } | ConvertTo-Json -Compress

az rest --method POST `
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PrincipalId/appRoleAssignments" `
  --headers "Content-Type=application/json" `
  --body $body

Write-Host "DONE. Mechanism B (Graph service-principal disable) is now authorized." -ForegroundColor Green
