"""Ask one question straight to the hub's data tool and print what comes back: the SQL, the columns and the first rows.
Use it to check a data connection before involving any agent.

    python integrations/genie/try_question.py "How many stores are there in each zone?"

Reads HUB_URL and HUB_API_KEY from .env."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in l and not l.lstrip().startswith("#"))
TOOL = "query_space_croma_pricing"


async def main(question: str) -> None:
    async with streamablehttp_client(env["HUB_URL"].rstrip("/") + "/genie/mcp", headers={"x-api-key": env["HUB_API_KEY"]}) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            print("tools on the hub:", tools)
            res = await session.call_tool(TOOL, {"query": question, "conversation_id": "try-question"})
            out = json.loads(res.content[0].text)
    if out.get("error"):
        print("ERROR:", out["error"])
        return
    print("backend:", out.get("backend"), "| result id:", out.get("result_id"), "| rows:", out.get("row_count"), "| seconds:", out.get("elapsed_s"))
    print("SQL:", out.get("sql"))
    print("columns:", out.get("columns"))
    for row in (out.get("rows") or [])[:10]:
        print("  ", row)


asyncio.run(main(" ".join(sys.argv[1:]) or "How many stores are there in each zone?"))
