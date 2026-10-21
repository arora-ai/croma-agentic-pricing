// The three gateways, defined on an existing APIM instance (see platform.bicep):
//   chat-api   FRONT gateway       browsers and channels  -> APIM -> chat API (Container App) -> Foundry CX agent
//   tools-api  TOOL gateway        Foundry agents         -> APIM -> tools hub MCP endpoints
//   data-api   DATA TOOL gateway   the hub's Genie backend -> APIM -> Azure Databricks Genie REST API
// Each has a backend, its operations (an allow-list: anything not listed is a 404), a policy loaded from ../policies, a product and a subscription.
// What each policy does and why: docs/APIM.md.

param apimName string

@description('Application Insights logger of the APIM instance (platform.bicep).')
param loggerId string

param prefix string

@description('Base URL of the chat API container app, https://<app>.<environment domain>.')
param chatBackendUrl string

@description('Base URL of the tools hub, https://<hub app>.<environment domain>.')
param hubBackendUrl string

@description('Azure Databricks workspace URL, https://adb-<id>.<n>.azuredatabricks.net.')
param databricksUrl string

@description('The one Genie space the data gateway lets through.')
param genieSpaceId string

@secure()
@description('The hub API key. Held only here, as a secret named value; the tool gateway swaps it in for the caller\'s APIM key.')
param hubApiKey string

@secure()
@description('Shared secret between this gateway and the chat API. The chat API believes x-user-id only when it arrives with this value in x-gateway-key.')
param gatewayKey string

@description('Validate Entra ID access tokens on the front gateway. When false, only an APIM subscription key is accepted.')
param jwtEnabled bool = false

param entraTenantId string = tenant().tenantId

@description('Accepted audience of the access tokens (the aud claim): api://<api client id>, and/or the bare client id for v2 tokens.')
param entraAudience string = 'api://not-configured'

param entraAudienceAlt string = 'api://not-configured'

@description('Extra origins allowed to call the front API from a browser. The gateway\'s own URL is always allowed (the web app is served from it).')
param corsOrigins array = []

@description('Calls per minute per user across the whole front API.')
param frontCallsPerMinute int = 120

@description('Chat turns (POST /api/chat) per minute per user.')
param chatTurnsPerMinute int = 10

@description('Chat turns per user per day. 0 = no daily quota.')
param chatTurnsPerDay int = 0

@description('Calls per minute per client address for the web app\'s own files.')
param staticCallsPerMinute int = 120

param toolsCallsPerMinute int = 600

@description('Calls per minute per subscription to Genie, including the status polls (about one every 1.5 seconds per running question).')
param dataCallsPerMinute int = 600

@description('New Genie questions per minute per subscription.')
param dataQuestionsPerMinute int = 30

@description('Seconds APIM waits for the chat API to start answering (a streamed answer then runs as long as events or keep-alives keep coming).')
param chatTimeoutSeconds int = 3600

@description('Expose GET /api/stream-check, a synthetic event stream for testing streaming. The chat API serves it only when ENABLE_STREAM_CHECK is set.')
param exposeStreamCheck bool = true

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

// ---------------------------------------------------------------- named values and backends

resource nvHubKey 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'hub-api-key'
  properties: {
    displayName: 'hub-api-key'
    secret: true
    value: hubApiKey
  }
}

resource nvGatewayKey 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'gateway-key'
  properties: {
    displayName: 'gateway-key'
    secret: true
    value: gatewayKey
  }
}

resource nvJwtEnabled 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'jwt-enabled'
  properties: {
    displayName: 'jwt-enabled'
    value: jwtEnabled ? 'true' : 'false'
  }
}

resource nvTenant 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'entra-tenant-id'
  properties: {
    displayName: 'entra-tenant-id'
    value: entraTenantId
  }
}

resource nvAudience 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'entra-audience'
  properties: {
    displayName: 'entra-audience'
    value: entraAudience
  }
}

resource nvAudienceAlt 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'entra-audience-alt'
  properties: {
    displayName: 'entra-audience-alt'
    value: entraAudienceAlt
  }
}

resource nvGenieSpace 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'genie-space-id'
  properties: {
    displayName: 'genie-space-id'
    value: genieSpaceId
  }
}

