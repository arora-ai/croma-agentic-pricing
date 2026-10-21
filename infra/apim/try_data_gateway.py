"""Asks one question through the APIM data gateway, using the hub's own Databricks backend in DATABRICKS_AUTH=apim mode, and prints the answer.
This is the same code path the hub takes when it is switched to the gateway, run from your machine: nothing but the APIM subscription key leaves it, and
APIM signs in to Databricks with its own managed identity.

    python infra/apim/try_data_gateway.py --space-id <genie space id> ["What was the total NMV in March 2026 in Rs crore?"]

Reads APIM_DATA_URL and APIM_DATA_KEY from .apim.env (written by deploy_apim.ps1). Prints no secret."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_QUESTION = "What was the total NMV in March 2026 in Rs crore?"


def read_env(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*")
    parser.add_argument("--space-id", required=True, help="the Genie space id (infra/config.ps1: Genie.SpaceId)")
    parser.add_argument("--json", action="store_true", help="print the whole result as JSON")
    args = parser.parse_args()

    env = read_env(ROOT / ".apim.env")
    if not env.get("APIM_DATA_URL") or not env.get("APIM_DATA_KEY"):
        print("APIM_DATA_URL and APIM_DATA_KEY are not in .apim.env: run infra/apim/deploy_apim.ps1 first.")
        return 2
    os.environ.update(GENIE_BACKEND="databricks", DATABRICKS_AUTH="apim", DATABRICKS_HOST=env["APIM_DATA_URL"],
                      APIM_SUBSCRIPTION_KEY=env["APIM_DATA_KEY"], GENIE_SPACE_ID=args.space_id)
    from backends import databricks_genie  # imported after the environment is set: the hub reads its settings at import

    question = " ".join(args.question) or DEFAULT_QUESTION
    result = asyncio.run(databricks_genie.ask(question, "try-data-gateway"))
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif result.get("error"):
        print("ERROR:", result["error"])
    else:
        print(f"gateway: {env['APIM_DATA_URL']}   auth mode: apim (Ocp-Apim-Subscription-Key only, no Authorization header)")
        print(f"question: {question}")
        print(f"backend: {result['backend']} | rows: {result['row_count']} | seconds: {result['elapsed_s']}")
        print("columns:", result["columns"])
        for row in result["rows"][:10]:
            print("  ", row)
        print("SQL:", (result.get("sql") or "").strip().replace("\n", " ")[:300])
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
