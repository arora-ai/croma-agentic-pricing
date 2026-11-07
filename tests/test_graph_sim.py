"""The Microsoft Graph simulator for SharePoint (hub/sharepoint/mock_graph.py): the ordinary Graph backend must read it exactly as it would read a tenant, and the
simulator must answer in Graph's shapes, with its errors. No network is used: the client talks to the simulator through an ASGI transport."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

import sharepoint  # noqa: E402
import sharepoint_gateway as gateway  # noqa: E402
from sharepoint import mock_graph, parse  # noqa: E402
from sharepoint.base import SharePointError  # noqa: E402
from sharepoint.folder import FolderBackend  # noqa: E402
from sharepoint.graph import GraphBackend  # noqa: E402
from studio import ledger  # noqa: E402

BASE = "http://sim" + mock_graph.MOUNT + "/v1.0"
SAMPLE = Path(__file__).resolve().parents[1] / "hub" / "sharepoint" / "sample_library"
AUTH = {"Authorization": f"Bearer {mock_graph.TOKEN}"}


def client() -> httpx.AsyncClient:
    from starlette.applications import Starlette
    from starlette.routing import Mount

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=[Mount(mock_graph.MOUNT, app=mock_graph.app)])), follow_redirects=True)


def backend(library: str = "") -> GraphBackend:
    return GraphBackend(mock_graph.SITE_URL, library, client(), graph_url=BASE, auth="simulated")


def get(path: str, headers=AUTH, **params):
    async def run():
        async with client() as c:
            return await c.get(BASE + path, headers=headers, params=params)

    return asyncio.run(run())


def test_it_refuses_a_missing_or_wrong_token_in_graphs_error_format():
    for headers in ({}, {"Authorization": "Bearer nope"}):
        r = get(f"/sites/{mock_graph.HOST}:{mock_graph.SITE_PATH}", headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "InvalidAuthenticationToken" and "request-id" in r.headers
    assert get(f"/sites/{mock_graph.HOST}:{mock_graph.SITE_PATH}").status_code == 200


def test_it_finds_the_site_its_libraries_and_the_default_one():
    site = get(f"/sites/{mock_graph.HOST}:{mock_graph.SITE_PATH}").json()
    assert site["id"].startswith(mock_graph.HOST + ",") and site["displayName"] == mock_graph.SITE_NAME and site["webUrl"] == mock_graph.SITE_URL
    drives = get(f"/sites/{site['id']}/drives").json()["value"]
    assert [d["name"] for d in drives] == ["Documents", "Archive"] and all(d["driveType"] == "documentLibrary" and d["id"].startswith("b!") for d in drives)
    assert get(f"/sites/{site['id']}/drive").json()["name"] == "Documents"
    missing = get(f"/sites/{mock_graph.HOST}:/sites/nothing")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "itemNotFound"


def test_files_and_folders_look_like_drive_items():
    lib = mock_graph.library()
    kids = get(f"/drives/{mock_graph.DEFAULT_DRIVE}/root/children").json()["value"]
    assert sorted(k["name"] for k in kids) == ["Notes", "Planning", "Policies", "Pricing"] and all("folder" in k for k in kids)
    planning = next(k for k in kids if k["name"] == "Planning")
    assert planning["folder"]["childCount"] == 2 and len(planning["id"]) == 34
    files = get(f"/drives/{mock_graph.DEFAULT_DRIVE}/items/{planning['id']}/children").json()["value"]
    f = next(i for i in files if i["name"] == "Festive_Promo_Calendar_FY27.xlsx")
    assert f["lastModifiedDateTime"].startswith("2026-07-24T") and f["lastModifiedBy"]["user"]["displayName"] == "Meera Iyer"
    assert f["file"]["mimeType"].endswith("spreadsheetml.sheet") and f["parentReference"]["path"].endswith("/root:/Planning") and f["webUrl"].startswith(mock_graph.SITE_URL)
    assert f["@microsoft.graph.downloadUrl"].startswith("http://sim" + mock_graph.MOUNT + "/download/") and f["size"] == lib.items[f["id"]]["size"]
    only = get(f"/drives/{mock_graph.DEFAULT_DRIVE}/items/{f['id']}", **{"$select": "id,name"}).json()
    assert set(only) == {"id", "name", "@microsoft.graph.downloadUrl"}


def test_results_are_paged_with_top_and_a_next_link():
    first = get(f"/drives/{mock_graph.DEFAULT_DRIVE}/root/search(q='plan')", **{"$top": "1"}).json()
    assert len(first["value"]) == 1 and "$skiptoken=1" in first["@odata.nextLink"]

    async def run():
        async with client() as c:
            return (await c.get(first["@odata.nextLink"], headers=AUTH)).json()

    second = asyncio.run(run())
    assert second["value"] and second["value"][0]["id"] != first["value"][0]["id"]


def test_content_is_a_redirect_to_a_download_link_that_expires_and_cannot_be_forged():
    iid = mock_graph.library().by_rel["Pricing/Competitor_Price_Index_Sep2026.csv"]
    drive = mock_graph.DEFAULT_DRIVE

    async def run():
        async with httpx.AsyncClient(transport=client()._transport) as c:  # no redirect following: look at the 302
            r = await c.get(f"{BASE}/drives/{drive}/items/{iid}/content", headers=AUTH)
            assert r.status_code == 302 and "/download/" in r.headers["location"]
            link = r.headers["location"]
            ok = await c.get(link)  # pre-authenticated: no bearer token
            forged = await c.get(link[:-1] + ("0" if link[-1] != "0" else "1"))
            stale = await c.get("http://sim" + mock_graph.MOUNT + f"/download/{iid}.1.{'0' * 32}")
            folder = await c.get(f"{BASE}/drives/{drive}/items/{mock_graph.library().by_rel['Pricing']}/content", headers=AUTH)
            return ok, forged, stale, folder

    ok, forged, stale, folder = asyncio.run(run())
    assert ok.status_code == 200 and ok.content.startswith(b"Week start")
    assert forged.status_code == 403 and stale.status_code == 403 and folder.status_code == 400


def test_the_graph_backend_reads_the_simulated_site_as_it_reads_a_tenant():
    g, folder = backend(), FolderBackend(SAMPLE)
    for query in ("festive promotion calendar", "lease", "competitor price index", "markdown"):
        sim, ref = asyncio.run(g.search(query, 5)), asyncio.run(folder.search(query, 5))
        assert [f["name"] for f in sim] == [f["name"] for f in ref] and sim  # the same files found, in the same order, as the folder backend finds them
    found = asyncio.run(g.search("festive promotion calendar", 5))[0]
    assert found["path"] == "Planning/Festive_Promo_Calendar_FY27.xlsx" and found["modified"] == "2026-07-24" and found["kind"] == "table"
    assert found["site"] == mock_graph.SITE_NAME and found["url"].startswith(mock_graph.SITE_URL) and len(found["id"]) == 34
    entry, data = asyncio.run(g.fetch(found["id"]))
    assert data == (SAMPLE / "Planning" / "Festive_Promo_Calendar_FY27.xlsx").read_bytes() and parse.read_tables(entry["name"], data)[0].name == "Weekly plan"
    listed = asyncio.run(g.search("", 20))  # an empty query lists the library (the folders are walked)
    assert len(listed) == 5 and {f["kind"] for f in listed} == {"table", "document"}


def test_a_library_is_chosen_by_name_and_a_wrong_name_lists_the_choices():
    assert asyncio.run(backend("archive").search("plan", 5)) == []  # the second library is empty
    assert asyncio.run(backend("Documents").search("plan", 5))
    with pytest.raises(SharePointError, match="no document library called 'Reports'.*Documents, Archive"):
        asyncio.run(backend("Reports").search("plan", 5))


def test_errors_reach_the_agent_in_words(monkeypatch):
    with pytest.raises(SharePointError, match="not found"):
        asyncio.run(GraphBackend("https://contoso.sharepoint.com/sites/other", "", client(), graph_url=BASE, auth="simulated").search("x", 5))
    with pytest.raises(SharePointError, match="not found"):
        asyncio.run(backend().fetch("01ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"))
    from sharepoint import graph as graph_module

    monkeypatch.setattr(graph_module, "SHAREPOINT_AUTH", "token")  # a client whose token the site does not accept
    monkeypatch.setattr(graph_module, "SHAREPOINT_TOKEN", "a-different-token")
    with pytest.raises(SharePointError, match="no read access"):
        asyncio.run(GraphBackend(mock_graph.SITE_URL, "", client(), graph_url=BASE).search("x", 5))


def test_the_sharepoint_tools_work_the_same_over_the_simulated_site():
    sharepoint.set_backend(backend())
    try:
        found = json.loads(asyncio.run(gateway.search_sharepoint("festive promotion calendar", "T-sim")))
        assert found["count"] >= 1 and found["files"][0]["name"] == "Festive_Promo_Calendar_FY27.xlsx"
        plan = json.loads(asyncio.run(gateway.read_sharepoint_file(found["files"][0]["id"], filter="category=Laptops; week_start>=2026-08-03", conversation_id="T-sim")))
        assert plan["kind"] == "sharepoint" and plan["row_count"] >= 5 and plan["columns"][0] == "Week start"
        doc_id = json.loads(asyncio.run(gateway.search_sharepoint("markdown policy", "T-sim")))["files"][0]["id"]
        doc = json.loads(asyncio.run(gateway.read_sharepoint_file(doc_id, focus="laptops cap", conversation_id="T-sim")))
        assert doc["kind"] == "document" and doc["columns"] == ["section", "text"]
    finally:
        sharepoint.set_backend(None)
        ledger.case("T-sim").tables.clear()


def test_the_simulated_backend_is_chosen_by_setting_and_points_at_the_hub_itself(monkeypatch):
    monkeypatch.setattr(sharepoint, "SHAREPOINT_BACKEND", "simulated")
    monkeypatch.setenv("PORT", "9099")
    sharepoint.set_backend(None)
    try:
        g = sharepoint.get_backend()
        assert g.graph == "http://127.0.0.1:9099/graph-sim/v1.0" and g.site_url == mock_graph.SITE_URL
    finally:
        sharepoint.set_backend(None)