resource chatBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'chat-backend'
  properties: {
    title: 'Chat API'
    description: 'The chat API container app (FastAPI, serves the web app and /api).'
    url: chatBackendUrl
    protocol: 'http'
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

resource hubBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'hub-backend'
  properties: {
    title: 'Tools hub'
    description: 'The tools hub container app: the four MCP servers.'
    url: hubBackendUrl
    protocol: 'http'
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

resource databricksBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'databricks-backend'
  properties: {
    title: 'Azure Databricks workspace'
    description: 'The Databricks workspace that hosts the Genie space.'
    url: databricksUrl
    protocol: 'http'
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

// ---------------------------------------------------------------- logging: same settings on every API, and never a body

var noBody = {
  bytes: 0
}
var diagnosticSettings = {
  loggerId: loggerId
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
      headers: [
        'x-correlation-id'
        'User-Agent'
        'Content-Type'
        'Accept'
      ]
      body: noBody
    }
    response: {
      headers: [ 'x-correlation-id' ]
      body: noBody
    }
  }
  backend: {
    request: {
      headers: [
        'x-correlation-id'
        'x-user-id'
        'Content-Type'
      ]
      body: noBody
    }
    response: {
      headers: [ 'Content-Type' ]
      body: noBody
    }
  }
}

// ---------------------------------------------------------------- policies from ../policies with the numbers filled in

var gatewayOrigin = apim.properties.gatewayUrl
var corsOriginsXml = join(map(concat([ gatewayOrigin ], corsOrigins), origin => '<origin>${origin}</origin>'), '')

var frontPolicy = replace(replace(replace(loadTextContent('../policies/front-api.xml'), '__CORS_ORIGINS__', corsOriginsXml), '__API_CALLS__', string(frontCallsPerMinute)), '__TIMEOUT__', string(chatTimeoutSeconds))
var chatQuotaXml = chatTurnsPerDay > 0 ? replace(loadTextContent('../policies/fragment-chat-quota.xml'), '__CHAT_QUOTA_CALLS__', string(chatTurnsPerDay)) : ''
var chatTurnPolicy = replace(replace(loadTextContent('../policies/front-chat-turn.xml'), '__CHAT_CALLS__', string(chatTurnsPerMinute)), '__CHAT_QUOTA__', chatQuotaXml)
var staticPolicy = replace(loadTextContent('../policies/front-static.xml'), '__STATIC_CALLS__', string(staticCallsPerMinute))
var toolsPolicy = replace(loadTextContent('../policies/tools-api.xml'), '__TOOLS_CALLS__', string(toolsCallsPerMinute))
var dataPolicy = replace(loadTextContent('../policies/data-api.xml'), '__DATA_CALLS__', string(dataCallsPerMinute))
var dataQuestionPolicy = replace(loadTextContent('../policies/data-start-conversation.xml'), '__DATA_QUESTIONS__', string(dataQuestionsPerMinute))

// ================================================================ FRONT gateway: the chat API

resource chatApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'chat-api'
  properties: {
    displayName: 'Croma chat API (front gateway)'
    description: 'Browser and channel traffic to the Croma assistant: authenticated here, then forwarded to the chat API with the caller\'s identity.'
    path: ''
    protocols: [ 'https' ]
    serviceUrl: chatBackendUrl
    apiType: 'http'
    // Not required at the API level because the policy accepts an Entra token or a subscription key; the policy returns 401 when there is neither.
    subscriptionRequired: false
    subscriptionKeyParameterNames: {
      header: 'Ocp-Apim-Subscription-Key'
      query: 'subscription-key'
    }
  }
}

// Operations of the chat API (ui/backend/main.py). Everything else, for example the interactive docs, is not reachable through the gateway.
var chatOperations = [
  { id: 'health', method: 'GET', url: '/api/health', parameters: [] }
  { id: 'me', method: 'GET', url: '/api/me', parameters: [] }
  { id: 'case', method: 'GET', url: '/api/case/{case_id}', parameters: [ 'case_id' ] }
  { id: 'report', method: 'GET', url: '/api/report/{case_id}/{n}/{ext}', parameters: [ 'case_id', 'n', 'ext' ] }
  { id: 'conversations-list', method: 'GET', url: '/api/conversations', parameters: [] }
  { id: 'conversations-get', method: 'GET', url: '/api/conversations/{conversation_id}', parameters: [ 'conversation_id' ] }
  { id: 'conversations-rename', method: 'PATCH', url: '/api/conversations/{conversation_id}', parameters: [ 'conversation_id' ] }
  { id: 'conversations-delete', method: 'DELETE', url: '/api/conversations/{conversation_id}', parameters: [ 'conversation_id' ] }
  { id: 'memory-list', method: 'GET', url: '/api/memory', parameters: [] }
  { id: 'memory-add', method: 'POST', url: '/api/memory', parameters: [] }
  { id: 'memory-delete', method: 'DELETE', url: '/api/memory/{memory_id}', parameters: [ 'memory_id' ] }
  { id: 'memory-clear', method: 'DELETE', url: '/api/memory', parameters: [] }
]
var streamCheckOperations = exposeStreamCheck ? [
  { id: 'stream-check', method: 'GET', url: '/api/stream-check', parameters: [] }
] : []

