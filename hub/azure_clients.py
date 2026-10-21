"""Azure clients shared by the hub. Locally this uses your az login; in Container Apps it uses the app's managed identity. No keys."""
from __future__ import annotations

from functools import lru_cache

from azure.identity import DefaultAzureCredential

from settings import PROJECT_ENDPOINT


@lru_cache(maxsize=1)
def credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


@lru_cache(maxsize=1)
def project_openai():
    """OpenAI-compatible client on the Foundry project, used to invoke Foundry agents by name."""
    from azure.ai.projects import AIProjectClient

    if not PROJECT_ENDPOINT:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is not set. See .env.example.")
    return AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential()).get_openai_client()
