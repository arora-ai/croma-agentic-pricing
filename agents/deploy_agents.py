"""Creates (or updates, as a new version) the Croma agents in the Foundry project.

    python -m agents.deploy_agents            deploy all
    python -m agents.deploy_agents --check    render every prompt from config/data_profile.json and report problems, without calling Azure
    python -m agents.deploy_agents --list     show what is deployed

Reads HUB_URL, HUB_API_KEY and FOUNDRY_PROJECT_ENDPOINT from .env (written by infra/deploy_hub.ps1). Signs in with your az login.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

from agents.data_profile import ProfileError
from agents.definitions import DESCRIPTIONS, NAMES, build
from agents.memory_store import ensure_memory_store

ROOT = Path(__file__).parent.parent


def load_env() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.exists():
        raise SystemExit(".env is missing. Copy .env.example to .env, or run infra/deploy_hub.ps1, which writes it.")
    env = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8-sig").splitlines() if "=" in line and not line.lstrip().startswith("#"))
    return {**env, **{k: v for k, v in os.environ.items() if k in env}}


def project() -> AIProjectClient:
    return AIProjectClient(endpoint=load_env()["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        try:
            defs = build("https://hub.example")
        except ProfileError as exc:
            sys.exit(f"Data profile problem: {exc}")
        print(f"OK: {len(defs)} agents render from config/data_profile.json: {', '.join(defs)}")
        return
    client = project()
    if args.list:
        for a in client.agents.list():
            print(a.name, getattr(getattr(a, "versions", None), "latest", None) and a.versions.latest.version)
        return
    env = load_env()
    try:
        definitions = build(env["HUB_URL"])
    except ProfileError as exc:
        sys.exit(f"Data profile problem: {exc}")
    print(f"Memory store: {ensure_memory_store(client)}")
    for key, definition in definitions.items():
        v = client.agents.create_version(agent_name=NAMES[key], definition=definition, description=DESCRIPTIONS[key])
        print(f"{NAMES[key]:24s} version {v.version}  tools: {[getattr(t, 'server_label', t.type) for t in definition.tools]}")


if __name__ == "__main__":
    main()
