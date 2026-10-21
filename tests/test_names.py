"""Agent, connection and memory-store names follow NAME_PREFIX, and the hub (its own container) and the agent deployment build the same names."""
import importlib
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hub"))


@contextmanager
def prefix(monkeypatch, value):
    """Loads the modules that derive names with NAME_PREFIX set (or unset), and puts them back to the default afterwards."""
    def load():
        import settings
        import agents.definitions as definitions
        import agents.memory_store as memory_store
        for module in (settings, memory_store, definitions):
            importlib.reload(module)
        return settings, memory_store, definitions

    if value is None:
        monkeypatch.delenv("NAME_PREFIX", raising=False)
    else:
        monkeypatch.setenv("NAME_PREFIX", value)
    try:
        yield load()
    finally:
        monkeypatch.undo()
        load()


def test_default_prefix_keeps_the_v1_names(monkeypatch):
    with prefix(monkeypatch, None) as (settings, memory_store, definitions):
        assert settings.AGENT_NAMES["cx"] == "croma-cx" and settings.AGENT_NAMES["planner"] == "croma-report-planner"
        assert definitions.NAMES == settings.AGENT_NAMES
        assert definitions.CONNECTIONS == {"genie": "croma-hub-genie", "agents": "croma-hub-agents", "context": "croma-hub-context", "studio": "croma-hub-studio"}
        assert (memory_store.MEMORY_STORE, memory_store.MEMORY_SCOPE) == ("croma-user-memory", "croma-user-1")


def test_prefix_renames_everything_consistently(monkeypatch):
    with prefix(monkeypatch, "croma2") as (settings, memory_store, definitions):
        assert all(name.startswith("croma2-") for name in settings.AGENT_NAMES.values())
        assert definitions.NAMES == settings.AGENT_NAMES
        assert set(definitions.CONNECTIONS.values()) == {"croma2-hub-genie", "croma2-hub-agents", "croma2-hub-context", "croma2-hub-studio"}
        assert memory_store.MEMORY_STORE == "croma2-user-memory"
        assert len(set(settings.AGENT_NAMES.values())) == len(settings.AGENT_NAMES)
