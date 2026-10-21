"""Scripted CX conversations to check how it asks. Each argument is one conversation: turns separated by ' || '.
Usage: python -m tests.cx_dialog "first turn || second turn" "another conversation"
Prints the CX reply for every turn and the specialists it called (so we can see whether it asked or ran)."""
import sys

from agents.definitions import NAMES
from agents.deploy_agents import load_env, project
from chat import hub_get, show_trace

sys.stdout.reconfigure(encoding="utf-8")
env = load_env()
client = project().get_openai_client()
for script in sys.argv[1:]:
    conv = client.conversations.create()
    print("=" * 100)
    for turn in [t.strip() for t in script.split("||")]:
        seen = len(hub_get(env, "/trace"))
        print(f"\nUSER: {turn}\n")
        stream = client.responses.create(conversation=conv.id, input=turn, stream=True, extra_body={"agent_reference": {"name": NAMES["cx"], "type": "agent_reference"}})
        print("CX:", "".join(e.delta for e in stream if getattr(e, "type", "") == "response.output_text.delta"))
        rows = hub_get(env, "/trace")[seen:]
        print("\n  specialists called:", [r["agent"] for r in rows] or "none (CX asked or answered itself)")
