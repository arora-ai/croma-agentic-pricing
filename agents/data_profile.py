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


def sharepoint_profile(profile: dict) -> dict | None:
    """The optional 'sharepoint' block of the data profile when SharePoint is switched on for the agents, else None."""
    sp = profile.get("sharepoint")
    return sp if isinstance(sp, dict) and sp.get("enabled") else None


def library_text(profile: dict) -> str:
    """What the SharePoint agent is told the library holds (its prompt's {{SHAREPOINT_LIBRARY}})."""
    sp = sharepoint_profile(profile)
    if not sp:
        return "- SharePoint is not described in config/data_profile.json: search the library and say what you find."
    lines = [f"- {sp.get('about', 'A SharePoint document library.')}"]
    for f in sp.get("files", []):
        parts = [f.get("holds", "").rstrip(". ") + "."]
        if f.get("grain"):
            parts.append(f["grain"].rstrip(". ") + ".")
        if f.get("columns"):
            parts.append(f"Columns: {', '.join(f['columns'])}.")
        lines.append(f"- {f['name']}: {' '.join(parts)}")
    lines += [f"- {r}" for r in sp.get("rules", [])]
    return "\n".join(lines)


SOURCES_OFF = ("Only one data source is connected: the data space described above. Every question uses it, and \"source\" stays at its default, \"databricks\" "
               "(you may leave the field out).")

SOURCES_ON = """Two data sources are connected. Each question names the one that answers it in "source" (default "databricks"):
- "databricks": the Genie data space described above: Croma's sales, margin, markdown, stock and target tables. Use it for every measure it holds. It can aggregate (sums, groupings, ratios): that is what SQL is for.
- "sharepoint": Croma's SharePoint document library, read by the SharePoint agent: {about} Use it for what the data space does not hold and only for that. Never ask SharePoint for a figure the data space has.
How to plan with SharePoint
- THE SHAREPOINT AGENT ONLY READS. It returns the rows of a file (optionally filtered by simple conditions such as a category, a competitor or a date range) or the passages of a document. It never calculates: do not ask it for averages, weighted means, totals, counts, shares or anything "per month" or "per zone" that is not already a row of the file. Ask for the ROWS you need (one row per week and category, one row per store change ...) and summarise them with an "aggregate" step, which does the arithmetic exactly. Asking SharePoint for a computed figure returns nothing usable.
- A SharePoint question has "form": "table" (the default: rows of a spreadsheet or CSV, which steps can compute on) or "text" (passages of a document, such as a policy or a note, which the analyst reads; no step may read a text question). Ask for text only when the brief needs what a document says.
- Name the file or the kind of file, the sheet if you know it, the period, and the scope, in words ("the festive promotion calendar, for 2026-08-03 to 2026-09-27, for Laptops"). The SharePoint agent finds the file itself, so never invent a file name: use the ones listed below, or describe the document.
- List the columns you expect in "columns" (lower_snake_case, from the file list below). The file keeps its own headings; the harness maps your names onto them afterwards.
- To compute on both sources together, add a "join" step: "source" is the question that holds the measure (usually the databricks one), "params": {{"on": the key column in it, "with": the other question's id (or an aggregate step's id), "take": [the other result's columns to add]}} and, when the key is named differently there, "right_on". The harness puts the two results side by side; later steps read the join by its step id (for example "source": "s1"). The keys must be written the same way in both (weeks as the Monday date such as 2026-09-07, categories exactly as the data space writes them), and the other side must have ONE ROW PER KEY.
- To get one row per key from a file's rows, add an "aggregate" step on the SharePoint question: "key" is the column to group by and "measures" an object of output name to "function:column where condition" (functions count, sum, mean, min, max; conditions joined with ";"). One aggregate can make several columns, each over its own rows, for example the mean planned discount in each of two months. A key that has no matching row still gets a row (a count of 0).
- A join or aggregate needs no figures of its own, so no hypothesis may be tested on it: test hypotheses on the step that reads it.
- Think about whether the brief needs SharePoint at all. A question about sales, margin or markdown alone does not: leave SharePoint out of the plan. Add it when the brief mentions a plan, calendar, price list, policy or document, or when an explanation (a promotion, a competitor price move, a policy change) can only be tested with what the library holds.
- A step on a column that is not in the plan's unit (a discount depth in %, a price gap in %) needs its own "unit" ("%"), so its figures are not shown in the plan's unit (for example ₹ Cr).
- The two sources can disagree on basis (a file's period or scope differs from the data space's). State the basis of each in "basis" and in "assumptions" (for example "weeks that start in the month").
What the library holds
{library}
Example 1: weekly sales beside the weekly plan (the SharePoint question is filtered to one row per week, so it joins directly):
```json
"questions": [
  {{"id": "q1", "purpose": "Weekly NMV for Laptops", "ask": "For Stores only, for Laptops: NMV in ₹ Crore for each week from 2026-08-03 to 2026-09-27, one row per week, with the Monday date of the week as week_start. {{{{STANDING_SCOPE}}}}.", "columns": ["week_start", "nmv"]}},
  {{"id": "q2", "purpose": "Planned promotion per week", "ask": "From the festive promotion calendar: the weekly plan rows for Laptops for each week from 2026-08-03 to 2026-09-27, one row per week_start (the Monday date).", "columns": ["week_start", "campaign", "planned_discount_pct"], "source": "sharepoint", "form": "table"}}
],
"steps": [
  {{"id": "s1", "label": "Weekly NMV with the planned discount", "op": "join", "source": "q1", "params": {{"on": "week_start", "with": "q2", "take": ["campaign", "planned_discount_pct"]}}}},
  {{"id": "s2", "label": "Weekly NMV trend", "op": "trend", "source": "s1", "params": {{"time": "week_start", "value": "nmv"}}}}
]
```
Example 2: monthly sales by category beside the monthly average of a file's weekly rows (the file's rows are aggregated by an aggregate step, then joined):
```json
"questions": [
  {{"id": "q1", "purpose": "Sales by category, both months", "ask": "For Stores only, for every category: units and NMV in ₹ Crore for 2026-09-01 to 2026-09-30 (cur_units, cur_nmv) and for 2026-08-01 to 2026-08-31 (base_units, base_nmv), one row per category. {{{{STANDING_SCOPE}}}}.", "columns": ["category", "cur_units", "cur_nmv", "base_units", "base_nmv"]}},
  {{"id": "q2", "purpose": "Weekly promotion plan rows", "ask": "From the festive promotion calendar: all rows of the weekly plan for the weeks starting 2026-08-03 to 2026-09-28 (week_start, category, planned_discount_pct).", "columns": ["week_start", "category", "planned_discount_pct"], "source": "sharepoint"}}
],
"steps": [
  {{"id": "s1", "label": "Sales change by category", "op": "price_volume_mix", "source": "q1", "params": {{"key": "category", "cur_qty": "cur_units", "cur_value": "cur_nmv", "base_qty": "base_units", "base_value": "base_nmv"}}}},
  {{"id": "s2", "label": "Average planned discount by category and month", "op": "aggregate", "source": "q2", "unit": "%", "params": {{"key": "category", "measures": {{"base_discount_pct": "mean:planned_discount_pct where week_start>=2026-08-03; week_start<=2026-08-31", "cur_discount_pct": "mean:planned_discount_pct where week_start>=2026-09-01; week_start<=2026-09-28"}}}}}},
  {{"id": "s3", "label": "Sales with the planned discounts", "op": "join", "source": "q1", "params": {{"on": "category", "with": "s2", "take": ["base_discount_pct", "cur_discount_pct"]}}}}
]
```"""


