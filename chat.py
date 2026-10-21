"""Talk to the Croma agents on Foundry. You always talk to the CX agent; it routes to the others.

    python chat.py                      interactive chat (type 'exit' to quit; '/trace' shows agent-to-agent calls)
    python chat.py "your question"      one message, then exit
    python chat.py --agent croma-data "weekly net sales by zone for last week"     talk straight to one agent (for testing)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from agents.deploy_agents import load_env, project

sys.stdout.reconfigure(encoding="utf-8")


def hub_get(env: dict, path: str):
    req = urllib.request.Request(env["HUB_URL"] + path, headers={"x-api-key": env["HUB_API_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def show_trace(env: dict, since: int = 0) -> int:
    rows = hub_get(env, "/trace")
    for t in rows[since:]:
        mark = "ok " if t.get("ok") else "ERR"
        print(f"   [{t['at']}] {mark} -> {t['agent']:9s} {t['seconds']:>5}s  {t['ask'][:110]!r}")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("message", nargs="?")
    parser.add_argument("--agent", default="croma-cx")
    args = parser.parse_args()
    env = load_env()
    client = project().get_openai_client()
    conversation = client.conversations.create()
    seen = len(hub_get(env, "/trace"))

    def turn(text: str) -> None:
        nonlocal seen
        started = time.time()
        stream = client.responses.create(conversation=conversation.id, input=text, stream=True, background=True,
                                         extra_body={"agent_reference": {"name": args.agent, "type": "agent_reference"}})
        parts: list[str] = []
        state = {"id": None, "seq": -1, "done": False}
        reconnects = 0
        while not state["done"]:
            try:
                for event in stream:
                    kind = getattr(event, "type", "")
                    if getattr(event, "sequence_number", None) is not None:
                        state["seq"] = max(state["seq"], event.sequence_number)
                    if kind == "response.created":
                        state["id"] = event.response.id
                    elif kind == "response.output_text.delta":
                        parts.append(event.delta)
                    elif kind == "response.completed":
                        state["done"] = True
            except Exception:
                if not state["id"] or reconnects >= 40:
                    raise
            if not state["done"]:  # the stream dropped (they are cut at about ten minutes): resume the same response
                reconnects += 1
                time.sleep(3)
                stream = client.responses.retrieve(state["id"], stream=True, starting_after=state["seq"])
        print(f"\n{''.join(parts)}\n")
        print(f"({time.time() - started:.0f}s) agent calls made behind the scenes:")
        seen = show_trace(env, seen)
        print()

    if args.message:
        turn(args.message)
        return
    print(f"Chatting with {args.agent}. Type 'exit' to quit.\n")
    while True:
        text = input("you> ").strip()
        if text.lower() in ("exit", "quit"):
            break
        if text:
            turn(text)


if __name__ == "__main__":
    main()
