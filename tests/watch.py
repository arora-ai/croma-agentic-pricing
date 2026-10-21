"""Prints a compact live view of the hub's events. Usage: python watch.py [since_seq]"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

env = dict(l.split("=", 1) for l in (Path(__file__).parents[1] / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in l)
since = int(sys.argv[1]) if len(sys.argv) > 1 else 0
req = urllib.request.Request(env["HUB_URL"] + f"/events?since={since}", headers={"x-api-key": env["HUB_API_KEY"]})
data = json.load(urllib.request.urlopen(req, timeout=30))
for e in data["events"]:
    t = time.strftime("%H:%M:%S", time.localtime(e["ts"] / 1000))
    if e["type"] == "agent_start":
        print(f"{e['seq']:5d} {t} >> {e['caller']} -> {e['agent']}: {(e.get('ask') or '')[:110]!r}")
    elif e["type"] == "agent_end":
        print(f"{e['seq']:5d} {t} << {e['agent']} done {e.get('seconds')}s ok={e.get('ok')} tokens={e.get('output_tokens')}")
    elif e["type"] == "tool_end":
        print(f"{e['seq']:5d} {t}    {e.get('caller')}: {e.get('server')}.{e.get('tool')} {'ok' if e.get('ok') else 'ERR'} {(e.get('summary') or '')[:90]}")
print("latest", data["latest"])
