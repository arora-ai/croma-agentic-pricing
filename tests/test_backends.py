"""The data backend layer: the registry, the stub, the custom hook, the gateway tool, and the example backend."""
import asyncio
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))

import pytest  # noqa: E402


def reload_with(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for name in ("settings", "backends", "backends.stub", "backends.custom", "backends.databricks_genie", "genie_gateway"):
        sys.modules.pop(name, None)
    return importlib.import_module("genie_gateway")


def call(gateway, question="How many stores?", cid="T-backend"):
    return json.loads(asyncio.run(gateway.query_space(question, cid)))


def test_stub_is_the_default_and_says_not_configured(monkeypatch):
    monkeypatch.delenv("GENIE_BACKEND", raising=False)
    gateway = reload_with(monkeypatch)
    out = call(gateway)
    assert "No data source is connected" in out["error"] and out["backend"] == "stub"


def test_unknown_backend_is_rejected(monkeypatch):
    gateway = reload_with(monkeypatch, GENIE_BACKEND="nonsense")
    with pytest.raises(RuntimeError, match="Unknown GENIE_BACKEND"):
        call(gateway)


def test_databricks_backend_explains_missing_configuration(monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("GENIE_SPACE_ID", raising=False)
    gateway = reload_with(monkeypatch, GENIE_BACKEND="databricks")
    assert "DATABRICKS_HOST and GENIE_SPACE_ID" in call(gateway)["error"]


def test_custom_backend_result_reaches_the_ledger_and_is_capped(monkeypatch, tmp_path):
    (tmp_path / "my_backend.py").write_text(
        "async def ask(question, conversation_id=''):\n"
        "    rows = [['z%d' % i, float(i)] for i in range(500)]\n"
        "    return {'sql': 'select 1', 'columns': ['zone', 'nmv'], 'rows': rows, 'row_count': len(rows)}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    gateway = reload_with(monkeypatch, GENIE_BACKEND="custom", CUSTOM_BACKEND="my_backend:ask", GENIE_MAX_ROWS="50")
    out = call(gateway, "nmv by zone", "T-custom")
    assert out["backend"] == "custom" and out["row_count"] == 50 and out["truncated_at_max_rows"] is True and out["result_id"] == "R1"
    from studio import ledger
    assert len(ledger.get_table("T-custom", "R1").rows) == 50


def test_custom_backend_failures_become_errors_not_exceptions(monkeypatch, tmp_path):
    (tmp_path / "bad_backend.py").write_text("async def ask(question, conversation_id=''):\n    raise ValueError('boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    gateway = reload_with(monkeypatch, GENIE_BACKEND="custom", CUSTOM_BACKEND="bad_backend:ask")
    assert "boom" in call(gateway)["error"]
    gateway = reload_with(monkeypatch, GENIE_BACKEND="custom", CUSTOM_BACKEND="does_not_exist:ask")
    assert "could not be loaded" in call(gateway)["error"]


def test_the_shipped_example_backend_follows_the_contract(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "integrations" / "genie"))
    gateway = reload_with(monkeypatch, GENIE_BACKEND="custom", CUSTOM_BACKEND="custom_backend_example:ask")
    out = call(gateway, "NMV by channel", "T-example")
    assert out["columns"] == ["channel", "cy_nmv", "ly_nmv"] and out["row_count"] == 2 and out["result_id"]
    assert "error" in call(gateway, "something else", "T-example2")


def test_the_tool_description_comes_from_the_environment(monkeypatch):
    gateway = reload_with(monkeypatch, GENIE_BACKEND="stub", GENIE_TOOL_DESCRIPTION="Retail sales data for testing.")
    tool = asyncio.run(gateway.mcp.list_tools())[0]
    assert tool.name == "query_space_croma_pricing" and tool.description == "Retail sales data for testing."