resource chatOps 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for op in concat(chatOperations, streamCheckOperations): {
  parent: chatApi
  name: op.id
  properties: {
    displayName: '${op.method} ${op.url}'
    method: op.method
    urlTemplate: op.url
    templateParameters: [for p in op.parameters: {
      name: p
      type: 'string'
      required: true
    }]
  }
}]

// The one call that starts an agent turn: Server-Sent Events back, a stricter rate limit and an optional daily quota.
resource chatTurnOp 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: chatApi
  name: 'chat-turn'
  properties: {
    displayName: 'POST /api/chat (streams Server-Sent Events)'
    method: 'POST'
    urlTemplate: '/api/chat'
  }
}

// The web app's files. They have to load before sign-in, so their policy does not authenticate.
var staticOperations = [
  { id: 'web-app', url: '/' }
  { id: 'web-assets', url: '/assets/*' }
]

resource staticOps 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for op in staticOperations: {
  parent: chatApi
  name: op.id
  properties: {
    displayName: 'GET ${op.url} (web app files)'
    method: 'GET'
    urlTemplate: op.url
  }
}]

resource chatApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: chatApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: frontPolicy
  }
  dependsOn: [
    nvGatewayKey
    nvJwtEnabled
    nvTenant
    nvAudience
    nvAudienceAlt
    chatBackend
  ]
}

resource chatTurnPolicyResource 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = {
  parent: chatTurnOp
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: chatTurnPolicy
  }
  dependsOn: [
    chatApiPolicy
  ]
}

resource staticPolicies 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = [for (op, i) in staticOperations: {
  parent: staticOps[i]
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: staticPolicy
  }
  dependsOn: [
    chatApiPolicy
  ]
}]

resource chatApiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: chatApi
  name: 'applicationinsights'
  properties: diagnosticSettings
}

// ================================================================ TOOL gateway: the hub's MCP endpoints

resource toolsApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'tools-api'
  properties: {
    displayName: 'Croma tools hub (tool gateway)'
    description: 'MCP endpoints of the tools hub for Foundry agents. The subscription key goes in the x-api-key header, the format of the existing Foundry connections.'
    path: 'tools'
    protocols: [ 'https' ]
    serviceUrl: hubBackendUrl
    apiType: 'http'
    subscriptionRequired: true
    subscriptionKeyParameterNames: {
      header: 'x-api-key'
      query: 'subscription-key'
    }
  }
}

var toolEndpoints = [ 'genie', 'agents', 'context', 'studio' ]
var toolOperations = flatten(map(toolEndpoints, endpoint => map([ 'POST', 'GET', 'DELETE' ], method => {
  id: '${endpoint}-${toLower(method)}'
  method: method
  url: '/${endpoint}/mcp'
})))

resource toolsOps 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for op in toolOperations: {
  parent: toolsApi
  name: op.id
  properties: {
    displayName: '${op.method} ${op.url}'
    method: op.method
    urlTemplate: op.url
  }
}]

resource toolsApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: toolsApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: toolsPolicy
  }
  dependsOn: [
    nvHubKey
    hubBackend
  ]
}

resource toolsApiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: toolsApi
  name: 'applicationinsights'
  properties: diagnosticSettings
}

// ================================================================ DATA TOOL gateway: Databricks Genie REST

resource dataApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'data-api'
  properties: {
    displayName: 'Databricks Genie (data tool gateway)'
    description: 'The Genie conversation calls the hub makes. APIM signs in to Databricks with its managed identity.'
    path: 'dbx'
    protocols: [ 'https' ]
    serviceUrl: databricksUrl
    apiType: 'http'
    subscriptionRequired: true
    subscriptionKeyParameterNames: {
      header: 'Ocp-Apim-Subscription-Key'
      query: 'subscription-key'
    }
  }
}

// Exactly the calls hub/backends/databricks_genie.py makes.
resource dataStartOp 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: dataApi
  name: 'genie-start-conversation'
  properties: {
    displayName: 'Start a Genie conversation (ask a question)'
    method: 'POST'
    urlTemplate: '/api/2.0/genie/spaces/{space_id}/start-conversation'
    templateParameters: [
      {
        name: 'space_id'
        type: 'string'
        required: true
      }
    ]
  }
}

