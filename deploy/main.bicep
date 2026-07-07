// Infra for the "block agent on budget" solution — fully self-contained.
//
// This template creates EVERYTHING needed to reproduce the deployment from
// scratch, generically:
//
//   * Azure AI Foundry account (Microsoft.CognitiveServices/accounts, kind
//     AIServices) + a Foundry project.  NOTE: it does NOT deploy a model or an
//     agent — you create those by hand in the Foundry portal after deployment,
//     and use the resulting agent id to fill AGENT_TARGET_MAP.
//   * Storage account (identity-based, no shared keys).
//   * Flex Consumption plan + Python Function App with a system-assigned
//     managed identity, wired with all the app settings the code reads.
//   * Log Analytics workspace + Application Insights (workspace-based).
//   * Action Group (webhook -> the Function's budget-alert endpoint, Common
//     Alert Schema) and a budget metric alert (TotalTokens on the Foundry
//     account) so the budget -> block flow is wired end to end.
//   * All role assignments for mechanisms A (Azure AI Developer + Cognitive
//     Services User) and C (Tag Contributor) on the created Foundry account,
//     plus the storage roles the runtime needs.
//
// Mechanism B (Graph Application.ReadWrite.All) needs Global Admin consent and
// is intentionally left out — run grant-graph-permission.ps1 for it.

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Location for all resources.')
param location string = resourceGroup().location

@description('Globally-unique name for the Function App.')
param functionAppName string = 'fa-blockagent-${uniqueString(resourceGroup().id)}'

@description('Globally-unique storage account name (3-24 lowercase alphanumeric).')
param storageAccountName string = 'stblk${uniqueString(resourceGroup().id)}'

@description('Globally-unique name for the Azure AI Foundry (Cognitive Services) account. Also used as its custom subdomain.')
param foundryAccountName string = 'aif-blockagent-${uniqueString(resourceGroup().id)}'

@description('Name of the Foundry project (child of the account).')
param foundryProjectName string = 'block-agent-project'

@description('Foundry Agents REST API version (v1 = modern Agent Service).')
param foundryApiVersion string = 'v1'

@description('JSON map: agentId -> { foundry_agent_id, service_principal_id }. Leave "{}" and fill in after you create the agent in Foundry.')
param agentTargetMap string = '{}'

@description('Default block mechanism when the alert does not specify one.')
@allowed([ 'foundry', 'graph', 'tag' ])
param defaultBlockMechanism string = 'foundry'

@description('Token count that trips the budget alert (TotalTokens, Total aggregation).')
param budgetTokenThreshold int = 1000

@description('Metric alert evaluation window (ISO 8601 duration).')
param budgetWindowSize string = 'PT1M'

@description('Metric alert evaluation frequency (ISO 8601 duration).')
param budgetEvaluationFrequency string = 'PT1M'

@description('Metric alert severity (0 = Critical .. 4 = Verbose).')
@allowed([ 0, 1, 2, 3, 4 ])
param alertSeverity int = 2

@description('Log Analytics workspace name backing Application Insights.')
param logAnalyticsName string = 'log-${functionAppName}'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var deploymentContainerName = 'app-package'

// Data-plane endpoint for the Foundry project (mechanism A talks to this).
var foundryProjectEndpoint = 'https://${foundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}'

// Well-known built-in role definition ids.
var roleStorageBlobDataOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var roleStorageQueueDataContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var roleAzureAIDeveloper = '64702f94-c441-49e6-a78b-ef80e0188fee'
var roleCognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var roleTagContributor = '4a9ae827-6dc8-4573-8ac7-8239d42aa03f'

// ---------------------------------------------------------------------------
// Azure AI Foundry account + project (model/agent are created by hand later)
// ---------------------------------------------------------------------------

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryAccountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    // Required so the account can host Foundry projects and agents.
    allowProjectManagement: true
    // Needed for the <name>.services.ai.azure.com data-plane endpoint.
    customSubDomainName: foundryAccountName
    publicNetworkAccess: 'Enabled'
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: foundryProjectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {}
}

// ---------------------------------------------------------------------------
// Observability: Log Analytics + Application Insights (workspace-based)
// ---------------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: functionAppName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Storage (identity-based, no shared keys)
// ---------------------------------------------------------------------------

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    // Tenant policy forbids shared-key auth; the Function uses its managed
    // identity for all storage access instead.
    allowSharedKeyAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
  properties: { publicAccess: 'None' }
}

