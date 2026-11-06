"""The data profile: prompts render from config/data_profile.json, and unfilled or missing profiles are refused with a clear message."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from agents import data_profile  # noqa: E402


def test_example_profile_is_refused_until_filled_in(monkeypatch, tmp_path):
    monkeypatch.setattr(data_profile, "PROFILE", tmp_path / "data_profile.json")
    with pytest.raises(data_profile.ProfileError, match="missing"):
        data_profile.load()
    (tmp_path / "data_profile.json").write_text((ROOT / "config" / "data_profile.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(data_profile.ProfileError, match="placeholders"):
        data_profile.load()


def test_every_prompt_renders_from_a_filled_profile(monkeypatch, tmp_path):
    profile = {"coverage": "from 2024-04-01 to 2026-03-31", "fiscal_year": "April to March", "zones": "North, South", "channels": "Stores or Online",
               "categories": "Laptops, TVs", "facts": ["Targets cover Stores only.", "A week is Monday to Sunday."]}
    path = tmp_path / "data_profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(data_profile, "PROFILE", path)
    from agents.definitions import build

    agents = build("https://hub.example")
    assert len(agents) == 15
    cx = agents["cx"].instructions
    assert "from 2024-04-01 to 2026-03-31" in cx and "North, South" in cx and "{{" not in cx
    assert "- Targets cover Stores only." in agents["planner"].instructions
    assert all("{{" not in d.instructions for d in agents.values())


def test_unresolved_placeholder_is_an_error():
    profile = {"coverage": "c", "fiscal_year": "f", "zones": "z", "channels": "ch", "categories": "cat", "facts": ["x"]}
    with pytest.raises(data_profile.ProfileError, match="Unresolved"):
        data_profile.render("hello {{SOMETHING_NEW}}", profile)
