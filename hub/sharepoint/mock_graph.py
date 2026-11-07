"""A Microsoft Graph simulator for SharePoint, so the Graph connector can be run, shown and tested without a tenant.

It answers the Graph calls the connector makes, in Graph's shapes: a site found by host and path, the site's document libraries, a library's folders and files (driveItem
ids, webUrl, created and modified by, eTag, parentReference), search over file names and contents, an item's metadata, and its content as a 302 redirect to a short-lived
pre-authenticated download URL. It needs a bearer token, and it answers in Graph's error format (401 InvalidAuthenticationToken, 404 itemNotFound). The files are the
synthetic sample library that ships with the hub, shown as a fictional site on the reserved contoso.sharepoint.com domain.

The hub mounts it at /graph-sim when SHAREPOINT_BACKEND=simulated, and the ordinary GraphBackend reads it: only the base URL and the token differ from a real tenant.
`python hub/sharepoint/mock_graph.py` serves it alone, for trying with curl."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from sharepoint import SAMPLE_LIBRARY, parse
from sharepoint.folder import FolderBackend

log = logging.getLogger("sharepoint.simulator")

MOUNT = "/graph-sim"
HOST = os.getenv("SHAREPOINT_SIM_HOST", "contoso.sharepoint.com")
SITE_PATH = "/sites/merchandising-planning"
SITE_URL = f"https://{HOST}{SITE_PATH}"
SITE_NAME = "Merchandising and Planning"
DEFAULT_LIBRARY = "Documents"
LIBRARIES = (DEFAULT_LIBRARY, "Archive")  # the second one is empty: it lets library selection be shown
# Whoever reads the site (a client or a person) needs this token. Without SHAREPOINT_SIM_TOKEN it is random for each start, so only this process can use it.
TOKEN = os.getenv("SHAREPOINT_SIM_TOKEN") or secrets.token_urlsafe(32)
_SECRET = secrets.token_bytes(32)
DOWNLOAD_SECONDS = 300
MIME = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12", ".csv": "text/csv",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
PEOPLE = {"Planning": ("Meera Iyer", "meera.iyer"), "Pricing": ("Karan Malhotra", "karan.malhotra"), "Policies": ("Ananya Rao", "ananya.rao"),
          "Notes": ("Rohan Desai", "rohan.desai")}
OWNER = ("Site Owner", "site.owner")


def local_url() -> str:
    """Where the hub reaches its own simulator (loopback), unless SHAREPOINT_GRAPH_URL says otherwise."""
    return f"http://127.0.0.1:{os.getenv('PORT', '8080')}{MOUNT}/v1.0"


def _digest(*parts: str) -> bytes:
    return hashlib.sha1("|".join(parts).encode()).digest()


def _guid(*parts: str) -> str:
    return str(uuid.UUID(bytes=_digest(*parts)[:16]))


def _item_id(path: str) -> str:
    return "01" + base64.b32encode(_digest("item", path)).decode().rstrip("=")[:32]  # 34 upper case characters, as a driveItem id looks


def _drive_id(name: str) -> str:
    return "b!" + base64.urlsafe_b64encode(_digest("drive", name) + _digest("drive2", name)[:12]).decode().rstrip("=")


SITE_ID = f"{HOST},{_guid('site', HOST, SITE_PATH)},{_guid('web', HOST, SITE_PATH)}"
DRIVES = {_drive_id(n): n for n in LIBRARIES}
DEFAULT_DRIVE = _drive_id(DEFAULT_LIBRARY)


def _time(day: str, path: str, back_days: int = 0) -> str:
    """A modified time on the given day (from the library's manifest), at a time of day that is stable for the file."""
    import datetime as dt

    d = _digest("time", path)
    stamp = dt.datetime.fromisoformat(day) - dt.timedelta(days=back_days)
    return stamp.replace(hour=8 + d[0] % 9, minute=d[1] % 60, second=d[2] % 60).strftime("%Y-%m-%dT%H:%M:%SZ")


class Library:
    """The sample folder seen as a library: folders and files with Graph-style ids and times."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.finder = FolderBackend(self.root)
        self.items: dict[str, dict] = {}
        self.children: dict[str, list[str]] = {}
        root_id = _item_id("")
        self.items[root_id] = {"id": root_id, "name": "root", "rel": "", "folder": True}
        self.children[root_id] = []
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if path.name.startswith(("~$", ".")) or any(part.startswith(".") for part in rel.split("/")):
                continue
            if path.is_file() and not parse.kind_of(path.name):
                continue
            iid = _item_id(rel)
            if path.is_dir():
                self.items[iid] = {"id": iid, "name": path.name, "rel": rel, "folder": True}
                self.children[iid] = []
            else:
                self.items[iid] = {"id": iid, "name": path.name, "rel": rel, "folder": False, "size": path.stat().st_size}
            self.children[_item_id(rel.rsplit("/", 1)[0] if "/" in rel else "")].append(iid)
        self.by_rel = {i["rel"]: i["id"] for i in self.items.values()}
        for item in self.items.values():  # a folder was last changed when its newest file was
            if not item["folder"]:
                item["day"] = self.finder._modified.get(item["rel"]) or "2026-01-01"
        for iid in reversed(list(self.items)):
            item = self.items[iid]
            if item["folder"]:
                days = [self.items[c].get("day", "") for c in self.children[iid]]
                item["day"] = max([d for d in days if d] or ["2026-01-01"])

    def count(self, iid: str) -> int:
        return len(self.children.get(iid, []))


@lru_cache(maxsize=1)
def library() -> Library:
    return Library(SAMPLE_LIBRARY)


# ---------------------------------------------------------------- the Graph shapes

def _person(rel: str) -> dict:
    top = rel.split("/", 1)[0]
    name, mail = PEOPLE.get(top, OWNER)
    return {"user": {"displayName": name, "email": f"{mail}@{HOST.split('.')[0]}.com"}}


def _web(rel: str, lib: str = DEFAULT_LIBRARY) -> str:
    base = f"{SITE_URL}/{'Shared Documents' if lib == DEFAULT_LIBRARY else lib}"
    return quote(base + ("/" + rel if rel else ""), safe=":/")


def _download_url(request: Request, iid: str) -> str:
    expires = int(time.time()) + DOWNLOAD_SECONDS
    sig = hmac.new(_SECRET, f"{iid}.{expires}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{str(request.base_url).rstrip('/')}{MOUNT}/download/{iid}.{expires}.{sig}"


def _item(request: Request, iid: str, drive: str) -> dict:
    lib = library()
    it = lib.items[iid]
    rel = it["rel"]
    parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
    modified, created = _time(it["day"], rel), _time(it["day"], rel, back_days=12 + _digest("c", rel)[0] % 30)
    out = {
        "id": iid, "name": it["name"], "webUrl": _web(rel), "size": it.get("size", 0) if not it["folder"] else sum(lib.items[c].get("size", 0) for c in lib.children[iid]),
        "createdDateTime": created, "lastModifiedDateTime": modified, "createdBy": _person(rel), "lastModifiedBy": _person(rel),
        "eTag": '"{%s},%d"' % (_guid("etag", rel).upper(), 2 + _digest("v", rel)[0] % 6), "cTag": '"c:{%s},0"' % _guid("ctag", rel).upper(),
        "fileSystemInfo": {"createdDateTime": created, "lastModifiedDateTime": modified},
        "parentReference": {"driveType": "documentLibrary", "driveId": drive, "id": _item_id(parent_rel), "siteId": SITE_ID.split(",")[1],
                            "path": f"/drives/{drive}/root:" + (f"/{parent_rel}" if parent_rel else "")},
    }
    if it["folder"]:
        out["folder"] = {"childCount": lib.count(iid)}
    else:
        ext = Path(it["name"]).suffix.lower()
        out["file"] = {"mimeType": MIME.get(ext, "application/octet-stream"), "hashes": {"quickXorHash": base64.b64encode(_digest("hash", rel)).decode()}}
        out["@microsoft.graph.downloadUrl"] = _download_url(request, iid)
    return out


def _site() -> dict:
    return {"id": SITE_ID, "name": SITE_PATH.rsplit("/", 1)[-1], "displayName": SITE_NAME, "webUrl": SITE_URL, "createdDateTime": "2025-11-03T06:12:40Z",
            "lastModifiedDateTime": "2026-10-13T09:41:07Z", "siteCollection": {"hostname": HOST}}


def _drive(drive: str) -> dict:
    name = DRIVES[drive]
    return {"id": drive, "name": name, "driveType": "documentLibrary", "description": "", "webUrl": _web("", name), "createdDateTime": "2025-11-03T06:12:41Z",
            "owner": {"user": {"displayName": OWNER[0]}}}


def _error(status: int, code: str, message: str) -> JSONResponse:
    rid = str(uuid.uuid4())
    return JSONResponse({"error": {"code": code, "message": message, "innerError": {"request-id": rid, "date": time.strftime("%Y-%m-%dT%H:%M:%S")}}}, status_code=status,
                        headers={"request-id": rid})


def _not_found(what: str) -> JSONResponse:
    return _error(404, "itemNotFound", f"{what} could not be found.")


def _select(obj: dict, fields: set[str]) -> dict:
    return {k: v for k, v in obj.items() if k in fields or k in ("id", "@microsoft.graph.downloadUrl")} if fields else obj


def _page(request: Request, entries: list[dict]) -> dict:
    """A collection, with $top, $select and a @odata.nextLink the way Graph pages it ($skiptoken is just the offset here)."""
    q = request.query_params
    top = max(1, min(int(q.get("$top", "200") or 200), 999))
    start = int(q.get("$skiptoken", "0") or 0)
    fields = {f.strip() for f in q.get("$select", "").split(",") if f.strip()}
    page = [_select(e, fields) for e in entries[start:start + top]]
    out = {"value": page}
    if start + top < len(entries):
        params = {k: v for k, v in q.items() if k != "$skiptoken"}
        out["@odata.nextLink"] = f"{request.url.scheme}://{request.url.netloc}{request.url.path}?" + "&".join(f"{quote(k, safe='$')}={quote(v, safe='')}" for k, v in
                                                                                                         {**params, "$skiptoken": str(start + top)}.items())
    return out


# ---------------------------------------------------------------- the routes

def _authorised(request: Request) -> bool:
    got = request.headers.get("authorization", "")
    return got.lower().startswith("bearer ") and hmac.compare_digest(got[7:].strip(), TOKEN)


async def graph(request: Request) -> Response:
    if not _authorised(request):
        return _error(401, "InvalidAuthenticationToken", "Access token validation failure. Invalid audience.")
    path = "/" + request.path_params["rest"].strip("/")
    log.info("simulated Graph GET %s", path)
    lib = library()

    if m := re.fullmatch(r"/sites/([^/:]+):(/.+)", path):  # /sites/{host}:/sites/{site}
        host, site_path = m.group(1), m.group(2).rstrip("/")
        if host.lower() != HOST.lower() or site_path.lower() != SITE_PATH:
            return _not_found("The requested site")
        return JSONResponse(_site())
    if m := re.fullmatch(r"/sites/([^/]+)/(drives|drive)", path):
        if m.group(1) != SITE_ID and m.group(1) != SITE_ID.split(",")[1]:
            return _not_found("The requested site")
        if m.group(2) == "drives":
            return JSONResponse(_page(request, [_drive(d) for d in DRIVES]))
        return JSONResponse(_drive(DEFAULT_DRIVE))

    m = re.fullmatch(r"/drives/([^/]+)(/.*)?", path)
    if not m:
        return _error(400, "invalidRequest", f"This simulator does not implement {path}.")
    drive, rest = m.group(1), m.group(2) or ""
    if drive not in DRIVES:
        return _not_found("The requested drive")
    empty = DRIVES[drive] != DEFAULT_LIBRARY
    root = _item_id("")

    if not rest:
        return JSONResponse(_drive(drive))
    if rest in ("/root", f"/items/{root}"):
        return JSONResponse(_item(request, root, drive))
    if rest.startswith("/root:/"):  # an item by its path in the library
        iid = lib.by_rel.get(unquote(rest[len("/root:/"):]).strip("/"))
        return JSONResponse(_item(request, iid, drive)) if iid and not empty else _not_found("The requested item")
    if rest == "/root/children" or (m2 := re.fullmatch(r"/items/([^/]+)/children", rest)):
        iid = root if rest == "/root/children" else m2.group(1)
        if iid not in lib.children:
            return _not_found("The requested item")
        return JSONResponse(_page(request, [] if empty else [_item(request, c, drive) for c in lib.children[iid]]))
    if m2 := re.fullmatch(r"/root/search\(q='(.*)'\)", rest):
        query = m2.group(1).replace("''", "'")
        hits = [] if empty else await lib.finder.search(query, 200)
        return JSONResponse(_page(request, [{**_item(request, lib.by_rel[h["id"]], drive), "searchResult": {}} for h in hits]))
    if m2 := re.fullmatch(r"/items/([^/]+)(/content)?", rest):
        iid = m2.group(1)
        if empty or iid not in lib.items:
            return _not_found("The requested item")
        if not m2.group(2):
            return JSONResponse(_select(_item(request, iid, drive), {f.strip() for f in request.query_params.get("$select", "").split(",") if f.strip()}))
        if lib.items[iid]["folder"]:
            return _error(400, "invalidRequest", "The requested item is a folder, which has no content.")
        return RedirectResponse(_download_url(request, iid), status_code=302)
    return _error(400, "invalidRequest", f"This simulator does not implement {path}.")


async def download(request: Request) -> Response:
    """The pre-authenticated, short-lived URL a /content call redirects to (no bearer token, as in SharePoint)."""
    try:
        iid, expires, sig = request.path_params["ticket"].rsplit(".", 2)
        good = hmac.compare_digest(sig, hmac.new(_SECRET, f"{iid}.{expires}".encode(), hashlib.sha256).hexdigest()[:32]) and int(expires) > time.time()
    except ValueError:
        good = False
    item = library().items.get(iid) if good else None
    if not item or item["folder"]:
        return _error(403, "accessDenied", "The download link is not valid or has expired.")
    data = await asyncio.to_thread((library().root / item["rel"]).read_bytes)
    ext = Path(item["name"]).suffix.lower()
    return Response(data, media_type=MIME.get(ext, "application/octet-stream"), headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(item['name'])}"})


app = Starlette(routes=[Route("/v1.0/{rest:path}", graph), Route("/download/{ticket}", download)])


if __name__ == "__main__":
    import uvicorn
    from starlette.routing import Mount

    port = int(os.getenv("PORT", "8081"))
    print(f"Graph simulator on http://127.0.0.1:{port}{MOUNT}/v1.0   site {SITE_URL}")
    print(f"token (send as 'Authorization: Bearer ...'): {TOKEN}" if not os.getenv("SHAREPOINT_SIM_TOKEN") else "token: the value of SHAREPOINT_SIM_TOKEN")
    uvicorn.run(Starlette(routes=[Mount(MOUNT, app=app)]), host="127.0.0.1", port=port)
