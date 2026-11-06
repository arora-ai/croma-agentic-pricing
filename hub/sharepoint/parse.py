"""Turns the files of a SharePoint library into what the evidence ledger holds: a spreadsheet or CSV becomes tables (columns and rows), and a Word, PDF, PowerPoint or
text file becomes passages of text. Nothing here talks to SharePoint, so the Graph backend and the folder backend share it and the tests need no network."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass

TABLE_TYPES = {".xlsx", ".xlsm", ".csv", ".tsv"}
DOCUMENT_TYPES = {".docx", ".pdf", ".pptx", ".txt", ".md"}
PASSAGE_CHARS = 900


class ParseError(ValueError):
    """A file that cannot be read, with a reason fit to give the SharePoint agent."""


@dataclass
class Sheet:
    name: str
    columns: list[str]
    rows: list[list]


def extension(name: str) -> str:
    m = re.search(r"\.[A-Za-z0-9]+$", name)
    return m.group(0).lower() if m else ""


def kind_of(name: str) -> str | None:
    """'table' for spreadsheets and CSV, 'document' for text documents, None for anything the connector does not read."""
    ext = extension(name)
    return "table" if ext in TABLE_TYPES else ("document" if ext in DOCUMENT_TYPES else None)


# ---------------------------------------------------------------- tables

def _cell(value):
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time(0) else value.isoformat(timespec="minutes")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _tidy(raw: list[list], percent: list[bool], name: str, max_rows: int) -> Sheet | None:
    """The table in a grid of cells: the first row with at least two filled cells is the header (title rows above it are skipped), blank rows are dropped, and a column
    whose cells are formatted as percentages is shown in percent with '%' in its name, so 12% is not read as 0.12."""
    grid = [r for r in raw if any(c is not None for c in r)]
    start = next((i for i, r in enumerate(grid) if sum(c is not None for c in r) >= 2), None)
    if start is None:
        return None
    header = grid[start]
    width = max(len(r) for r in grid[start:])
    used = [j for j in range(width) if any((r[j] if j < len(r) else None) is not None for r in grid[start:])]
    columns: list[str] = []
    for j in used:
        label = str(header[j]).strip() if j < len(header) and header[j] is not None else f"column_{j + 1}"
        while label in columns:
            label += "_2"
        columns.append(label)
    pct_cols = {j for j in used if j < len(percent) and percent[j]}
    rows = []
    for r in grid[start + 1:]:
        row = []
        for j in used:
            v = r[j] if j < len(r) else None
            row.append(round(v * 100, 10) if j in pct_cols and _is_number(v) else v)
        rows.append(row)
    for k, j in enumerate(used):
        if j in pct_cols and "%" not in columns[k] and "pct" not in columns[k].lower() and "percent" not in columns[k].lower():
            columns[k] += " %"
    return Sheet(name, columns, rows[:max_rows]) if rows else None


def _number(text: str):
    """A CSV cell as a number when it is one (thousands separators allowed), else as it is."""
    s = text.strip()
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?|-?\d+(\.\d+)?", s):
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return text
    return text


def read_tables(name: str, data: bytes, max_rows: int = 5000) -> list[Sheet]:
    """The sheets of a workbook (or the one table of a CSV) that have a header and at least one row."""
    ext = extension(name)
    if ext in (".csv", ".tsv"):
        text = data.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter="\t" if ext == ".tsv" else ",")
        grid = [[_cell(_number(c)) for c in row] for row in reader]
        sheet = _tidy(grid, [], "data", max_rows)
        return [sheet] if sheet else []
    if ext in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise ParseError(f"{name} is not a workbook that can be read ({type(exc).__name__})")
        sheets = []
        for ws in wb.worksheets:
            if ws.sheet_state != "visible":
                continue
            grid, formats = [], []
            for row in ws.iter_rows():
                grid.append([_cell(c.value) for c in row])
                formats.append([("%" in (getattr(c, "number_format", "") or "")) for c in row])
            width = max((len(f) for f in formats), default=0)
            percent = []
            for j in range(width):
                numeric = [i for i, g in enumerate(grid) if j < len(g) and _is_number(g[j])]
                pct = [i for i in numeric if j < len(formats[i]) and formats[i][j]]
                percent.append(bool(numeric) and len(pct) * 2 > len(numeric))  # most of the numbers in the column are formatted as percentages
            sheet = _tidy(grid, percent, ws.title, max_rows)
            if sheet:
                sheets.append(sheet)
        wb.close()
        return sheets
    raise ParseError(f"{name} is not a spreadsheet or CSV file")


def _col_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


FILTER_TERM = re.compile(r"^\s*(?P<col>.+?)\s*(?P<op>>=|<=|!=|=|>|<)\s*(?P<val>.+?)\s*$")


def filter_rows(sheet: Sheet, spec: str) -> tuple[list[list], str]:
    """The rows of a sheet that satisfy a filter such as 'category=Laptops; week_start>=2026-08-03; week_start<=2026-09-27' (terms joined with ';', all must hold).
    Text compares without regard to case; numbers compare as numbers; ISO dates compare as dates. Returns (rows, a sentence on what was applied). A column that is not
    in the sheet is an error, so a typo is not mistaken for 'no rows'."""
    terms = [t for t in re.split(r"[;\n]", spec) if t.strip()]
    if not terms:
        return sheet.rows, ""
    index = {_col_key(c): i for i, c in enumerate(sheet.columns)}
    parsed = []
    for t in terms:
        m = FILTER_TERM.match(t)
        if not m:
            raise ParseError(f"the filter term '{t.strip()}' is not of the form column=value (operators: = != > >= < <=)")
        key = _col_key(m["col"])
        if key not in index:
            near = [i for k, i in index.items() if k.startswith(key) or key.startswith(k)]
            if len(near) != 1:
                raise ParseError(f"the filter column '{m['col'].strip()}' is not in the sheet. Its columns are: {', '.join(sheet.columns)}")
            index[key] = near[0]
        parsed.append((index[key], m["op"], m["val"].strip().strip("'\"")))

    def holds(cell, op: str, want: str) -> bool:
        if cell is None:
            return op == "!="
        try:
            a, b = float(cell), float(want.replace(",", ""))
        except (TypeError, ValueError):
            a, b = str(cell).strip().lower(), want.lower()
        return {"=": a == b, "!=": a != b, ">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]

    rows = [r for r in sheet.rows if all(holds(r[i], op, val) for i, op, val in parsed)]
    return rows, f"Filter applied: {'; '.join(t.strip() for t in terms)} ({len(rows)} of {len(sheet.rows)} rows)."


# ---------------------------------------------------------------- documents

def _chunks(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Paragraphs (section heading, text) merged into passages of about PASSAGE_CHARS, never across a heading."""
    out: list[tuple[str, str]] = []
    for section, text in blocks:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if out and out[-1][0] == section and len(out[-1][1]) + len(text) < PASSAGE_CHARS:
            out[-1] = (section, out[-1][1] + " " + text)
        else:
            while len(text) > PASSAGE_CHARS * 1.5:
                cut = text.rfind(". ", 0, PASSAGE_CHARS)
                cut = cut + 1 if cut > 200 else PASSAGE_CHARS
                out.append((section, text[:cut].strip()))
                text = text[cut:].strip()
            out.append((section, text))
    return out


