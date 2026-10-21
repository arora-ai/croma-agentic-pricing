"""Gives the hub's managed identity the access it needs in a Databricks workspace, so the Databricks Genie backend works with no secrets.

    python integrations/genie/grant_hub_access.py --host https://adb-<id>.<n>.azuredatabricks.net --warehouse-id <id> --space-id <id> --catalog <catalog> \
        --hub-app croma-tools-hub --resource-group <rg>

What it does (safe to re-run), signed in as you (az login; you must be a Databricks workspace admin):
  1. finds the hub's identity (the container app's system-assigned identity) and its application id
  2. adds it to the workspace as a service principal
  3. USE CATALOG and SELECT on the catalog the Genie space reads, CAN_USE on the SQL warehouse, CAN_RUN on the Genie space

If your organisation manages Databricks access differently (a SCIM provisioning pipeline, Terraform), give the same three permissions to the same
identity there instead. Pass --application-id to skip step 1 when you already know the identity's application (client) id.
"""
from __future__ import annotations

import argparse
import subprocess
import time

import httpx
from azure.identity import AzureCliCredential

DATABRICKS_RESOURCE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"  # the Azure Databricks first-party application


def az(*args: str) -> str:
    out = subprocess.run(["az", *args, "-o", "tsv"], capture_output=True, text=True, shell=True)
    if out.returncode:
        raise SystemExit(f"az {' '.join(args[:3])} failed: {out.stderr.strip()[-300:]}")
    return out.stdout.strip()


class Workspace:
    def __init__(self, host: str):
        self.host = host.rstrip("/")
        self.http = httpx.Client(timeout=120)
        self.cred = AzureCliCredential(process_timeout=120)  # the default 10 seconds is too short on a slow machine

    def call(self, method: str, path: str, **kw):
        token = self.cred.get_token(f"{DATABRICKS_RESOURCE}/.default").token
        r = self.http.request(method, self.host + path, headers={"Authorization": f"Bearer {token}"}, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}

    def sql(self, warehouse_id: str, statement: str) -> None:
        r = self.call("POST", "/api/2.0/sql/statements", json={"warehouse_id": warehouse_id, "statement": statement, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
        while r["status"]["state"] in ("PENDING", "RUNNING"):  # the warehouse may need a minute or two to start
            time.sleep(3)
            r = self.call("GET", f"/api/2.0/sql/statements/{r['statement_id']}")
        if r["status"]["state"] != "SUCCEEDED":
            raise SystemExit(f"SQL failed: {r['status'].get('error', {}).get('message', r['status'])}\n  {statement}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", required=True)
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--space-id", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--hub-app", help="name of the hub container app")
    p.add_argument("--resource-group", help="resource group of the hub container app")
    p.add_argument("--application-id", help="the identity's application (client) id, if you know it")
    p.add_argument("--name", default="", help="display name for the service principal (default: the hub app name)")
    a = p.parse_args()

    app_id = a.application_id
    if not app_id:
        if not (a.hub_app and a.resource_group):
            raise SystemExit("give --hub-app and --resource-group, or --application-id")
        principal = az("containerapp", "show", "-n", a.hub_app, "-g", a.resource_group, "--query", "identity.principalId")
        app_id = az("ad", "sp", "show", "--id", principal, "--query", "appId")
    ws = Workspace(a.host)
    existing = ws.call("GET", "/api/2.0/preview/scim/v2/ServicePrincipals", params={"filter": f'applicationId eq "{app_id}"'}).get("Resources", [])
    if not existing:
        ws.call("POST", "/api/2.0/preview/scim/v2/ServicePrincipals", json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"], "applicationId": app_id, "displayName": a.name or a.hub_app or "croma-hub",
            "active": True, "entitlements": [{"value": "databricks-sql-access"}, {"value": "workspace-access"}]})
        print(f"Added {app_id} to the workspace")
    ws.sql(a.warehouse_id, f"GRANT USE CATALOG ON CATALOG {a.catalog} TO `{app_id}`")
    ws.sql(a.warehouse_id, f"GRANT USE SCHEMA, SELECT ON CATALOG {a.catalog} TO `{app_id}`")
    ws.call("PATCH", f"/api/2.0/permissions/sql/warehouses/{a.warehouse_id}", json={"access_control_list": [{"service_principal_name": app_id, "permission_level": "CAN_USE"}]})
    ws.call("PATCH", f"/api/2.0/permissions/genie/{a.space_id}", json={"access_control_list": [{"service_principal_name": app_id, "permission_level": "CAN_RUN"}]})
    print(f"Identity {app_id} can use the warehouse, read catalog {a.catalog} and run the Genie space.")


if __name__ == "__main__":
    main()
