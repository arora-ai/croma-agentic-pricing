"""Call a studio tool directly over MCP (for testing). Usage: python tests/studio_call.py TOOL '{"json": "args"}'"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

env = dict(l.split("=", 1) for l in (Path(__file__).parents[1] / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in l)


async def main(tool: str, args: dict):
    async with streamablehttp_client(env["HUB_URL"] + "/studio/mcp", headers={"x-api-key": env["HUB_API_KEY"]}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            text = "".join(c.text for c in res.content if hasattr(c, "text"))
            if len(sys.argv) > 4:
                Path(sys.argv[4]).write_text(text, encoding="utf-8")
                print("wrote", sys.argv[4], len(text))
            else:
                print(text.encode("ascii", "replace").decode())


asyncio.run(main(sys.argv[1], {"case_id": sys.argv[2], **({"force": True} if len(sys.argv) > 3 and sys.argv[3] == "force" else {})}))
