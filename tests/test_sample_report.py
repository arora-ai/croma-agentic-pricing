"""The sample report that ships with the app (the About page links to it) must not carry the data space's real names or any environment identifier."""
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sanitise_sample_report as sample  # noqa: E402

FOLDER = sample.DEFAULT_OUT
HTML, DOCX = FOLDER / f"{sample.NAME}.html", FOLDER / f"{sample.NAME}.docx"


def test_the_sample_is_there_and_is_a_real_report():
    html = HTML.read_text(encoding="utf-8")
    assert "Weekly commercial pulse" in html and "THE BOTTOM LINE" in html.upper() and html.count("<table") >= 10 and html.count("<img") >= 3
    with zipfile.ZipFile(DOCX) as z:
        assert "word/document.xml" in z.namelist() and any(n.startswith("word/media/") for n in z.namelist())
    assert (FOLDER / f"{sample.NAME}-p1.png").exists() and (FOLDER / f"{sample.NAME}-p3.png").exists() and (FOLDER / f"{sample.NAME}-p8.png").exists(), "the About page shows these three page images"


def test_no_sql_or_data_space_name_or_environment_identifier_is_left_in_it():
    html = HTML.read_text(encoding="utf-8")
    assert sample.problems(html, DOCX) == []
    assert html.count(sample.NOTE) == 10  # every query block was replaced, none dropped silently


def test_the_checker_catches_what_it_is_there_for(tmp_path):
    leaking = "<p>Queries</p><pre>SELECT a FROM dbx_croma_pricing.fct_transaction.t WHERE x = 1</pre> adb.azuredatabricks.net 0b1c2d3e-0000-4000-8000-123456789abc"
    found = " ".join(sample.problems(leaking, DOCX))
    assert all(token in found for token in ("dbx_", "fct_", "SELECT", "azuredatabricks", "0b1c2d3e"))
    assert sample.strip_html(leaking).count(sample.NOTE) == 1 and "SELECT" not in sample.strip_html(leaking)
    ordinary = "<p>Stores fell where units fell; group by zone, with the data from the week.</p>"
    assert sample.problems(ordinary, DOCX) == [], "ordinary prose must not trip the SQL keyword check"
    assert not re.search(r"C-[0-9a-f]{8}", HTML.read_text(encoding="utf-8")), "case ids belong to a deployment, not to the sample"
