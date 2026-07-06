// Infra for the "block agent on budget" Azure Function.
// Provisions: storage account, consumption plan, Linux Python Function App with a
// system-assigned managed identity, and the app settings the code reads.
//
// Role assignments against the *existing* Foundry resource (agent-verse-resource)
// and the Graph app permission are done in deploy.ps1, because that resource may
// live in a different resource group / require admin consent.

@description('Location for all resources.')
param location string = resourceGroup().location

@description('Globally-unique name for the Function App.')
param functionAppName string

@description('Globally-unique storage account name (3-24 lowercase alphanumeric).')
param storageAccountName string

@description('Subscription id that holds agent-verse-resource (for mechanism C tag scope).')
param foundrySubscriptionId string

@description('Resource group that holds agent-verse-resource.')
param foundryResourceGroup string

@description('Name of the Foundry / Cognitive Services account.')
param foundryAccountName string = 'agent-verse-resource'

@description('Foundry project data-plane endpoint (mechanism A).')
param foundryProjectEndpoint string

@description('Foundry Agents REST API version.')
param foundryApiVersion string = '2025-05-01'

@description('JSON map: agentId -> { foundry_agent_id, service_principal_id }.')
param agentTargetMap string = '{}'

@description('Default block mechanism when the alert does not specify one.')
@allowed([ 'foundry', 'graph', 'tag' ])
param defaultBlockMechanism string = 'foundry'

var storageAccountId = storageAccount.id
var deploymentContainerName = 'app-package'

// Well-known built-in role definition ids (storage, identity-based access).
var roleStorageBlobDataOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var roleStorageQueueDataContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

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

// Flex Consumption plan (managed-identity storage, no shared keys required).
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
        { name: 'AZURE_SUBSCRIPTION_ID', value: foundrySubscriptionId }
        { name: 'AZURE_RESOURCE_GROUP', value: foundryResourceGroup }
        { name: 'FOUNDRY_ACCOUNT_NAME', value: foundryAccountName }
        { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
        { name: 'FOUNDRY_API_VERSION', value: foundryApiVersion }
        { name: 'GRAPH_SCOPE', value: 'https://graph.microsoft.com/.default' }
        { name: 'AGENT_TARGET_MAP', value: agentTargetMap }
        { name: 'DEFAULT_BLOCK_MECHANISM', value: defaultBlockMechanism }
      ]
    }
  }
}

// Grant the Function's managed identity access to its storage (blob + queue),
// used for both the deployment package and the Functions runtime state.
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

output functionAppName string = functionApp.name
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output managedIdentityPrincipalId string = functionApp.identity.principalId
output foundryAccountResourceId string = '/subscriptions/${foundrySubscriptionId}/resourceGroups/${foundryResourceGroup}/providers/Microsoft.CognitiveServices/accounts/${foundryAccountName}'
output usedStorageAccountId string = storageAccountId
