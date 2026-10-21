"""The typed contracts between the harness code and its language-model workers.

A worker's reply is JSON; it is parsed into these models, and anything that does not fit comes back to the worker with the exact problem so it can
correct itself once. The harness code never reads free text from a worker."""
from __future__ import annotations

import ast
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from studio import analytics

Confidence = Literal["high", "medium", "low"]
ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")
RULE_FUNCTIONS = {"abs", "round", "min", "max", "sqrt", "sum", "log"}


class Capped(BaseModel):
    """Lists a worker may over-fill are cut to size rather than failing the whole reply (CAPS: field name -> most items kept)."""
    CAPS: ClassVar[dict[str, int]] = {}

    @model_validator(mode="before")
    @classmethod
    def _cap(cls, data: Any) -> Any:
        if isinstance(data, dict) and cls.CAPS:
            data = dict(data)
            for name, n in cls.CAPS.items():
                if isinstance(data.get(name), list):
                    data[name] = data[name][:n]
        return data


# ---------------------------------------------------------------- plan

class Question(BaseModel):
    id: str
    purpose: str = ""
    ask: str = Field(min_length=12)  # self-contained: exact dates, scope, measure, grouping and the column names wanted
    columns: list[str] = Field(default_factory=list)  # the columns the steps will read; the Data agent is asked to use these names

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID.match(v):
            raise ValueError(f"id '{v}' must be a short name such as q1")
        return v


class Step(BaseModel):
    id: str
    label: str
    op: str
    source: str = ""  # the id of the question whose result this reads (not needed for reconcile and materiality)
    params: dict[str, Any] = Field(default_factory=dict)
    unit: str = ""  # how this step's value figures are shown (for example "\u20b9 Cr" or "units"); blank means the plan's unit

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID.match(v):
            raise ValueError(f"id '{v}' must be a short name such as s1")
        return v

    @model_validator(mode="after")
    def _op(self) -> "Step":
        spec = analytics.OPS.get(self.op)
        if spec is None:
            raise ValueError(f"step {self.id}: unknown op '{self.op}'. Available: {', '.join(analytics.OPS)}")
        try:
            analytics.validate(self.op, self.params)
        except analytics.AnalyticsError as exc:
            raise ValueError(f"step {self.id}: {exc}")
        if not spec.scalar and not self.source:
            raise ValueError(f"step {self.id}: {self.op} needs a source (the id of a question)")
        return self


class Materiality(BaseModel):
    min_share: float = Field(0.05, ge=0, le=1)  # a segment is material when it carries at least this share of the gross movement
    abs_floor: float = Field(0.0, ge=0)  # and at least this much in the unit of the figures


class Plan(BaseModel):
    restated: str = Field(min_length=10)
    kpi: str
    unit: str = "\u20b9 Cr"
    current_label: str
    base_label: str
    basis: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
    materiality: Materiality = Field(default_factory=Materiality)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _wiring(self) -> "Plan":
        qids = [q.id for q in self.questions]
        sids = [s.id for s in self.steps]
        if len(set(qids)) != len(qids) or len(set(sids)) != len(sids):
            raise ValueError("question ids and step ids must each be unique")
        bad = [f"{s.id} reads '{s.source}'" for s in self.steps if s.source and s.source not in qids]
        if bad:
            raise ValueError(f"steps must read a question id ({', '.join(qids)}): {'; '.join(bad)}")
        return self


class AnalysisPlan(Plan):
    pass


