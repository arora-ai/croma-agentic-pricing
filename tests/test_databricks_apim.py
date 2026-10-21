"""The Databricks Genie backend's three ways of signing in (managed identity, token, and the APIM data gateway), against a mocked HTTP transport."""
import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))

SPACE = "space-123"
GATEWAY = "https://apim-test.azure-api.net/dbx"
WORKSPACE = "https://adb-1.2.azuredatabricks.net"
ENV_NAMES = ("DATABRICKS_AUTH", "DATABRICKS_HOST", "GENIE_SPACE_ID", "DATABRICKS_TOKEN", "APIM_SUBSCRIPTION_KEY")


def load_backend(monkeypatch, **env):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for module in ("settings", "backends", "backends.base", "backends.databricks_genie", "azure_clients"):
        sys.modules.pop(module, None)
    return importlib.import_module("backends.databricks_genie")


def fake_genie(backend, monkeypatch, status=200):
    """Answers the three Genie calls the backend makes and records every request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "denied"})
        path = request.url.path
        if request.method == "POST" and path.endswith("/start-conversation"):
            return httpx.Response(200, json={"conversation_id": "c1", "message_id": "m1"})
        if request.method == "GET" and path.endswith("/conversations/c1/messages/m1"):
            return httpx.Response(200, json={"status": "COMPLETED", "attachments": [{"attachment_id": "a1", "query": {"query": "select zone, nmv from t", "description": "NMV by zone"}}]})
        if request.method == "GET" and path.endswith("/messages/m1/attachments/a1/query-result"):
            columns = [{"name": "zone", "type_name": "STRING"}, {"name": "nmv", "type_name": "DOUBLE"}]
            return httpx.Response(200, json={"statement_response": {"manifest": {"schema": {"columns": columns}, "total_row_count": 2},
                                                                    "result": {"data_array": [["West", "1.5"], ["East", "2.5"]]}}})
        return httpx.Response(404, json={"error": f"unexpected {request.method} {path}"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    return seen


def ask(backend, question="NMV by zone"):
    return asyncio.run(backend.ask(question))


def test_apim_mode_sends_the_subscription_key_and_no_authorization_header(monkeypatch):
    backend = load_backend(monkeypatch, DATABRICKS_AUTH="apim", DATABRICKS_HOST=GATEWAY + "/", GENIE_SPACE_ID=SPACE, APIM_SUBSCRIPTION_KEY="apim-key-1")
    seen = fake_genie(backend, monkeypatch)
    out = ask(backend)
    assert out["columns"] == ["zone", "nmv"] and out["rows"] == [["West", 1.5], ["East", 2.5]] and out["sql"] == "select zone, nmv from t"
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", f"/dbx/api/2.0/genie/spaces/{SPACE}/start-conversation"),
        ("GET", f"/dbx/api/2.0/genie/spaces/{SPACE}/conversations/c1/messages/m1"),
        ("GET", f"/dbx/api/2.0/genie/spaces/{SPACE}/conversations/c1/messages/m1/attachments/a1/query-result"),
    ]
    assert all(r.url.host == "apim-test.azure-api.net" for r in seen)
    assert all(r.headers["Ocp-Apim-Subscription-Key"] == "apim-key-1" and "authorization" not in r.headers for r in seen)


def test_apim_mode_without_a_key_fails_before_any_request(monkeypatch):
    backend = load_backend(monkeypatch, DATABRICKS_AUTH="apim", DATABRICKS_HOST=GATEWAY, GENIE_SPACE_ID=SPACE)
    seen = fake_genie(backend, monkeypatch)
    out = ask(backend)
    assert "APIM subscription key" in out["error"] and "APIM_SUBSCRIPTION_KEY" in out["error"] and seen == []


def test_apim_mode_reports_a_refusal_from_the_gateway(monkeypatch):
    backend = load_backend(monkeypatch, DATABRICKS_AUTH="apim", DATABRICKS_HOST=GATEWAY, GENIE_SPACE_ID=SPACE, APIM_SUBSCRIPTION_KEY="wrong-key")
    fake_genie(backend, monkeypatch, status=401)
    assert "Genie request failed (401)" in ask(backend)["error"]


def test_token_mode_still_sends_a_bearer_token_and_no_subscription_key(monkeypatch):
    backend = load_backend(monkeypatch, DATABRICKS_AUTH="token", DATABRICKS_HOST=WORKSPACE, GENIE_SPACE_ID=SPACE, DATABRICKS_TOKEN="dapi-test-token",
                           APIM_SUBSCRIPTION_KEY="ignored-in-this-mode")
    seen = fake_genie(backend, monkeypatch)
    assert ask(backend)["row_count"] == 2
    assert all(r.headers["authorization"] == "Bearer dapi-test-token" and "ocp-apim-subscription-key" not in r.headers for r in seen)
    assert seen[0].url.path == f"/api/2.0/genie/spaces/{SPACE}/start-conversation"


def test_token_mode_without_a_token_keeps_its_message(monkeypatch):
    backend = load_backend(monkeypatch, DATABRICKS_AUTH="token", DATABRICKS_HOST=WORKSPACE, GENIE_SPACE_ID=SPACE)
    assert "Could not get a Databricks token" in ask(backend)["error"]


def test_managed_identity_mode_still_asks_entra_for_a_databricks_token(monkeypatch):
    backend = load_backend(monkeypatch, DATABRICKS_HOST=WORKSPACE, GENIE_SPACE_ID=SPACE)
    import azure_clients

    scopes = []
    credential = SimpleNamespace(get_token=lambda scope: scopes.append(scope) or SimpleNamespace(token="entra-token"))
    monkeypatch.setattr(azure_clients, "credential", lambda: credential)
    seen = fake_genie(backend, monkeypatch)
    assert ask(backend)["row_count"] == 2
    assert scopes == [f"{backend.DATABRICKS_RESOURCE}/.default"] and backend.DATABRICKS_RESOURCE == "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
    assert all(r.headers["authorization"] == "Bearer entra-token" and "ocp-apim-subscription-key" not in r.headers for r in seen)


@pytest.mark.parametrize("mode", ["apim", "token", "managed_identity"])
def test_only_the_three_genie_calls_are_made(monkeypatch, mode):
    """The data gateway's allow-list (infra/apim/modules/gateway.bicep) is exactly these three calls: start, poll, read the result."""
    backend = load_backend(monkeypatch, DATABRICKS_AUTH=mode, DATABRICKS_HOST=GATEWAY, GENIE_SPACE_ID=SPACE, DATABRICKS_TOKEN="t", APIM_SUBSCRIPTION_KEY="k")
    if mode == "managed_identity":
        import azure_clients

        monkeypatch.setattr(azure_clients, "credential", lambda: SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="t")))
    seen = fake_genie(backend, monkeypatch)
    ask(backend)
    shapes = [f"{r.method} {r.url.path.split(SPACE, 1)[1].rsplit('/', 1)[-1] if r.method == 'POST' else r.url.path.rsplit('/', 1)[-1]}" for r in seen]
    assert shapes == ["POST start-conversation", "GET m1", "GET query-result"]
