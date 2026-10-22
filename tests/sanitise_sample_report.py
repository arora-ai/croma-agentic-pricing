"""Makes a report safe to ship inside the repository as the app's sample: the evidence appendix lists the SQL that produced each figure, and that SQL names the real
catalog, schemas and tables of the data space. This replaces every SQL block with a note, then refuses to write anything that still looks like it carries an identifier.

    python tests/sanitise_sample_report.py <report.html> <report.docx> [out_dir]

Fetch the report first with tests/fetch_report.py (or from the chat app: /api/report/<case>/<n>/html and /docx). The default out_dir is the folder the About page links to,
ui/frontend/public/assets/sample/. The checks in DENY are also what tests/test_sample_report.py enforces on the shipped files."""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

NOTE = "SQL omitted from this sample."
NAME = "weekly-commercial-pulse"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "ui" / "frontend" / "public" / "assets" / "sample"

# Anything that names the data space, the Azure environment or a credential (any case), and SQL keywords (upper case only: ordinary prose says "where" and "group by").
DENY_NAMES = re.compile(
    r"dbx_|fct_|dim_master|kybm|all_stores_daily|article_mapping|snop_category|azuredatabricks|azurecontainerapps|azure-api\.net|services\.ai\.azure"
    r"|x-api-key|api[_-]?key|\bsecret\b|\bBearer\b|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
DENY_SQL = re.compile(r"\b(?:SELECT|FROM|WHERE|GROUP BY|ORDER BY|LEFT JOIN|INNER JOIN)\b")
CASE_ID = re.compile(r"\bC-[0-9a-f]{8}\b")


def leaks(text: str) -> list[str]:
    return [m.group(0) for rx in (DENY_NAMES, DENY_SQL) for m in rx.finditer(text)]


def strip_html(text: str) -> str:
    return re.sub(r"<pre>.*?</pre>", f"<pre>{NOTE}</pre>", text, flags=re.S)


def strip_docx(src: Path, dst: Path) -> int:
    """Replaces the SQL runs and returns how many there were. The renderer sets SQL in Consolas (nothing else is), so that is the test; text that starts like a query is the second net."""
    from docx import Document

    doc = Document(src)
    n = 0
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            if run.font.name == "Consolas" or run.text.lstrip().upper().startswith(("SELECT", "WITH ")):
                run.text = NOTE
                n += 1
    doc.save(dst)
    return n


def problems(html: str, docx: Path) -> list[str]:
    found = [f"html: {w!r}" for w in leaks(re.sub(r"<[^>]+>", " ", html))]
    found += [f"html: case id {m.group(0)}" for m in CASE_ID.finditer(html)]
    with zipfile.ZipFile(docx) as z:
        for part in z.namelist():
            if part.endswith((".xml", ".rels")):
                body = re.sub(r"<[^>]+>", " ", z.read(part).decode("utf-8", "ignore"))
                found += [f"docx {part}: {w!r}" for w in leaks(body)]
    return found


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    html_in, docx_in = Path(sys.argv[1]), Path(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    html = strip_html(html_in.read_text(encoding="utf-8"))
    docx_out = out / f"{NAME}.docx"
    runs = strip_docx(docx_in, docx_out)
    bad = problems(html, docx_out)
    if bad:
        docx_out.unlink(missing_ok=True)
        print("Not written: the report still carries identifiers:\n  " + "\n  ".join(bad[:15]))
        return 1
    (out / f"{NAME}.html").write_text(html, encoding="utf-8")
    print(f"Wrote {out / (NAME + '.html')} and {docx_out} ({html.count(NOTE)} SQL blocks and {runs} SQL runs replaced).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
