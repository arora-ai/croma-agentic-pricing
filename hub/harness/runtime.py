"""The harness runtime. A run is an ordered list of stages that code owns: what happens next, how often something is retried, what is checked and when
to stop are decided here, never by a model. Language-model workers are called inside stages and return typed replies.

Every stage saves its output on the run, so a run that is interrupted (a redeploy, a crash) resumes at the stage it reached and a finished run can be
inspected stage by stage. Runs travel with the case when it is exported and restored (GET /state/<case>, POST /state)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from events import emit

RESUME_WINDOW_S = 6 * 3600


@dataclass
class Stage:
    name: str
    status: str = "pending"  # pending | running | done | failed | interrupted
    started: float | None = None
    ended: float | None = None
    summary: str = ""
    error: str = ""


@dataclass
class Run:
    id: str
    harness: str
    case_id: str
    brief: str
    status: str = "running"  # running | done | failed
    stages: list[Stage] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)  # stage name -> the output that stage saved
    result: dict | None = None
    error: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def stage(self, name: str) -> Stage:
        for s in self.stages:
            if s.name == name:
                return s
        self.stages.append(Stage(name))
        return self.stages[-1]

    @property
    def brief_hash(self) -> str:
        return hashlib.sha1(" ".join(self.brief.lower().split()).encode()).hexdigest()[:12]

    def to_dict(self, with_data: bool = True) -> dict:
        out = {"id": self.id, "harness": self.harness, "case_id": self.case_id, "brief": self.brief, "status": self.status, "result": self.result, "error": self.error,
               "created": self.created, "updated": self.updated, "stages": [s.__dict__ for s in self.stages]}
        if with_data:
            out["data"] = self.data
        return json.loads(json.dumps(out, default=str))

    @classmethod
    def from_dict(cls, d: dict) -> "Run":
        return cls(id=d["id"], harness=d["harness"], case_id=d["case_id"], brief=d.get("brief", ""), status=d.get("status", "failed"), stages=[Stage(**s) for s in d.get("stages", [])],
                   data=d.get("data", {}), result=d.get("result"), error=d.get("error", ""), created=d.get("created", time.time()), updated=d.get("updated", time.time()))


_runs: dict[str, Run] = {}
_live: set[str] = set()  # runs executing in this process right now
_lock = threading.RLock()


def new_run(harness: str, case_id: str, brief: str) -> Run:
    prefix = {"analysis": "AN", "research": "RS"}.get(harness, "RN")
    run = Run(id=f"{prefix}-{uuid.uuid4().hex[:8]}", harness=harness, case_id=case_id, brief=brief)
    with _lock:
        _runs[run.id] = run
    return run


def get(run_id: str) -> Run | None:
    return _runs.get(run_id)


def for_case(case_id: str) -> list[Run]:
    with _lock:
        return sorted((r for r in _runs.values() if r.case_id == case_id), key=lambda r: r.created)


def find_resumable(harness: str, case_id: str, brief: str) -> Run | None:
    """The latest unfinished or failed run of the same harness and brief on this case that is not executing right now."""
    probe = Run(id="", harness=harness, case_id=case_id, brief=brief)
    with _lock:
        for r in sorted(_runs.values(), key=lambda r: r.updated, reverse=True):
            if (r.harness, r.case_id, r.brief_hash) == (harness, case_id, probe.brief_hash) and r.id not in _live and r.status != "done" \
                    and time.time() - r.updated < RESUME_WINDOW_S and any(s.status == "done" for s in r.stages):
                return r
    return None


def dump(case_id: str) -> list[dict]:
    return [r.to_dict() for r in for_case(case_id)]


def load(runs: list[dict]) -> None:
    with _lock:
        for d in runs:
            run = Run.from_dict(d)
            if run.status == "running":
                run.status = "failed"  # it was executing in a process that no longer exists
            _runs[run.id] = run


@dataclass
class Deps:
    """What a pipeline needs from the outside; the hub wires the real ones, tests wire scripted ones."""
    ask_data: Callable[[str, str], Awaitable[str]]  # (question, case_id) -> the Data agent's answer
    worker: Callable[..., Awaitable[Any]]  # call_worker
    save_finding: Callable[[str, str, str, str], None]  # (case_id, agent, title, finding)
    get_case: Callable[[str], dict | None]  # the case as saved: confirmed request and earlier findings


class HarnessError(RuntimeError):
    """A run cannot go on, with a reason that is fit to show the user."""


class Ctx:
    def __init__(self, run: Run, deps: Deps):
        self.run, self.deps = run, deps

    @property
    def case_id(self) -> str:
        return self.run.case_id

    @property
    def harness(self) -> str:
        return self.run.harness

    async def ask_data(self, question: str) -> str:
        return await self.deps.ask_data(question, self.run.case_id)

    async def worker(self, role: str, task: str, payload: dict, schema: type, **kw) -> Any:
        return await self.deps.worker(role, task, payload, schema, case_id=self.run.case_id, parent=self.run.harness, **kw)

    def checkpoint(self, name: str, partial: Any) -> None:
        """Saves progress inside a long stage (for example the answers collected so far) so a resumed run does not repeat them."""
        self.run.data[name] = partial
        self.run.updated = time.time()

    async def step(self, name: str, fn: Callable[[], Awaitable[Any]], summarize: Callable[[Any], str] | None = None) -> Any:
        """Runs a stage unless it already finished (a resumed run) and saves its output. The output must be plain data (dicts, lists, numbers, text)."""
        stage = self.run.stage(name)
        if stage.status == "done":
            return self.run.data.get(name)
        stage.status, stage.started, stage.ended, stage.error = "running", time.time(), None, ""
        call_id = uuid.uuid4().hex[:8]
        emit("stage_start", id=call_id, harness=self.harness, run_id=self.run.id, stage=name, case_id=self.case_id)
        try:
            out = await fn()
        except asyncio.CancelledError:
            stage.status = "interrupted"
            emit("stage_end", id=call_id, harness=self.harness, run_id=self.run.id, stage=name, case_id=self.case_id, ok=False, seconds=round(time.time() - stage.started, 1), summary="interrupted")
            raise
        except Exception as exc:
            stage.status, stage.ended, stage.error = "failed", time.time(), f"{type(exc).__name__}: {exc}"[:400]
            emit("stage_end", id=call_id, harness=self.harness, run_id=self.run.id, stage=name, case_id=self.case_id, ok=False, seconds=round(stage.ended - stage.started, 1), summary=stage.error[:160])
            raise
        stage.ended = time.time()
        self.run.data[name] = json.loads(json.dumps(out, default=str))
        stage.status, stage.summary = "done", (summarize(out) if summarize else "")[:200]
        self.run.updated = stage.ended
        emit("stage_end", id=call_id, harness=self.harness, run_id=self.run.id, stage=name, case_id=self.case_id, ok=True, seconds=round(stage.ended - stage.started, 1), summary=stage.summary)
        return self.run.data[name]


async def execute(harness: str, brief: str, case_id: str, pipeline: Callable[[Ctx], Awaitable[dict]], deps: Deps) -> Run:
    """Runs (or resumes) a harness on a case. Returns the run; run.result holds the output, run.error the reason when it failed."""
    run = find_resumable(harness, case_id, brief)
    if run:
        run.status, run.error = "running", ""
    else:
        run = new_run(harness, case_id, brief)
    with _lock:
        _live.add(run.id)
    ctx = Ctx(run, deps)
    try:
        run.result = await pipeline(ctx)
        run.status = "done"
    except asyncio.CancelledError:
        run.status, run.error = "failed", "interrupted"
        raise
    except Exception as exc:
        run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:600]
    finally:
        run.updated = time.time()
        with _lock:
            _live.discard(run.id)
    return run
