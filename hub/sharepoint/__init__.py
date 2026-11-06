"""The configured SharePoint backend (settings.SHAREPOINT_BACKEND), created on first use."""
from __future__ import annotations

from pathlib import Path

from settings import SHAREPOINT_BACKEND, SHAREPOINT_FOLDER
from sharepoint.base import Backend, SharePointError

SAMPLE_LIBRARY = Path(__file__).parent / "sample_library"
_backend: Backend | None = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        if SHAREPOINT_BACKEND == "graph":
            from sharepoint.graph import GraphBackend

            _backend = GraphBackend()
        elif SHAREPOINT_BACKEND == "folder":
            from sharepoint.folder import FolderBackend

            _backend = FolderBackend(Path(SHAREPOINT_FOLDER) if SHAREPOINT_FOLDER else SAMPLE_LIBRARY)
        else:
            raise SharePointError("SharePoint is switched off on this hub (SHAREPOINT_BACKEND=off)")
    return _backend


def set_backend(backend: Backend | None) -> None:
    """For tests: use this backend instead of the configured one (None goes back to the configured one)."""
    global _backend
    _backend = backend
