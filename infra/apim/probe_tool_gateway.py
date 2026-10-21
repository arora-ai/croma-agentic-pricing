"""Proves the tool gateway from the Foundry side: a throwaway agent whose only tool is the hub's Genie MCP endpoint reached THROUGH APIM, with an APIM
subscription key held in a Foundry project connection. It asks one data question, shows the tool call Foundry made, and deletes the agent again.

    # 1. connections that point at APIM (not at the hub), under their own names so the live ones are untouched
    .\\infra\\create_hub_connection.ps1 -Apim -NamePrefix croma2-apimprobe
    # 2. the probe
    python infra/apim/probe_tool_gateway.py ["What was the total NMV in March 2026 in Rs crore?"]

Reads FOUNDRY_PROJECT_ENDPOINT from .env and APIM_TOOLS_URL from .apim.env. Signs in with az login. Creates the agent <NAME_PREFIX>-apimprobe and removes it afterwards
(--keep leaves it). Prints no secret."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def read_env(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*")
    parser.add_argument("--keep", action="store_true", help="do not delete the probe agent afterwards")
    parser.add_argument("--server-url", choices=["apim", "hub"], default="apim",
                        help="the server_url written in the agent's tool: the APIM URL (default), or the hub's own URL as the existing agents have it "
                             "(then only the connection, which holds the APIM URL and key, decides where Foundry really calls)")
    args = parser.parse_args()

    env, apim = read_env(ROOT / ".env"), read_env(ROOT / ".apim.env")
    prefix = os.getenv("NAME_PREFIX", "croma2")
    os.environ["FOUNDRY_PROJECT_ENDPOINT"] = os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or env["FOUNDRY_PROJECT_ENDPOINT"]

    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import MCPTool, PromptAgentDefinition
    from azure.identity import DefaultAzureCredential

    name = f"{prefix}-apimprobe"
    connection = f"{prefix}-apimprobe-hub-genie"
    server_url = f"{apim['APIM_TOOLS_URL']}/genie/mcp" if args.server_url == "apim" else f"{env['HUB_URL'].rstrip('/')}/genie/mcp"
    question = " ".join(args.question) or "What was the total NMV in March 2026 in Rs crore?"
    # process_timeout: signing in through the Azure CLI can take longer than the default 10 seconds on a busy machine.
    client = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                             credential=DefaultAzureCredential(exclude_interactive_browser_credential=True, process_timeout=120))
    definition = PromptAgentDefinition(
        model=os.getenv("AGENT_MODEL", "gpt-5.4-mini"),
        instructions="Answer data questions by calling the query_space_croma_pricing tool once, then state the figure it returned. Be brief.",
        tools=[MCPTool(server_label="genie", server_url=server_url, project_connection_id=connection,
                       allowed_tools=["query_space_croma_pricing"], require_approval="never")],
    )
    version = client.agents.create_version(agent_name=name, definition=definition, description="Tool-gateway probe: Foundry to APIM to the hub (safe to delete)")
    print(f"agent {name} version {version.version}; its tool is {server_url} through connection {connection}")
    try:
        openai = client.get_openai_client()
        conversation = openai.conversations.create()
        response = openai.responses.create(conversation=conversation.id, input=question, extra_body={"agent_reference": {"name": name, "type": "agent_reference"}})
        calls = [item for item in response.output if getattr(item, "type", "") == "mcp_call"]
        listed = [item for item in response.output if getattr(item, "type", "") == "mcp_list_tools"]
        for item in listed:
            print(f"Foundry listed tools from {item.server_label}: {[t.name for t in item.tools]}")
        for item in calls:
            result = (getattr(item, "output", None) or "")[:160].replace("\n", " ")
            print(f"Foundry called {item.server_label}.{item.name}({(item.arguments or '')[:100]}) error={getattr(item, 'error', None)}")
            print(f"   result: {result}")
        print(f"answer: {response.output_text.strip()}")
        openai.conversations.delete(conversation.id)
        return 0 if calls and not any(getattr(c, "error", None) for c in calls) else 1
    finally:
        if not args.keep:
            client.agents.delete(agent_name=name)
            print(f"deleted agent {name}")


if __name__ == "__main__":
    sys.exit(main())
