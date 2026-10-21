"""Foundry memory store for user preferences. The CX agent searches it at the start of a turn and Foundry extracts new memories from the
conversation after a short idle delay. Memories are scoped per user, so one store serves many users."""
from __future__ import annotations

import os

from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects.models import MemoryStoreDefaultDefinition, MemoryStoreDefaultOptions

NAME_PREFIX = os.getenv("NAME_PREFIX", "croma")
MEMORY_STORE = os.getenv("MEMORY_STORE", f"{NAME_PREFIX}-user-memory")
MEMORY_SCOPE = os.getenv("MEMORY_SCOPE", f"{NAME_PREFIX}-user-1")  # the default user; one scope per user when several people share the store
# The scope the CX agent's memory search tool is deployed with. "{{$userId}}" makes Foundry take the scope from the x-memory-user-id header on each request, which the
# chat API sets to <MEMORY_SCOPE>-<user id> behind the API gateway: one memory per signed-in user (docs/APIM.md). The default is the one shared scope above.
MEMORY_TOOL_SCOPE = os.getenv("MEMORY_TOOL_SCOPE", MEMORY_SCOPE)
CHAT_MODEL = os.getenv("AGENT_MODEL", "gpt-5.4-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")

PROFILE_DETAILS = (
    "Analysis preferences that a Croma pricing and merchandising user explicitly states about themselves, for example 'I always...', "
    "'I prefer...', 'I mostly look at...', 'keep it short': the regions or pricing zones, product groups, categories and brands they focus on; "
    "the measure they prefer (net margin value or margin %); period and comparison conventions; output preferences (short answer or full report, "
    "level of detail, rupee units such as lakh or crore); recurring analyses; business definitions or rules they ask to be applied. "
    "Save only what the user said in their own messages. Never infer a preference from what the assistant did, offered or assumed, from the "
    "questions asked, or from tone. Do not store one-off questions, requests or data values."
)


def ensure_memory_store(client) -> str:
    """Creates the memory store if it does not exist. Safe to call repeatedly."""
    try:
        client.beta.memory_stores.get(MEMORY_STORE)
    except ResourceNotFoundError:
        client.beta.memory_stores.create(
            name=MEMORY_STORE,
            description="Croma assistant: user analysis preferences (never analysis results)",
            definition=MemoryStoreDefaultDefinition(
                chat_model=CHAT_MODEL, embedding_model=EMBEDDING_MODEL,
                # Preferences only. Conversation summaries would carry old analysis results into new questions, and the assistant would replay them.
                options=MemoryStoreDefaultOptions(user_profile_enabled=True, user_profile_details=PROFILE_DETAILS, chat_summary_enabled=False),
            ),
        )
    return MEMORY_STORE


def recreate_memory_store(client) -> str:
    """Deletes the store and every memory in it, then creates it again. Extraction instructions cannot be edited in place."""
    try:
        client.beta.memory_stores.delete(MEMORY_STORE)
    except ResourceNotFoundError:
        pass
    return ensure_memory_store(client)


if __name__ == "__main__":
    import sys

    from agents.deploy_agents import project

    if "--recreate" in sys.argv:
        print("Recreated:", recreate_memory_store(project()), "(all memories deleted)")
    else:
        print("Memory store:", ensure_memory_store(project()))