resource dataMessageOp 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: dataApi
  name: 'genie-get-message'
  properties: {
    displayName: 'Get a Genie message (status and attachments)'
    method: 'GET'
    urlTemplate: '/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}'
    templateParameters: [
      {
        name: 'space_id'
        type: 'string'
        required: true
      }
      {
        name: 'conversation_id'
        type: 'string'
        required: true
      }
      {
        name: 'message_id'
        type: 'string'
        required: true
      }
    ]
  }
}

resource dataResultOp 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: dataApi
  name: 'genie-query-result'
  properties: {
    displayName: 'Get the result rows of a Genie query'
    method: 'GET'
    urlTemplate: '/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}/attachments/{attachment_id}/query-result'
    templateParameters: [
      {
        name: 'space_id'
        type: 'string'
        required: true
      }
      {
        name: 'conversation_id'
        type: 'string'
        required: true
      }
      {
        name: 'message_id'
        type: 'string'
        required: true
      }
      {
        name: 'attachment_id'
        type: 'string'
        required: true
      }
    ]
  }
}

resource dataApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: dataApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: dataPolicy
  }
  dependsOn: [
    nvGenieSpace
    databricksBackend
  ]
}

resource dataStartPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = {
  parent: dataStartOp
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: dataQuestionPolicy
  }
  dependsOn: [
    dataApiPolicy
  ]
}

resource dataApiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: dataApi
  name: 'applicationinsights'
  properties: diagnosticSettings
}

// ================================================================ products and subscriptions
// One product per gateway, so each can be limited and revoked on its own. The keys are generated by APIM and never appear in a template or an
// output: infra/apim/deploy_apim.ps1 reads them from APIM and writes them to the git-ignored .apim.env.

resource frontProduct 'Microsoft.ApiManagement/service/products@2024-05-01' = {
  parent: apim
  name: '${prefix}-front'
  properties: {
    displayName: '${prefix} front gateway'
    description: 'Channels and test clients that call the chat API with a subscription key instead of a user token.'
    state: 'published'
    subscriptionRequired: true
    approvalRequired: false
  }
}

resource frontProductApi 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = {
  parent: frontProduct
  name: chatApi.name
}

resource toolsProduct 'Microsoft.ApiManagement/service/products@2024-05-01' = {
  parent: apim
  name: '${prefix}-tools'
  properties: {
    displayName: '${prefix} tool gateway'
    description: 'Foundry project connections to the hub\'s MCP endpoints.'
    state: 'published'
    subscriptionRequired: true
    approvalRequired: false
  }
}

resource toolsProductApi 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = {
  parent: toolsProduct
  name: toolsApi.name
}

resource dataProduct 'Microsoft.ApiManagement/service/products@2024-05-01' = {
  parent: apim
  name: '${prefix}-data'
  properties: {
    displayName: '${prefix} data tool gateway'
    description: 'The hub\'s Databricks Genie backend (DATABRICKS_AUTH=apim).'
    state: 'published'
    subscriptionRequired: true
    approvalRequired: false
  }
}

resource dataProductApi 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = {
  parent: dataProduct
  name: dataApi.name
}

resource frontSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = {
  parent: apim
  name: 'front-channel'
  properties: {
    displayName: 'Front channel (tests, Teams, schedulers)'
    scope: frontProduct.id
    state: 'active'
    allowTracing: false
  }
  dependsOn: [
    frontProductApi
  ]
}

resource toolsSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = {
  parent: apim
  name: 'tools-foundry'
  properties: {
    displayName: 'Foundry project connections'
    scope: toolsProduct.id
    state: 'active'
    allowTracing: false
  }
  dependsOn: [
    toolsProductApi
  ]
}

resource dataSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = {
  parent: apim
  name: 'data-hub'
  properties: {
    displayName: 'Hub data backend'
    scope: dataProduct.id
    state: 'active'
    allowTracing: false
  }
  dependsOn: [
    dataProductApi
  ]
}

output gatewayUrl string = gatewayOrigin
output frontUrl string = gatewayOrigin
output toolsUrl string = '${gatewayOrigin}/${toolsApi.properties.path}'
output dataUrl string = '${gatewayOrigin}/${dataApi.properties.path}'
output subscriptionNames object = {
  front: frontSubscription.name
  tools: toolsSubscription.name
  data: dataSubscription.name
}
