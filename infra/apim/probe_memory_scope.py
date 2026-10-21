"""Proves per-user memory at run time: the `x-memory-user-id` request header that the chat API sends (see ui/backend/main.py) selects the Foundry memory scope,
when the agent's memory tool is deployed with scope "{{$userId}}". It makes a throwaway agent with that setting, saves a different fact for two users in
two scopes, asks the same question once per user and once with no header, shows what the agent answered, and removes everything it made.

    python infra/apim/probe_memory_scope.py

Reads FOUNDRY_PROJECT_ENDPOINT from .env. Signs in with az login. Uses the memory store <NAME_PREFIX>-user-memory but only scopes named ...-probe-a and ...-probe-b,
which it deletes again. Creates and deletes the agent <NAME_PREFIX>-memprobe. Prints no secret."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def read_env(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))


def main() -> int:
    os.environ.setdefault("FOUNDRY_PROJECT_ENDPOINT", read_env(ROOT / ".env")["FOUNDRY_PROJECT_ENDPOINT"])
    os.environ.setdefault("NAME_PREFIX", "croma2")
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import MemorySearchOptions, MemorySearchPreviewTool, PromptAgentDefinition
    from azure.identity import DefaultAzureCredential

    from agents.memory_store import MEMORY_SCOPE, MEMORY_STORE

    client = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=DefaultAzureCredential(exclude_interactive_browser_credential=True, process_timeout=120))
    openai = client.get_openai_client()
    name = f"{os.environ['NAME_PREFIX']}-memprobe"
    scopes = {"A": f"{MEMORY_SCOPE}-probe-a", "B": f"{MEMORY_SCOPE}-probe-b"}
    facts = {"A": "The user's favourite colour is teal.", "B": "The user's favourite colour is orange."}
    question = "What is my favourite colour? Use only what you remember about me. Answer in one word, or say unknown."
    ok = True
    try:
        for who, scope in scopes.items():
            client.beta.memory_stores.create_memory(MEMORY_STORE, scope=scope, content=facts[who], kind="user_profile")
        client.agents.create_version(agent_name=name, description="Memory-scope probe (safe to delete)", definition=PromptAgentDefinition(
            model=os.getenv("AGENT_MODEL", "gpt-5.4-mini"),
            instructions="You have a memory search tool. Answer from what it returns about the user; if it returns nothing relevant, answer: unknown.",
            tools=[MemorySearchPreviewTool(memory_store_name=MEMORY_STORE, scope="{{$userId}}", update_delay=0, search_options=MemorySearchOptions(max_memories=8))]))
        time.sleep(5)  # a new memory can take a moment to be searchable
        for label, header in (("user A (x-memory-user-id: %s)" % scopes["A"], scopes["A"]), ("user B (x-memory-user-id: %s)" % scopes["B"], scopes["B"]), ("no header", None)):
            conversation = openai.conversations.create()
            kwargs = {"extra_headers": {"x-memory-user-id": header}} if header else {}
            answer = openai.responses.create(conversation=conversation.id, input=question, extra_body={"agent_reference": {"name": name, "type": "agent_reference"}}, **kwargs).output_text.strip()
            print(f"{label}: {answer}")
            expected = {"A": "teal", "B": "orange"}.get(label[5] if label.startswith("user") else "", None)
            ok = ok and (expected in answer.lower() if expected else all(c not in answer.lower() for c in ("teal", "orange")))
            openai.conversations.delete(conversation.id)
    finally:
        for scope in scopes.values():
            try:
                client.beta.memory_stores.delete_scope(MEMORY_STORE, scope=scope)
            except Exception as exc:
                print(f"(could not delete scope {scope}: {type(exc).__name__})")
        try:
            client.agents.delete(agent_name=name)
        except Exception:
            pass
    print("RESULT:", "each user's header selected that user's memory scope" if ok else "the header did not select the scope as expected")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
