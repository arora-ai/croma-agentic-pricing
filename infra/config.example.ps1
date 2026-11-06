# Copy this file to infra/config.ps1 (which is git-ignored) and fill in every value. The deploy scripts read it; nothing here is a secret, but it
# identifies your Azure environment, so it stays out of the repository.
$Cfg = @{
    Subscription  = "<subscription id>"
    ResourceGroup = "<resource group that holds the Foundry account and will hold the hub>"
    Acr           = "<azure container registry name, without .azurecr.io>"
    Environment   = "<container apps environment name>"
    Foundry       = "<foundry (AI services) account name>"
    Project       = "<foundry project name>"
    App           = "croma-tools-hub"                        # name of the container app that runs the hub
    NamePrefix    = "croma"                                  # prefix of every Foundry agent, connection and the memory store: <prefix>-cx, <prefix>-hub-genie ...
                                                             # use a different prefix (and App) to run a second copy of the system side by side
}
$Cfg.ProjectEndpoint = "https://$($Cfg.Foundry).services.ai.azure.com/api/projects/$($Cfg.Project)"
$Cfg.FoundryScope = "/subscriptions/$($Cfg.Subscription)/resourceGroups/$($Cfg.ResourceGroup)/providers/Microsoft.CognitiveServices/accounts/$($Cfg.Foundry)"

# The data layer the Data agent talks to. See integrations/genie/README.md.
#   Backend: "databricks"  a Databricks Genie space (set DatabricksHost and SpaceId)
#            "custom"      your own function (set CustomBackend, for example "my_backend:ask")
#            "stub"        no data source; every question answers "not configured" (the default while you set things up)
$Cfg.Genie = @{
    Backend         = "stub"
    DatabricksHost  = ""        # https://adb-<workspace-id>.<n>.azuredatabricks.net   (with AuthMode = "apim": the data gateway's URL, APIM_DATA_URL in .apim.env)
    SpaceId         = ""        # the Genie space id (the long id in the space's URL)
    AuthMode        = "managed_identity"   # managed_identity | token | apim
                                           #   token  reads $env:DATABRICKS_TOKEN at deploy time and stores it as a container app secret
                                           #   apim   the hub calls Databricks through the Azure API Management data gateway; its subscription key is read from
                                           #          $env:APIM_SUBSCRIPTION_KEY or APIM_DATA_KEY in .apim.env at deploy time and stored as a container app secret
    CustomBackend   = ""
    ToolDescription = ""        # one or two sentences telling the Data agent what this data space covers (optional but worth writing)
}

# Optional: SharePoint as a second data source for the Analysis and Research journeys (docs/SHAREPOINT.md). Leave this out to run without it.
#   Backend: "graph"   reads one document library of a SharePoint site through Microsoft Graph (set SiteUrl; the hub's managed identity needs read access to that site)
#            "folder"  reads a folder bundled with the hub as if it were a library: for trying the connector on synthetic files
#            "off"     (the default) SharePoint is not offered to the agents
# The agents are told what the library holds by the "sharepoint" block of config/data_profile.json (set "enabled" there as well), and deploy_agents.py must be re-run.
# $Cfg.SharePoint = @{
#     Backend         = "graph"
#     SiteUrl         = "https://<tenant>.sharepoint.com/sites/<site>"
#     Library         = ""        # the document library to read; empty means the site's default library
#     ToolDescription = ""        # one or two sentences telling the SharePoint agent what the library holds (optional)
# }

# Optional: Azure API Management in front of the system (infra/apim, docs/APIM.md). Leave this out to run without it.
# When GatewayUrl is set, infra/create_hub_connection.ps1 points the five Foundry project connections at the tool gateway and stores the APIM subscription key
# in them instead of the hub key (the hub key then lives only in APIM). Without it the connections point straight at the hub, as before.
# $Cfg.Apim = @{
#     GatewayUrl         = "https://<apim name>.azure-api.net/tools"   # APIM_TOOLS_URL in .apim.env, written by infra/apim/deploy_apim.ps1
#     SubscriptionKeyEnv = "APIM_TOOLS_KEY"                            # the environment variable (or the .apim.env entry) that holds the tool gateway's subscription key
#     # Used by infra/apim/deploy_apim.ps1, all optional:
#     ResourceGroup      = "rg-croma-v2"                               # a separate resource group, so one command removes the whole gateway
#     Name               = "apim-croma2"                               # globally unique: <name>.azure-api.net
#     Location           = "eastus2"                                   # default: the region of the Container Apps environment
#     ChatApp            = "croma-chat-v2"                             # the container app that runs the chat API behind the front gateway
# }
