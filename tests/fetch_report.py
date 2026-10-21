"""Downloads a published report from the hub and renders it to PNG pages for a visual check.
Usage: python tests/fetch_report.py C-xxxxxxxx [n=1]"""
import subprocess
import sys
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env = dict(l.split("=", 1) for l in (root / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in l)
case, n = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "1"
out = root / "tests" / "_out" / case
out.mkdir(parents=True, exist_ok=True)
for ext in ("docx", "html"):
    req = urllib.request.Request(f"{env['HUB_URL']}/report/{case}/{n}.{ext}", headers={"x-api-key": env["HUB_API_KEY"]})
    (out / f"report.{ext}").write_bytes(urllib.request.urlopen(req, timeout=60).read())
print("saved to", out)
subprocess.run([sys.executable, str(root / "tests" / "docx_to_png.py"), str(out / "report.docx"), str(out / "png")], check=True)
