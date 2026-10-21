// The platform half of the APIM deployment: Log Analytics, Application Insights and the API Management instance with its logger and diagnostics.
// Kept apart from the gateway definitions (gateway.bicep) because provisioning the APIM service is the slow part (minutes), and the gateway
// definitions can only be applied once it exists.

@description('Name prefix: resources are called apim-<prefix>, law-<prefix>, appi-<prefix>.')
param prefix string

param location string

@description('Globally unique APIM name (it becomes <name>.azure-api.net).')
param apimName string

@description('BasicV2 for dev and test (hourly billing, SLA). StandardV2 if BasicV2 is not offered in the region, or for VNet integration.')
@allowed([
  'BasicV2'
  'StandardV2'
])
param apimSku string = 'BasicV2'

param apimCapacity int = 1

@description('Required by APIM: where service notifications go.')
param publisherEmail string

param publisherName string = 'Croma agentic pricing'

param tags object = {
  project: 'croma-agentic-pricing'
  version: 'v2'
  purpose: 'apim-gateways'
}

// Headers worth having in the request logs. Never add Authorization, x-api-key or Ocp-Apim-Subscription-Key here: the value of every listed header is stored.
var loggedFrontendHeaders = [
  'x-correlation-id'
  'User-Agent'
  'Content-Type'
  'Accept'
]
var loggedBackendHeaders = [
  'x-correlation-id'
  'x-user-id'
  'Content-Type'
]
// Request and response bodies are never logged: bytes 0. (Body logging would also buffer the Server-Sent Events stream of the chat API.)
var noBody = {
  bytes: 0
}

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law-${prefix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${prefix}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
  }
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: apimName
  location: location
  tags: tags
  sku: {
    name: apimSku
    capacity: apimCapacity
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appi.id
    credentials: {
      instrumentationKey: appi.properties.InstrumentationKey
    }
    isBuffered: true
  }
}

// Service-wide default for every API. gateway.bicep repeats the same settings per API, so a change here cannot start logging bodies by accident.
resource diagnostics 'Microsoft.ApiManagement/service/diagnostics@2024-05-01' = {
  parent: apim
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    logClientIp: true
    httpCorrelationProtocol: 'W3C'
    verbosity: 'information'
    metrics: true
    frontend: {
      request: {
        headers: loggedFrontendHeaders
        body: noBody
      }
      response: {
        headers: [ 'x-correlation-id' ]
        body: noBody
      }
    }
    backend: {
      request: {
        headers: loggedBackendHeaders
        body: noBody
      }
      response: {
        headers: [ 'Content-Type' ]
        body: noBody
      }
    }
  }
}

resource gatewayLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-log-analytics'
  scope: apim
  properties: {
    workspaceId: law.id
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      {
        category: 'GatewayLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output apimName string = apim.name
output apimId string = apim.id
output gatewayUrl string = apim.properties.gatewayUrl
output principalId string = apim.identity.principalId
output tenantId string = apim.identity.tenantId
output loggerId string = logger.id
output appInsightsName string = appi.name
output logAnalyticsName string = law.name
