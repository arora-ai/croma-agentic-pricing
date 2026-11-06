"""The contract every SharePoint backend meets, and the settings-driven choice of one.

The SharePoint agent calls two tools on the hub (search_sharepoint, read_sharepoint_file); the hub hands them to a backend. A backend finds files and
fetches their bytes; it never parses them (hub/sharepoint/parse.py does that), so any backend gets the same table and passage handling.

    class Backend:
        label: str                                                       "graph", "folder", ...
        async def search(self, query: str, top: int = 10) -> list[dict]  files that match, most relevant first; an empty query lists the library
        async def fetch(self, file_id: str) -> tuple[dict, bytes]        the file's entry and its content

A file entry is {"id", "name", "path", "site", "modified" (ISO date), "size" (bytes), "url", "kind" ("table" | "document")}. The id is whatever the backend needs
to fetch the file again; the agent passes it back unchanged. Raise SharePointError (with a sentence the agent can act on) for anything that goes wrong.
"""
from __future__ import annotations

from typing import Protocol


class SharePointError(RuntimeError):
    """A problem the SharePoint agent should be told about in plain words (file not found, no access, too large)."""


class Backend(Protocol):
    label: str

    async def search(self, query: str, top: int = 10) -> list[dict]: ...

    async def fetch(self, file_id: str) -> tuple[dict, bytes]: ...