def sources_text(profile: dict) -> str:
    """What the Scoper is told about the connected sources (its prompt's {{SOURCES}})."""
    sp = sharepoint_profile(profile)
    if not sp:
        return SOURCES_OFF
    return SOURCES_ON.format(about=sp.get("about", "a document library of plans, calendars, price lists and policies."), library=library_text(profile))


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


def render(text: str, profile: dict, extra: dict[str, str] | None = None) -> str:
    """Fills {{COVERAGE}}, {{FISCAL_YEAR}}, {{ZONES}}, {{CHANNELS}}, {{CATEGORIES}} and {{DATA_FACTS}} in a prompt, plus any placeholders in `extra`
    (for example {{ANALYTICS_CATALOG}}, generated from the analytics the harness can run)."""
    for token, key in PLACEHOLDERS.items():
        text = text.replace("{{" + token + "}}", str(profile[key]))
    text = text.replace("{{DATA_FACTS}}", "\n".join(f"- {f}" for f in profile["facts"]))
    text = text.replace("{{SOURCES}}", sources_text(profile)).replace("{{SHAREPOINT_LIBRARY}}", library_text(profile))
    text = text.replace("{{STANDING_SCOPE}}", str(profile.get("standing_scope") or "the data space's standing filters"))
    for token, value in (extra or {}).items():
        text = text.replace("{{" + token + "}}", value)
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise ProfileError(f"Unresolved placeholders in a prompt: {sorted(set(left))}")
    return text
