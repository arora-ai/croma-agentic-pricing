"""Runs one Foundry agent to completion. The single place where the hub calls an agent: the agent bridge (for tool calls from other agents) and the
harness workers both go through here."""
from __future__ import annotations

import time
from collections import deque

from azure_clients import project_openai
from settings import AGENT_NAMES

# Recent calls, so a test run can show which agents talked to which (no fixed order, so this is the evidence).
trace: deque[dict] = deque(maxlen=500)


def invoke(agent: str, text: str) -> tuple[str, dict]:
    """Runs the agent as a Foundry background response and polls it: a long run (a report takes many minutes) as one open HTTP request gets cut
    by idle-connection limits, whereas short polls do not. A run that fails on the model's token rate limit is retried after a pause."""
    client = project_openai()
    ref = {"agent_reference": {"name": AGENT_NAMES[agent], "type": "agent_reference"}}
    for attempt in range(1, 7):
        resp = client.responses.create(input=text, background=True, extra_body=ref)
        failures = 0
        while resp.status in ("queued", "in_progress"):
            time.sleep(3)
            try:
                resp = client.responses.retrieve(resp.id)
                failures = 0
            except Exception:
                failures += 1  # a dropped poll is not a failed run; try again a few times
                if failures > 5:
                    raise
        if resp.status == "completed":
            usage = getattr(resp, "usage", None)
            return resp.output_text, {"input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None)}
        detail = getattr(getattr(resp, "error", None), "message", None) or str(resp.status)
        if "rate limit" in detail.lower() and attempt < 6:
            time.sleep(65)  # the token quota renews every 60 seconds, so waiting a full window is what actually helps
            continue
        raise RuntimeError(f"the {agent} agent run did not complete ({detail})")
    raise RuntimeError(f"the {agent} agent run did not complete")
