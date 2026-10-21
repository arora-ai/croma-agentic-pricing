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
    DatabricksHost  = ""        # https://adb-<workspace-id>.<n>.azuredatabricks.net
    SpaceId         = ""        # the Genie space id (the long id in the space's URL)
    AuthMode        = "managed_identity"   # managed_identity | token  (token reads $env:DATABRICKS_TOKEN at deploy time and stores it as a container app secret)
    CustomBackend   = ""
    ToolDescription = ""        # one or two sentences telling the Data agent what this data space covers (optional but worth writing)
}
