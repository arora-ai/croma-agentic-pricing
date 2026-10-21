"""EXAMPLE custom backend. Not used unless you copy it into hub/ and set CUSTOM_BACKEND=custom_backend_example:ask.

It shows the shape of a backend with two small patterns: calling an HTTP service you already have (replace the URL and payload), and a tiny local fake so the
whole system can be exercised without any data source. The fake returns invented numbers: it is for plumbing tests only, never for a demo of real results.

The contract is documented in hub/backends/base.py."""
from __future__ import annotations

import os
import time

import httpx

SERVICE_URL = os.getenv("MY_DATA_SERVICE_URL", "")  # for example https://my-data-agent.internal/ask  (set it on the hub if you use the HTTP pattern)


async def ask(question: str, conversation_id: str = "") -> dict:
    started = time.time()
    if SERVICE_URL:
        # Pattern 1: your own service. It should answer within 90 seconds and return SQL and rows (or you can build them here).
        async with httpx.AsyncClient(timeout=85) as http:
            r = await http.post(SERVICE_URL, json={"question": question, "conversation_id": conversation_id})
        if r.status_code >= 400:
            return {"question": question, "error": f"The data service returned {r.status_code}: {r.text[:200]}", "backend": "my-service"}
        data = r.json()  # adapt to whatever your service returns
        return {"question": question, "sql": data.get("sql", ""), "columns": data["columns"], "rows": data["rows"], "row_count": len(data["rows"]),
                "elapsed_s": round(time.time() - started, 1), "backend": "my-service"}

    # Pattern 2: a local fake, for plumbing tests.
    if "channel" in question.lower():
        rows = [["Stores", 100.0, 90.0], ["Online", 20.0, 18.0]]
        return {"question": question, "description": "FAKE sample: NMV by channel", "sql": "-- fake data, no query was run",
                "columns": ["channel", "cy_nmv", "ly_nmv"], "rows": rows, "row_count": len(rows), "backend": "example-fake"}
    return {"question": question, "error": "The example backend only fakes a 'by channel' question. Replace it with your own.", "backend": "example-fake"}
