"""The data profile: what the agents are told about the data space they sit on top of.

Prompts in agents/prompts/*.md contain placeholders such as {{COVERAGE}}; this module fills them from config/data_profile.json when the agents are
deployed. Edit that one file to point the system at a different data space; no prompt needs to change."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROFILE = ROOT / "config" / "data_profile.json"
EXAMPLE = ROOT / "config" / "data_profile.example.json"

PLACEHOLDERS = {"COVERAGE": "coverage", "FISCAL_YEAR": "fiscal_year", "ZONES": "zones", "CHANNELS": "channels", "CATEGORIES": "categories"}


class ProfileError(RuntimeError):
    pass


def load(allow_example: bool = False) -> dict:
    path = PROFILE if PROFILE.exists() else (EXAMPLE if allow_example else None)
    if path is None:
        raise ProfileError("config/data_profile.json is missing. Copy config/data_profile.example.json to config/data_profile.json and describe your data space "
                           "(date coverage, zones, channels, categories, planning facts).")
    data = json.loads(path.read_text(encoding="utf-8"))
    unfilled = [k for k, v in data.items() if not k.startswith("_") and "<" in json.dumps(v)]
    if unfilled and not allow_example:
        raise ProfileError(f"config/data_profile.json still has placeholders (<...>) in: {', '.join(unfilled)}. Replace them with your values.")
    return data


def render(text: str, profile: dict) -> str:
    """Fills {{COVERAGE}}, {{FISCAL_YEAR}}, {{ZONES}}, {{CHANNELS}}, {{CATEGORIES}} and {{DATA_FACTS}} in a prompt."""
    for token, key in PLACEHOLDERS.items():
        text = text.replace("{{" + token + "}}", str(profile[key]))
    text = text.replace("{{DATA_FACTS}}", "\n".join(f"- {f}" for f in profile["facts"]))
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise ProfileError(f"Unresolved placeholders in a prompt: {sorted(set(left))}")
    return text
