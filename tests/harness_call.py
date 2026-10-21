"""Runs the Analysis or Research harness on the hub directly, without the CX agent, so a run can be repeated and timed.

    python tests/harness_call.py analysis "Why did merchandise margin % for Stores move in September 2026 versus August 2026? ..." [--out brief.md]
    python tests/harness_call.py research "Why did ...?" [--case C-1234abcd]       (--case reuses a case, so earlier evidence and findings are on it)

Reads HUB_URL and HUB_API_KEY from .env. Starts the tool call, then follows the run through /runs/<case> (so a dropped connection does not lose the run),
prints each stage as it finishes, and writes the finished brief to --out (UTF-8)."""
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


async def collect(job_text: str) -> None:
    """Hands the finished job back to the hub (it keeps a finished job until somebody collects it). Best effort."""
    while job_text.startswith("STILL RUNNING"):
        job = re.search(r"job (J-\w+)", job_text).group(1)
        try:
            job_text = await call("/agents/mcp", "wait_for_agent", {"job_id": job})
        except BaseException:
            return


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
    ask = asyncio.create_task(call("/agents/mcp", f"ask_{a.harness}_agent", {"brief": a.brief, "case_id": case_id}))
    seen: set[tuple[str, str]] = set()
    run = None
    while True:
        await asyncio.sleep(6)
        try:
            runs = await asyncio.to_thread(get, f"/runs/{case_id}")
        except Exception as exc:
            print(f"  (could not read the run: {type(exc).__name__})")
            continue
        run = runs[-1] if runs else None
        for s in (run or {}).get("stages", []):
            key = (s["name"], s["status"])
            if s["status"] in ("done", "failed") and key not in seen:
                seen.add(key)
                seconds = round((s["ended"] or 0) - (s["started"] or 0), 1) if s["started"] and s["ended"] else ""
                print(f"  [{time.time() - started:5.0f}s] {s['name']:12s} {s['status']:7s} {str(seconds):>6}s  {s['summary']} {s['error']}")
        if run and run["status"] in ("done", "failed"):
            break
        if ask.done() and not run:
            break
    print(f"\nfinished in {time.time() - started:.0f}s: run {run['id']} {run['status']} {run['error']}")
    if ask.done() and ask.exception() is None:
        await collect(ask.result())
    elif not ask.done():
        ask.cancel()
    if run and run["result"]:
        text = run["result"]["markdown"]
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
            print("brief written to", a.out)
        print("\n" + text)


asyncio.run(main())
