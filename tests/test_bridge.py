"""What the agent bridge tells a calling agent when the agent it called fails."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

import pytest  # noqa: E402

import agent_bridge  # noqa: E402


def _call_failing(monkeypatch, message: str) -> str:
    def fail(agent, text):
        raise RuntimeError(message)

    monkeypatch.setattr(agent_bridge, "_invoke", fail)
    return asyncio.run(agent_bridge._call("insight", "rank the drivers", "C-bridge"))


def test_rate_limit_is_reported_as_transient(monkeypatch):
    answer = _call_failing(monkeypatch, "the insight agent run did not complete (Your requests to gpt-6-sol for gpt-6-sol in eastus2 have exceeded token rate limit.)")
    assert answer.startswith("ERROR")
    assert "out of model capacity" in answer and "again" in answer
    assert "smaller question" not in answer


def test_other_failures_keep_the_generic_advice(monkeypatch):
    answer = _call_failing(monkeypatch, "the insight agent run did not complete (the model returned an invalid tool call)")
    assert answer.startswith("ERROR") and "could not answer (RuntimeError)" in answer and "smaller question" in answer


@pytest.mark.parametrize("message", ["token rate limit", "Rate Limit exceeded"])
def test_rate_limit_match_ignores_case(monkeypatch, message):
    assert "out of model capacity" in _call_failing(monkeypatch, message)


def test_failed_call_is_traced_with_its_reason(monkeypatch):
    agent_bridge.trace.clear()
    _call_failing(monkeypatch, "the insight agent run did not complete (Your requests have exceeded token rate limit.)")
    last = agent_bridge.trace[-1]
    assert last["ok"] is False and "rate limit" in last["error"]
