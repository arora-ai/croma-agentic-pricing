"""Guards for the API Management definitions in infra/apim: the Bicep and the policy files agree with each other and with the code they sit in front of.
Nothing here needs Azure; the live checks are infra/apim/test_gateway.ps1."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APIM = ROOT / "infra" / "apim"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BICEP = (APIM / "modules" / "gateway.bicep").read_text(encoding="utf-8")
POLICIES = {p.name: p.read_text(encoding="utf-8") for p in sorted((APIM / "policies").glob("*.xml"))}
ALL_POLICY_TEXT = "\n".join(POLICIES.values())


def section(start: str, end: str | None = None) -> str:
    """The part of the gateway Bicep between two banner comments."""
    text = BICEP[BICEP.index(start):]
    return text[:text.index(end)] if end else text


def test_every_policy_file_is_loaded_by_the_bicep_and_every_placeholder_is_filled():
    for name in POLICIES:
        assert f"loadTextContent('../policies/{name}')" in BICEP, f"{name} is never loaded"
    for placeholder in set(re.findall(r"__[A-Z_]+__", ALL_POLICY_TEXT)):
        assert f"'{placeholder}'" in BICEP, f"{placeholder} is never replaced"


def test_named_values_the_policies_use_are_defined():
    defined = set(re.findall(r"namedValues@\S+ = \{\s*parent: apim\s*name: '([a-z-]+)'", BICEP))
    used = set(re.findall(r"\{\{([a-z0-9-]+)\}\}", ALL_POLICY_TEXT))
    assert used and used <= defined, f"undefined named values: {used - defined}"
    secret = set(re.findall(r"name: '([a-z-]+)'\s*properties: \{\s*displayName: '[a-z-]+'\s*secret: true", BICEP))
    assert secret == {"hub-api-key", "gateway-key"}, "only the two keys are secret named values, and both are"


def test_backends_the_policies_use_are_defined():
    defined = set(re.findall(r"backends@\S+ = \{\s*parent: apim\s*name: '([a-z-]+)'", BICEP))
    used = set(re.findall(r'backend-id="([a-z-]+)"', ALL_POLICY_TEXT))
    assert used == defined == {"chat-backend", "hub-backend", "databricks-backend"}


def test_no_policy_can_buffer_a_response_or_log_a_body():
    for name, text in POLICIES.items():
        code = re.sub(r"<!--.*?-->", "", text, flags=re.S)  # the comments explain the rule, so they mention what is banned
        for banned in ("context.Response.Body", "log-to-eventhub", "validate-content", "<cache-", "<mock-response"):
            assert banned not in code, f"{name} uses {banned}: that buffers or caches a response, which breaks streaming"
    assert BICEP.count("body: noBody") == 4 and "bytes: 0" in BICEP, "diagnostics must log no request or response body"
    assert BICEP.count("properties: diagnosticSettings") == 3, "every API gets the no-body diagnostics"
    assert "preserveContent: true" in POLICIES["tools-api.xml"], "the request body is read without consuming it"


def test_the_streaming_apis_forward_without_buffering():
    for name in ("front-api.xml", "tools-api.xml"):
        assert 'buffer-response="false"' in POLICIES[name]
    assert 'timeout="120"' in POLICIES["tools-api.xml"] and 'timeout="__TIMEOUT__"' in POLICIES["front-api.xml"]


def test_the_front_policy_strips_the_identity_headers_before_it_sets_them():
    text = POLICIES["front-api.xml"]
    for header in ("x-user-id", "x-user-name", "x-gateway-key"):
        deleted, set_ = (text.index(f'<set-header name="{header}" exists-action="delete"'), text.index(f'<set-header name="{header}" exists-action="override"'))
        assert deleted < set_, f"{header} must be deleted before it is set"
    assert "validate-jwt" in text and "<issuer>" in text and 'claim name="tid"' in text, "the token's issuer and tenant are checked, not only its signature"
    assert "{{gateway-key}}" in text and "{{jwt-enabled}}" in text


def test_the_tool_gateway_exposes_only_the_four_mcp_paths_and_reads_the_key_from_x_api_key():
    tools = section("// ================================================================ TOOL gateway", "// ================================================================ DATA")
    assert re.search(r"toolEndpoints = \[ 'genie', 'agents', 'context', 'studio' \]", tools)
    assert re.search(r"\[ 'POST', 'GET', 'DELETE' \]", tools) and "url: '/${endpoint}/mcp'" in tools
    assert re.search(r"subscriptionKeyParameterNames: \{\s*header: 'x-api-key'", tools)
    assert 'name="x-api-key" exists-action="override"' in POLICIES["tools-api.xml"] and "{{hub-api-key}}" in POLICIES["tools-api.xml"]


def test_the_data_gateway_allows_exactly_the_calls_the_hubs_backend_makes(monkeypatch):
    """The allow-list in the Bicep is compared with the requests hub/backends/databricks_genie.py really sends (a mocked transport records them)."""
    from test_databricks_apim import GATEWAY, SPACE, ask, fake_genie, load_backend

    data = section("// ================================================================ DATA", "// ================================================================ products")
    operations = re.findall(r"method: '(\w+)'\s*urlTemplate: '([^']+)'", data)
    assert len(operations) == 3
    patterns = [(m, re.compile("^" + re.sub(r"\{[a-z_]+\}", "[^/]+", t) + "$")) for m, t in operations]

    backend = load_backend(monkeypatch, DATABRICKS_AUTH="apim", DATABRICKS_HOST=GATEWAY, GENIE_SPACE_ID=SPACE, APIM_SUBSCRIPTION_KEY="k")
    seen = fake_genie(backend, monkeypatch)
    ask(backend)
    sent = {(r.method, r.url.path.removeprefix("/dbx")) for r in seen}
    assert len(sent) == 3
    for method, path in sent:
        assert any(m == method and p.match(path) for m, p in patterns), f"{method} {path} is not in the gateway's allow-list"
    for method, pattern in patterns:
        assert any(m == method and pattern.match(path) for m, path in sent), f"the gateway allows {pattern.pattern}, which the backend never calls"


def test_the_front_operations_cover_the_chat_api_routes():
    """Every API route of the chat app has an operation at the gateway (a route without one would be unreachable behind it)."""
    from fastapi.routing import APIRoute

    from ui.backend import main

    front = section("// ================================================================ FRONT gateway", "// ================================================================ TOOL")
    operations = {(m, re.sub(r"\{[a-z_]+\}", "{}", u)) for m, u in re.findall(r"method: '(\w+)', url: '([^']+)'", front)}
    operations |= {("POST", "/api/chat")}
    routes = {(m, re.sub(r"\{[a-z_]+\}", "{}", r.path)) for r in main.app.routes if isinstance(r, APIRoute) for m in r.methods}
    assert routes <= operations | {("GET", "/api/stream-check")}, f"routes without a gateway operation: {routes - operations}"
    assert operations - {("GET", "/api/stream-check")} <= routes, f"gateway operations without a route: {operations - routes}"
