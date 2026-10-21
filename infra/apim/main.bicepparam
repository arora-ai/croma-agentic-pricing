using 'main.bicep'

// Every value comes from an environment variable that infra/apim/deploy_apim.ps1 sets for its own process only, so nothing secret is written to disk
// or placed on a command line. The second argument is the default when the variable is not set.
param prefix = readEnvironmentVariable('APIM_PREFIX', 'croma2')
param location = readEnvironmentVariable('APIM_LOCATION', 'eastus2')
param apimName = readEnvironmentVariable('APIM_NAME', 'apim-croma2')
param apimSku = readEnvironmentVariable('APIM_SKU', 'BasicV2')
param deployPlatform = toLower(readEnvironmentVariable('APIM_DEPLOY_PLATFORM', 'true')) == 'true'
param publisherEmail = readEnvironmentVariable('APIM_PUBLISHER_EMAIL')

param chatBackendUrl = readEnvironmentVariable('APIM_CHAT_BACKEND_URL')
param hubBackendUrl = readEnvironmentVariable('APIM_HUB_BACKEND_URL')
param databricksUrl = readEnvironmentVariable('APIM_DATABRICKS_URL')
param genieSpaceId = readEnvironmentVariable('APIM_GENIE_SPACE_ID')

param hubApiKey = readEnvironmentVariable('APIM_HUB_API_KEY')
param gatewayKey = readEnvironmentVariable('APIM_GATEWAY_KEY')

param jwtEnabled = toLower(readEnvironmentVariable('APIM_JWT_ENABLED', 'false')) == 'true'
param entraTenantId = readEnvironmentVariable('APIM_ENTRA_TENANT_ID')
param entraAudience = readEnvironmentVariable('APIM_ENTRA_AUDIENCE', 'api://not-configured')
param entraAudienceAlt = readEnvironmentVariable('APIM_ENTRA_AUDIENCE_ALT', 'api://not-configured')
param corsOrigins = readEnvironmentVariable('APIM_CORS_ORIGINS', '')

param frontCallsPerMinute = int(readEnvironmentVariable('APIM_FRONT_CALLS_PER_MINUTE', '120'))
param chatTurnsPerMinute = int(readEnvironmentVariable('APIM_CHAT_TURNS_PER_MINUTE', '10'))
param chatTurnsPerDay = int(readEnvironmentVariable('APIM_CHAT_TURNS_PER_DAY', '0'))
param staticCallsPerMinute = int(readEnvironmentVariable('APIM_STATIC_CALLS_PER_MINUTE', '120'))
param toolsCallsPerMinute = int(readEnvironmentVariable('APIM_TOOLS_CALLS_PER_MINUTE', '600'))
param dataCallsPerMinute = int(readEnvironmentVariable('APIM_DATA_CALLS_PER_MINUTE', '600'))
param dataQuestionsPerMinute = int(readEnvironmentVariable('APIM_DATA_QUESTIONS_PER_MINUTE', '30'))
param chatTimeoutSeconds = int(readEnvironmentVariable('APIM_CHAT_TIMEOUT_SECONDS', '3600'))
param exposeStreamCheck = toLower(readEnvironmentVariable('APIM_EXPOSE_STREAM_CHECK', 'true')) == 'true'