// ---------------------------------------------------------------------------
// Function App (Flex Consumption, system-assigned managed identity)
// ---------------------------------------------------------------------------

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${functionAppName}-plan'
  location: location
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  kind: 'functionapp'
  properties: { reserved: true }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    reserved: true
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageAccount.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: { type: 'SystemAssignedIdentity' }
        }
      }
      runtime: { name: 'python', version: '3.11' }
      scaleAndConcurrency: { maximumInstanceCount: 40, instanceMemoryMB: 2048 }
    }
    siteConfig: {
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        // Identity-based AzureWebJobsStorage (no account key).
        { name: 'AzureWebJobsStorage__accountName', value: storageAccount.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        // Foundry lives in this same subscription / resource group now.
        { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
        { name: 'AZURE_RESOURCE_GROUP', value: resourceGroup().name }
        { name: 'FOUNDRY_ACCOUNT_NAME', value: foundryAccountName }
        { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
        { name: 'FOUNDRY_API_VERSION', value: foundryApiVersion }
        { name: 'GRAPH_SCOPE', value: 'https://graph.microsoft.com/.default' }
        { name: 'AGENT_TARGET_MAP', value: agentTargetMap }
        { name: 'DEFAULT_BLOCK_MECHANISM', value: defaultBlockMechanism }
        { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: appInsights.properties.InstrumentationKey }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Role assignments: storage (runtime) + Foundry (mechanisms A & C)
// ---------------------------------------------------------------------------

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.id, roleStorageBlobDataOwner)
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataOwner)
  }
}

resource queueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.id, roleStorageQueueDataContributor)
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageQueueDataContributor)
  }
}

// Mechanism A needs BOTH: "Azure AI Developer" for the project/data plane and
// "Cognitive Services User" for the /agents data-actions (native :disable /
// :enable). "Azure AI Developer" alone returns 403 on the agents endpoints.
resource aiDeveloperRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, functionApp.id, roleAzureAIDeveloper)
  scope: foundry
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAzureAIDeveloper)
  }
}

resource cognitiveServicesUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, functionApp.id, roleCognitiveServicesUser)
  scope: foundry
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesUser)
  }
}

// Mechanism C: reversible ARM tag on the Foundry account.
resource tagContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, functionApp.id, roleTagContributor)
  scope: foundry
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleTagContributor)
  }
}

// ---------------------------------------------------------------------------
// Budget alert wiring: Action Group (webhook -> Function) + metric alert
// ---------------------------------------------------------------------------

// Host-level default key authorizes the budget-alert HTTP function. It exists
// once the Function App is created (before code is published).
var functionHostKey = listKeys('${functionApp.id}/host/default', '2023-12-01').functionKeys.default
var budgetAlertUrl = 'https://${functionApp.properties.defaultHostName}/api/budget-alert?code=${functionHostKey}'

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-${functionAppName}'
  location: 'Global'
  properties: {
    groupShortName: 'blkagent'
    enabled: true
    webhookReceivers: [
      {
        name: 'block-fn'
        serviceUri: budgetAlertUrl
        useCommonAlertSchema: true
        useAadAuth: false
      }
    ]
  }
}

resource budgetAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'budget-${foundryAccountName}'
  location: 'global'
  properties: {
    description: 'Blocks the target agent when TotalTokens exceeds the budget on the Foundry account.'
    severity: alertSeverity
    enabled: true
    scopes: [ foundry.id ]
    evaluationFrequency: budgetEvaluationFrequency
    windowSize: budgetWindowSize
    targetResourceType: 'Microsoft.CognitiveServices/accounts'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'cond0'
          metricName: 'TotalTokens'
          metricNamespace: 'Microsoft.CognitiveServices/accounts'
          operator: 'GreaterThan'
          threshold: budgetTokenThreshold
          timeAggregation: 'Total'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output functionAppName string = functionApp.name
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output managedIdentityPrincipalId string = functionApp.identity.principalId
output foundryAccountName string = foundry.name
output foundryAccountResourceId string = foundry.id
output foundryProjectEndpoint string = foundryProjectEndpoint
output actionGroupId string = actionGroup.id
output budgetAlertName string = budgetAlert.name
