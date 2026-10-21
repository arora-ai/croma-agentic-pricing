"""Document spec: the contract between the Writer agent and the renderers.

Tables and charts reference ledger results by id; the renderer pulls the rows itself, so the model never types a table.
Narrative text may quote numbers, and the audit checks each one against the ledger.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, Field, ValidationError

from studio import cites

Fmt = Literal["text", "int", "num1", "num2", "pct", "ratio_pct", "spct", "pp", "cr", "cr2", "inr", "x", "date"]
Tone = Literal["good", "bad", "neutral", "warn"]


def short(n: int):
    """A text field limited to n characters, counted without the evidence ids, so the writer is pushed to be concise."""
    def check(v: str) -> str:
        length = len(cites.plain(v))
        if length > n:
            raise ValueError(f"is {length} characters without evidence ids; the limit is {n}. Cut it: one idea, no repeated caveats")
        return v
    return AfterValidator(check)


class Paragraph(BaseModel):
    type: Literal["paragraph"]
    text: Annotated[str, short(750)]


class Bullets(BaseModel):
    type: Literal["bullets"]
    items: list[Annotated[str, short(280)]] = Field(min_length=1)


class Tile(BaseModel):
    value: str
    label: str
    sub: Annotated[str, short(80)] = ""
    delta: Annotated[str, short(40)] = ""
    tone: Tone = "neutral"


class KpiTiles(BaseModel):
    type: Literal["kpi_tiles"]
    heading: str = ""
    tiles: list[Tile] = Field(min_length=1, max_length=5)


class Column(BaseModel):
    key: str
    label: str = ""
    format: Fmt = "text"
    align: Literal["auto", "left", "right", "center"] = "auto"
    heat: Literal["none", "achievement", "growth", "delta"] = "none"


class Table(BaseModel):
    type: Literal["table"]
    title: str = ""
    result_id: str
    columns: list[Column] = Field(min_length=1)
    top_n: int = 0
    total_row: bool = False
    sum_columns: list[str] = []
    total_label: str = "Total"
    note: str = ""


class Series(BaseModel):
    col: str
    label: str = ""
    color: str = ""


class Chart(BaseModel):
    type: Literal["chart"]
    kind: Literal["bar", "hbar", "line", "stacked", "waterfall"]
    title: str
    result_id: str
    x: str
    series: list[Series] = Field(min_length=1)
    top_n: int = 0
    value_format: Fmt = "num1"
    y_label: str = ""
    start_ref: str = ""  # waterfall: cell ref or D-id for the starting bar
    start_label: str = ""
    end_label: str = ""
    caption: str = ""


class Callout(BaseModel):
    type: Literal["callout"]
    tone: Literal["insight", "risk", "action", "note"] = "insight"
    title: Annotated[str, short(90)] = ""
    text: Annotated[str, short(520)]


class Action(BaseModel):
    priority: Literal["P1", "P2", "P3"]
    action: Annotated[str, short(300)]
    where: Annotated[str, short(160)] = ""
    owner: Annotated[str, short(110)]
    impact: Annotated[str, short(240)] = ""  # the size of the prize and the assumption behind it, in one or two sentences
    guardrail: Annotated[str, short(220)] = ""  # the risk to avoid or the condition to respect
    evidence: list[str] = []
    confidence: Literal["high", "medium", "low"] = "medium"
    kpi: Annotated[str, short(110)] = ""
    timing: Annotated[str, short(60)] = ""


class Actions(BaseModel):
    type: Literal["actions"]
    title: str = ""
    items: list[Action] = Field(min_length=1)


class Term(BaseModel):
    term: str
    definition: str


class Definitions(BaseModel):
    type: Literal["definitions"]
    items: list[Term] = Field(min_length=1)


class PageBreak(BaseModel):
    type: Literal["page_break"]


Block = Annotated[Union[Paragraph, Bullets, KpiTiles, Table, Chart, Callout, Actions, Definitions, PageBreak], Field(discriminator="type")]


class Section(BaseModel):
    id: str
    title: Annotated[str, short(90)]
    takeaway: Annotated[str, short(230)] = ""  # one sentence that says what the section means, shown right under the heading
    blocks: list[Block] = Field(min_length=1)


class Frame(BaseModel):
    title: Annotated[str, short(100)]
    subtitle: Annotated[str, short(160)] = ""
    report_type: Literal["weekly_performance", "category_deep_dive", "decision_brief", "ad_hoc"] = "ad_hoc"
    audience: str = ""
    period_label: Annotated[str, short(120)] = ""
    comparison_label: Annotated[str, short(140)] = ""
    scope: Annotated[str, short(200)] = ""
    measure: Annotated[str, short(200)] = ""
    data_as_of: str = ""
    headline: Annotated[str, short(300)]  # the answer in one or two sentences
    bottom_line: list[Annotated[str, short(270)]] = Field(default=[], max_length=6)  # the 3 to 5 things the reader must take away
    decisions: list[Annotated[str, short(330)]] = []  # decisions or asks for the reader
    section_order: list[str] = []
    caveats: list[Annotated[str, short(260)]] = []


def validate_frame(data: dict) -> tuple[Frame | None, str]:
    try:
        return Frame.model_validate(data), ""
    except ValidationError as exc:
        return None, _explain(exc)


def validate_section(data: dict) -> tuple[Section | None, str]:
    try:
        return Section.model_validate(data), ""
    except ValidationError as exc:
        return None, _explain(exc)


def _explain(exc: ValidationError) -> str:
    lines = []
    for e in exc.errors()[:12]:
        loc = ".".join(str(p) for p in e["loc"])
        lines.append(f"{loc}: {e['msg']}")
    return "Invalid spec. " + " | ".join(lines)
