"""A local folder standing in for a SharePoint document library. Lets the connector, the SharePoint agent and the harnesses be exercised end to end on synthetic
files, with no tenant access. The files are found and read exactly as the Graph backend's would be: by search, then by id."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from sharepoint import parse
from sharepoint.base import SharePointError


class FolderBackend:
    label = "folder"

    def __init__(self, root: Path, site: str = "Croma merchandising library (sample)"):
        self.root, self.site = Path(root).resolve(), site
        self._text: dict[tuple[str, float], str] = {}

    def _entry(self, path: Path) -> dict:
        rel = path.relative_to(self.root).as_posix()
        st = path.stat()
        return {"id": rel, "name": path.name, "path": rel, "site": self.site, "modified": dt.datetime.fromtimestamp(st.st_mtime).date().isoformat(), "size": st.st_size,
                "url": f"folder://{rel}", "kind": parse.kind_of(path.name)}

    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            raise SharePointError(f"the sample library folder does not exist: {self.root.name}")
        return [p for p in sorted(self.root.rglob("*")) if p.is_file() and not p.name.startswith(("~$", ".")) and parse.kind_of(p.name)]

    def _content(self, path: Path) -> str:
        """The words a search can match inside a file: sheet names, headings and the first rows of a table, or the text of a document."""
        key = (str(path), path.stat().st_mtime)
        if key not in self._text:
            try:
                data = path.read_bytes()
                if parse.kind_of(path.name) == "table":
                    text = " ".join(f"{s.name} {' '.join(s.columns)} " + " ".join(str(c) for r in s.rows[:40] for c in r if isinstance(c, str)) for s in parse.read_tables(path.name, data))
                else:
                    text = " ".join(f"{section} {body}" for section, body in parse.read_passages(path.name, data))
            except Exception:
                text = ""
            self._text[key] = text.lower()
        return self._text[key]

    async def search(self, query: str, top: int = 10) -> list[dict]:
        words = [w for w in re.findall(r"[a-z0-9]{3,}", query.lower())]
        scored = []
        for path in self._files():
            entry = self._entry(path)
            if not words:
                scored.append((0, entry["modified"], entry))
                continue
            name = f"{path.stem} {path.parent.name}".lower().replace("_", " ").replace("-", " ")
            content = self._content(path)
            score = sum(3 * (w in name) + (w in content) for w in words)
            if score:
                scored.append((score, entry["modified"], entry))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [e for _s, _m, e in scored[:top]]

    async def fetch(self, file_id: str) -> tuple[dict, bytes]:
        path = (self.root / file_id).resolve()
        if self.root not in path.parents or not path.is_file():
            raise SharePointError(f"no file with id '{file_id}' in the library: use an id returned by search_sharepoint")
        return self._entry(path), path.read_bytes()