def read_passages(name: str, data: bytes) -> list[tuple[str, str]]:
    """(section, text) passages of a document, in reading order."""
    ext = extension(name)
    blocks: list[tuple[str, str]] = []
    if ext in (".txt", ".md"):
        section = name
        for para in re.split(r"\n\s*\n", data.decode("utf-8-sig", errors="replace")):
            lines = para.strip().splitlines()
            if lines and lines[0].startswith("#"):
                section = lines[0].lstrip("# ").strip() or section
                para = "\n".join(lines[1:])
            blocks.append((section, para))
    elif ext == ".docx":
        try:
            from docx import Document
            from docx.table import Table as DocxTable
            from docx.text.paragraph import Paragraph

            doc = Document(io.BytesIO(data))
        except Exception as exc:
            raise ParseError(f"{name} is not a Word document that can be read ({type(exc).__name__})")
        section = name
        for block in doc.element.body.iterchildren():
            tag = block.tag.rsplit("}", 1)[-1]
            if tag == "p":
                p = Paragraph(block, doc)
                if (p.style.name or "").lower().startswith(("heading", "title")) and p.text.strip():
                    section = p.text.strip()
                else:
                    blocks.append((section, p.text))
            elif tag == "tbl":
                for row in DocxTable(block, doc).rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        blocks.append((section, " | ".join(cells) + "."))
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            for n, page in enumerate(reader.pages, 1):
                blocks.append((f"{name}, page {n}", page.extract_text() or ""))
        except Exception as exc:
            raise ParseError(f"{name} is not a PDF that can be read ({type(exc).__name__})")
    elif ext == ".pptx":
        try:
            from pptx import Presentation

            deck = Presentation(io.BytesIO(data))
        except ImportError:
            raise ParseError("PowerPoint files are not supported by this hub (python-pptx is not installed)")
        except Exception as exc:
            raise ParseError(f"{name} is not a presentation that can be read ({type(exc).__name__})")
        for n, slide in enumerate(deck.slides, 1):
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                    blocks.append((f"{name}, slide {n}", shape.text_frame.text))
    else:
        raise ParseError(f"{name} is not a text document")
    return _chunks(blocks)


def pick_passages(passages: list[tuple[str, str]], focus: str, limit: int) -> list[tuple[str, str]]:
    """The passages most relevant to a few keywords, kept in reading order; without keywords, the first ones."""
    if len(passages) <= limit:
        return passages
    words = set(re.findall(r"[a-z0-9]{3,}", focus.lower()))
    if not words:
        return passages[:limit]
    scored = []
    for i, (section, text) in enumerate(passages):
        hay = f"{section} {text}".lower()
        scored.append((sum(hay.count(w) for w in words), -i, i))
    best = sorted(i for _score, _neg, i in sorted(scored, reverse=True)[:limit])
    return [passages[i] for i in best]
