"""SharePoint through Microsoft Graph: searches and reads files in one document library of one site.

Access is read only. In the Croma tenant the hub's managed identity is given the Microsoft Graph application role Sites.Selected, and then read access to this one
site (docs/SHAREPOINT.md has the commands), so the connector can see nothing else. For a quick test, SHAREPOINT_AUTH=token with SHAREPOINT_TOKEN uses a Graph access
token instead (for example one from `az account get-access-token --resource https://graph.microsoft.com`), and a developer signed in with `az login` is covered by
the same default credential chain as the other connectors."""
from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import quote, urlparse

import httpx

from settings import (SHAREPOINT_AUTH, SHAREPOINT_LIBRARY, SHAREPOINT_MAX_FILE_MB, SHAREPOINT_SITE_URL, SHAREPOINT_TIMEOUT_S, SHAREPOINT_TOKEN)
from sharepoint import parse
from sharepoint.base import SharePointError

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"
SELECT = "id,name,webUrl,size,lastModifiedDateTime,file,folder,parentReference"


class GraphBackend:
    label = "graph"

    def __init__(self, site_url: str = "", library: str = "", client: httpx.AsyncClient | None = None):
        self.site_url, self.library = site_url or SHAREPOINT_SITE_URL, library or SHAREPOINT_LIBRARY
        if not self.site_url:
            raise SharePointError("SHAREPOINT_SITE_URL is not set: give the SharePoint site to read (https://<tenant>.sharepoint.com/sites/<site>)")
        self._client = client
        self._token: tuple[str, float] | None = None
        self._site: dict | None = None
        self._drive: str | None = None
        self._lock = asyncio.Lock()

    # -- plumbing
    async def _bearer(self) -> str:
        if SHAREPOINT_AUTH == "token":
            if not SHAREPOINT_TOKEN:
                raise SharePointError("SHAREPOINT_AUTH is token but SHAREPOINT_TOKEN is empty")
            return SHAREPOINT_TOKEN
        if self._token and self._token[1] - time.time() > 120:
            return self._token[0]
        from azure.identity import DefaultAzureCredential

        def get() -> tuple[str, float]:
            tok = DefaultAzureCredential().get_token(SCOPE)
            return tok.token, float(tok.expires_on)

        self._token = await asyncio.to_thread(get)
        return self._token[0]

    async def _get(self, url: str, params: dict | None = None, raw: bool = False):
        client = self._client or httpx.AsyncClient(timeout=SHAREPOINT_TIMEOUT_S, follow_redirects=True)
        try:
            for attempt in range(3):
                resp = await client.get(url if url.startswith("http") else f"{GRAPH}{url}", params=params, headers={"Authorization": f"Bearer {await self._bearer()}"})
                if resp.status_code in (429, 503) and attempt < 2:
                    await asyncio.sleep(min(float(resp.headers.get("Retry-After", "2")), 10))
                    continue
                break
        except httpx.HTTPError as exc:
            raise SharePointError(f"SharePoint could not be reached ({type(exc).__name__})")
        finally:
            if self._client is None:
                await client.aclose()
        if resp.status_code in (401, 403):
            raise SharePointError("SharePoint refused access: the hub's identity has no read access to this site (see docs/SHAREPOINT.md, Sites.Selected)")
        if resp.status_code == 404:
            raise SharePointError("not found in SharePoint (the site, library or file does not exist, or is not visible to the hub)")
        if resp.status_code >= 400:
            raise SharePointError(f"SharePoint answered HTTP {resp.status_code}")
        return resp.content if raw else resp.json()

    async def _drive_id(self) -> str:
        async with self._lock:
            if self._drive:
                return self._drive
            u = urlparse(self.site_url)
            if not u.hostname or not u.path.strip("/"):
                raise SharePointError("SHAREPOINT_SITE_URL must look like https://<tenant>.sharepoint.com/sites/<site>")
            self._site = await self._get(f"/sites/{u.hostname}:{u.path.rstrip('/')}")
            site_id = self._site["id"]
            if self.library:
                drives = (await self._get(f"/sites/{site_id}/drives")).get("value", [])
                match = [d for d in drives if d.get("name", "").lower() == self.library.lower()]
                if not match:
                    raise SharePointError(f"the site has no document library called '{self.library}' (it has: {', '.join(d.get('name', '') for d in drives)})")
                self._drive = match[0]["id"]
            else:
                self._drive = (await self._get(f"/sites/{site_id}/drive"))["id"]
            return self._drive

    def _entry(self, item: dict) -> dict | None:
        kind = parse.kind_of(item.get("name", ""))
        if "file" not in item or not kind:
            return None
        parent = (item.get("parentReference") or {}).get("path", "")
        return {"id": item["id"], "name": item["name"], "path": re.sub(r"^/drives?/[^/]+/root:?", "", parent).strip("/") + ("/" if parent else "") + item["name"],
                "site": (self._site or {}).get("displayName", ""), "modified": (item.get("lastModifiedDateTime") or "")[:10], "size": int(item.get("size") or 0),
                "url": item.get("webUrl", ""), "kind": kind}

    # -- the contract
    async def search(self, query: str, top: int = 10) -> list[dict]:
        drive = await self._drive_id()
        q = query.strip()
        if q:
            data = await self._get(f"/drives/{drive}/root/search(q='{quote(q.replace(chr(39), chr(39) * 2), safe='')}')", {"$top": 40, "$select": SELECT})
            items = data.get("value", [])
        else:
            items, queue = [], [f"/drives/{drive}/root/children"]
            for _ in range(2):  # the library's top level and one level of folders below it
                nxt = []
                for path in queue:
                    for it in (await self._get(path, {"$top": 100, "$select": SELECT})).get("value", []):
                        (nxt.append(f"/drives/{drive}/items/{it['id']}/children") if "folder" in it else items.append(it))
                queue = nxt[:10]
        entries = [e for e in (self._entry(i) for i in items) if e]
        return entries[:top]

    async def fetch(self, file_id: str) -> tuple[dict, bytes]:
        drive = await self._drive_id()
        if not re.fullmatch(r"[A-Za-z0-9!_-]{8,}", file_id):
            raise SharePointError("that is not a file id: use an id returned by search_sharepoint")
        item = await self._get(f"/drives/{drive}/items/{file_id}", {"$select": SELECT})
        entry = self._entry(item)
        if entry is None:
            raise SharePointError(f"'{item.get('name', file_id)}' is not a file this connector reads (spreadsheets, CSV, Word, PDF, PowerPoint and text files)")
        if entry["size"] > SHAREPOINT_MAX_FILE_MB * 1024 * 1024:
            raise SharePointError(f"'{entry['name']}' is {entry['size'] // (1024 * 1024)} MB, over the {SHAREPOINT_MAX_FILE_MB} MB limit")
        return entry, await self._get(f"/drives/{drive}/items/{file_id}/content", raw=True)
