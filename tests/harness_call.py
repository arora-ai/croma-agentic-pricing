"""Runs the Analysis or Research harness on the hub directly, without the CX agent, so a run can be repeated and timed.

    python tests/harness_call.py analysis "Why did merchandise margin % for Stores move in September 2026 versus August 2026? ..." [--out brief.md]
    python tests/harness_call.py research "Why did ...?" [--case C-1234abcd]       (--case reuses a case, so earlier evidence and findings are on it)

Reads HUB_URL and HUB_API_KEY from .env. Prints the stage timings from /runs/<case> and writes the finished brief to --out (UTF-8)."""
import argparse
import asyncio
import json
import re
import sys
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).parents[1]
env = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in line and not line.startswith("#"))
sys.stdout.reconfigure(encoding="utf-8")


async def call(path: str, tool: str, args: dict) -> str:
    async with streamablehttp_client(env["HUB_URL"] + path, headers={"x-api-key": env["HUB_API_KEY"]}, timeout=timedelta(seconds=150)) as (r, w, _):
        async with ClientSession(r, w, read_timeout_seconds=timedelta(seconds=150)) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            return "".join(c.text for c in res.content if hasattr(c, "text"))


def get(path: str):
    req = urllib.request.Request(env["HUB_URL"] + path, headers={"x-api-key": env["HUB_API_KEY"]})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("harness", choices=["analysis", "research"])
    p.add_argument("brief")
    p.add_argument("--case")
    p.add_argument("--out")
    a = p.parse_args()
    case_id = a.case or json.loads(await call("/context/mcp", "create_case", {"request_summary": a.brief[:400]}))["case_id"]
    print("case", case_id)
    started = time.time()
    text = await call("/agents/mcp", f"ask_{a.harness}_agent", {"brief": a.brief, "case_id": case_id})
    while text.startswith("STILL RUNNING"):
        job = re.search(r"job (J-\w+)", text).group(1)
        print(f"  ... still running after {time.time() - started:.0f}s ({job})")
        text = await call("/agents/mcp", "wait_for_agent", {"job_id": job})
    print(f"finished in {time.time() - started:.0f}s\n")
    for run in get(f"/runs/{case_id}"):
        print(f"run {run['id']} {run['status']} {run['error']}")
        for s in run["stages"]:
            seconds = round((s["ended"] or 0) - (s["started"] or 0), 1) if s["started"] and s["ended"] else ""
            print(f"  {s['name']:12s} {s['status']:9s} {str(seconds):>6}s  {s['summary']} {s['error']}")
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print("\nbrief written to", a.out)
    print("\n" + text)


asyncio.run(main())
