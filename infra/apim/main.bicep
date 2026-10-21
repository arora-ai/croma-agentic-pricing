// API Management for the Croma agentic system: the infrastructure and the three gateways.
//   FRONT gateway       browsers and channels -> APIM -> chat API -> Foundry CX agent
//   TOOL gateway        Foundry agents        -> APIM -> tools hub MCP endpoints
//   DATA TOOL gateway   the hub               -> APIM -> Databricks Genie REST API
// Deploy with infra/apim/deploy_apim.ps1, which passes every parameter through environment variables (see main.bicepparam), so no secret is ever on a
// command line or in a file. Documentation: docs/APIM.md.
targetScope = 'resourceGroup'

@description('Name prefix of every resource: apim-<prefix>, law-<prefix>, appi-<prefix>, and the products.')
param prefix string = 'croma2'

param location string = resourceGroup().location

@description('Globally unique: it becomes <name>.azure-api.net.')
param apimName string = 'apim-${prefix}'

@allowed([
  'BasicV2'
  'StandardV2'
])
param apimSku string = 'BasicV2'

param publisherEmail string

param chatBackendUrl string
param hubBackendUrl string
param databricksUrl string
param genieSpaceId string

@secure()
param hubApiKey string

@secure()
param gatewayKey string

param jwtEnabled bool = false
param entraTenantId string = tenant().tenantId
param entraAudience string = 'api://not-configured'
param entraAudienceAlt string = 'api://not-configured'

@description('Comma-separated extra origins allowed to call the front API from a browser.')
param corsOrigins string = ''

param frontCallsPerMinute int = 120
param chatTurnsPerMinute int = 10
param chatTurnsPerDay int = 0
param staticCallsPerMinute int = 120
param toolsCallsPerMinute int = 600
param dataCallsPerMinute int = 600
param dataQuestionsPerMinute int = 30
param chatTimeoutSeconds int = 3600
param exposeStreamCheck bool = true

@description('Create or update the API Management instance, Log Analytics and Application Insights. The deploy script turns this off once the instance exists: updating the instance takes minutes, and the gateway definitions do not need it.')
param deployPlatform bool = true

module platform 'modules/platform.bicep' = if (deployPlatform) {
  name: 'platform'
  params: {
    prefix: prefix
    location: location
    apimName: apimName
    apimSku: apimSku
    publisherEmail: publisherEmail
  }
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

module gateway 'modules/gateway.bicep' = {
  name: 'gateway'
  dependsOn: [
    platform
  ]
  params: {
    apimName: apimName
    loggerId: resourceId('Microsoft.ApiManagement/service/loggers', apimName, 'appinsights')
    prefix: prefix
    chatBackendUrl: chatBackendUrl
    hubBackendUrl: hubBackendUrl
    databricksUrl: databricksUrl
    genieSpaceId: genieSpaceId
    hubApiKey: hubApiKey
    gatewayKey: gatewayKey
    jwtEnabled: jwtEnabled
    entraTenantId: entraTenantId
    entraAudience: entraAudience
    entraAudienceAlt: entraAudienceAlt
    corsOrigins: filter(split(corsOrigins, ','), origin => !empty(trim(origin)))
    frontCallsPerMinute: frontCallsPerMinute
    chatTurnsPerMinute: chatTurnsPerMinute
    chatTurnsPerDay: chatTurnsPerDay
    staticCallsPerMinute: staticCallsPerMinute
    toolsCallsPerMinute: toolsCallsPerMinute
    dataCallsPerMinute: dataCallsPerMinute
    dataQuestionsPerMinute: dataQuestionsPerMinute
    chatTimeoutSeconds: chatTimeoutSeconds
    exposeStreamCheck: exposeStreamCheck
  }
}

output apimName string = apimName
output gatewayUrl string = gateway.outputs.gatewayUrl
output frontUrl string = gateway.outputs.frontUrl
output toolsUrl string = gateway.outputs.toolsUrl
output dataUrl string = gateway.outputs.dataUrl
output apimPrincipalId string = apim.identity.principalId
output subscriptionNames object = gateway.outputs.subscriptionNames
output appInsightsName string = 'appi-${prefix}'
