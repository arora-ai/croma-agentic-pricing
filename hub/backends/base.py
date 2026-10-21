"""The contract every data backend meets.

The Data agent asks the hub's data tool a natural-language question; the hub passes it to a backend and returns what the backend gives back. A backend is
an async function:

    async def ask(question: str, conversation_id: str = "") -> dict

It returns ONE of these dictionaries.

Success
    {
      "question":    the question as asked,
      "description": one sentence on what the query computes (optional),
      "sql":         the SQL that was run (optional but strongly recommended: it is shown in the admin view and kept in the evidence appendix),
      "columns":     ["channel", "cy_nmv", ...]            column names, in order. The report team refers to columns by these names,
      "rows":        [["Stores", 25.64, ...], ...]         a list of rows; numbers as numbers, everything else as strings,
      "row_count":   len(rows),
      "truncated_at_max_rows": False,                       True if the backend returned fewer rows than the query produced,
      "elapsed_s":   2.4,                                   optional,
      "backend":     "my-backend",                          optional label,
    }

Failure (never raise for a business-level failure; return this so the Data agent can rephrase or report the gap)
    {"question": ..., "error": "What went wrong, in a sentence the Data agent can act on.", "backend": "my-backend"}

Rules the rest of the system relies on
- Money and quantity columns come back as numbers, not formatted strings. The report studio formats them.
- Percentages are returned as numbers in percent units (51.4 means 51.4%) unless the column name says otherwise.
- Give a backend no more than the timeout in settings (GENIE_TIMEOUT_S, 90 seconds): Foundry cuts a tool call off at 100 seconds.
- Each call is independent. The Data agent writes self-contained questions and may ask several at once, so a backend must be safe to call concurrently.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Protocol


class Backend(Protocol):
    async def __call__(self, question: str, conversation_id: str = "") -> dict: ...


AskFn = Callable[[str, str], Awaitable[dict]]


def failure(question: str, message: str, backend: str) -> dict:
    return {"question": question, "error": message, "backend": backend}
