"""The sample report's Word file must be served with the Word media type. The container image has no system table of media types, so without
an explicit entry it was an untyped download (a Windows machine, which has a table, would not show the problem)."""
import mimetypes
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from ui.backend import main  # noqa: E402


def test_a_word_file_gets_the_word_type_on_a_machine_that_knows_no_media_types(monkeypatch):
    monkeypatch.setattr(mimetypes, "_db", mimetypes.MimeTypes())  # a bare table, like the container image's
    main.register_media_types()
    assert mimetypes.guess_type("weekly-commercial-pulse.docx")[0] == main.WORD_TYPE
    assert main.WORD_TYPE == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
