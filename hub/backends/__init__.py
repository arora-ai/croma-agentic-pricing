"""Backend registry: the hub's data tool calls whichever backend GENIE_BACKEND names."""
from __future__ import annotations

from settings import GENIE_BACKEND

BACKENDS = ("databricks", "custom", "stub")


def get_ask():
    """Returns the active backend's ask function. Imports lazily so an unused backend's dependencies are never loaded."""
    if GENIE_BACKEND == "databricks":
        from backends.databricks_genie import ask
    elif GENIE_BACKEND == "custom":
        from backends.custom import ask
    elif GENIE_BACKEND == "stub":
        from backends.stub import ask
    else:
        raise RuntimeError(f"Unknown GENIE_BACKEND '{GENIE_BACKEND}'. Use one of: {', '.join(BACKENDS)}.")
    return ask