def check_rule(rule: str, step: Step) -> None:
    """A hypothesis rule is an expression over the outputs of its step, for example 'top3_share_of_gross_pct >= 50 and rate_share_of_gross_pct > 60'."""
    allowed = set(analytics.OPS[step.op].outputs)
    try:
        tree = ast.parse(rule.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"rule '{rule}' is not a valid expression ({exc.msg}). Use comparisons joined with 'and' / 'or'")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - RULE_FUNCTIONS
    unknown = sorted(names - allowed)
    if unknown:
        raise ValueError(f"rule '{rule}' uses {unknown}, which are not outputs of {step.op}. Outputs: {', '.join(sorted(allowed))}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) > 1:
            raise ValueError(f"rule '{rule}' chains comparisons; write 'a >= 1 and a <= 5' instead")


class Hypothesis(BaseModel):
    id: str
    claim: str = Field(min_length=8)
    mechanism: str = ""  # why it would move the KPI
    expected: str = ""  # what the data would show if it were true, in words
    step: str  # the step whose outputs test it
    supports_if: str  # rule over that step's outputs
    refutes_if: str = ""  # optional rule that rules the hypothesis out


class ResearchPlan(Plan, Capped):
    CAPS = {"hypotheses": 5}
    sizing_step: str  # the step that measures the movement to be explained
    hypotheses: list[Hypothesis] = Field(min_length=1)

    @model_validator(mode="after")
    def _hypotheses(self) -> "ResearchPlan":
        steps = {s.id: s for s in self.steps}
        if self.sizing_step not in steps:
            raise ValueError(f"sizing_step '{self.sizing_step}' is not a step id ({', '.join(steps)})")
        ids = [h.id for h in self.hypotheses]
        if len(set(ids)) != len(ids):
            raise ValueError("hypothesis ids must be unique")
        for h in self.hypotheses:
            if h.step not in steps:
                raise ValueError(f"hypothesis {h.id} tests step '{h.step}', which is not a step id ({', '.join(steps)})")
            if steps[h.step].op in ("reconcile", "materiality"):
                raise ValueError(f"hypothesis {h.id}: test it with a decomposition, concentration, trend or target step, not {steps[h.step].op}")
            check_rule(h.supports_if, steps[h.step])
            if h.refutes_if.strip():
                check_rule(h.refutes_if, steps[h.step])
        return self


class FollowUp(Capped):
    """A second, bounded round: what to ask next and how to compute it."""
    CAPS = {"questions": 3, "steps": 4}
    why: str = Field(min_length=8)
    questions: list[Question] = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def _wiring(self) -> "FollowUp":
        qids = {q.id for q in self.questions}
        bad = [f"{s.id} reads '{s.source}'" for s in self.steps if s.source and s.source not in qids]
        if bad:
            raise ValueError(f"follow-up steps must read the follow-up question ids ({', '.join(sorted(qids))}): {'; '.join(bad)}")
        return self


class FixedSteps(BaseModel):
    steps: list[Step] = Field(min_length=1)


# ---------------------------------------------------------------- analysis findings

class Driver(BaseModel):
    headline: str = Field(min_length=8)  # the claim, with its figures quoted from the evidence
    evidence: list[str] = Field(min_length=1)  # T / D / R ids it rests on
    why: str = ""  # the reading of the numbers (a mechanism); says so when it is an inference
    confidence: Confidence = "medium"
    inference: bool = False  # true when 'why' goes beyond what the numbers show


class AnalysisFindings(Capped):
    CAPS = {"drivers": 5, "not_drivers": 5, "basis": 5, "caveats": 5, "gaps": 4, "next_questions": 3}
    headline: str = Field(min_length=8)
    drivers: list[Driver] = Field(default_factory=list)
    not_drivers: list[str] = Field(default_factory=list)  # checked and immaterial, so nobody wonders
    basis: list[str] = Field(default_factory=list)  # scope and definitions as the data space applied them
    caveats: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)  # asked for but could not be obtained or computed
    next_questions: list[str] = Field(default_factory=list)


class AnalystTurn(BaseModel):
    """Either the findings, or (in the first round only) a follow-up the analyst needs before it can conclude."""
    findings: AnalysisFindings | None = None
    follow_up: FollowUp | None = None

    @model_validator(mode="after")
    def _one(self) -> "AnalystTurn":
        if (self.findings is None) == (self.follow_up is None):
            raise ValueError("return exactly one of 'findings' or 'follow_up'")
        return self


# ---------------------------------------------------------------- research conclusion

class Cause(BaseModel):
    claim: str = Field(min_length=8)
    size: str = ""  # how much of the movement it explains, quoted from the evidence
    evidence: list[str] = Field(min_length=1)
    confidence: Confidence = "medium"
    inference: bool = False


class ResearchConclusion(Capped):
    CAPS = {"causes": 5, "ruled_out": 6, "unknowns": 5, "caveats": 5, "next_steps": 4}
    headline: str = Field(min_length=8)
    causes: list[Cause] = Field(default_factory=list)  # ranked, most important first
    ruled_out: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class ResearchTurn(BaseModel):
    conclusion: ResearchConclusion | None = None
    follow_up: FollowUp | None = None

    @model_validator(mode="after")
    def _one(self) -> "ResearchTurn":
        if (self.conclusion is None) == (self.follow_up is None):
            raise ValueError("return exactly one of 'conclusion' or 'follow_up'")
        return self


# ---------------------------------------------------------------- critique

class Issue(BaseModel):
    severity: Literal["blocking", "major", "minor"]
    where: str
    problem: str
    fix: str = ""


class Critique(Capped):
    CAPS = {"issues": 8, "missing_checks": 4}
    verdict: Literal["accept", "revise"]
    issues: list[Issue] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)  # what was not looked at that a careful reader would want to know
    follow_up: FollowUp | None = None  # research only: one data question that would settle a doubt

    @model_validator(mode="after")
    def _consistent(self) -> "Critique":
        if self.verdict == "accept" and any(i.severity in ("blocking", "major") for i in self.issues):
            raise ValueError("verdict 'accept' cannot carry blocking or major issues; use 'revise'")
        return self

    @property
    def must_fix(self) -> list[Issue]:
        return [i for i in self.issues if i.severity in ("blocking", "major")]
