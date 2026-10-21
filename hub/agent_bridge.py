"""Agent bridge: lets one Foundry agent call another as a tool (MCP), so the post agents can ask the Data agent and the CX agent can hand
off to the post agents. Which agent may call which is set by each agent's allowed_tools in agents/definitions.py, not here."""
from __future__ import annotations

import asyncio
import os
import time
import uuid

from mcp.server.fastmcp import FastMCP

import case_context
import harness
from events import emit, infer_caller
from foundry_runs import invoke as _invoke
from foundry_runs import trace  # recent agent-to-agent calls, shown by /trace
from harness import runtime as harness_runtime
from harness import workers as harness_workers
from settings import AGENT_CALL_TIMEOUT_S, HARNESS_MODE, REPORT_TEAM

mcp = FastMCP("agent-bridge", host="0.0.0.0", stateless_http=True, json_response=True)


def _failure_text(agent: str, exc: Exception) -> str:
    """What the calling agent is told. Out of model capacity is transient and says nothing about the question, so "try a smaller question" would
    make the caller drop part of the work; the harness relies on the ERROR prefix to see a failed call."""
    if "rate limit" in str(exc).lower():
        return (f"ERROR: the {agent} agent is temporarily out of model capacity (token rate limit). The question is fine: "
                "ask the same thing again in a minute.")
    return f"ERROR: the {agent} agent could not answer ({type(exc).__name__}). Try a smaller question or continue without it."


async def _call(agent: str, text: str, case_id: str) -> str:
    started = time.time()
    entry = {"at": time.strftime("%H:%M:%S"), "agent": agent, "case_id": case_id, "ask": text[:300]}
    call_id = uuid.uuid4().hex[:8]
    caller = "cx" if agent in ("lookup", "research", "analysis", "reporting") else ("reporting" if agent in REPORT_TEAM else infer_caller(case_id))
    emit("agent_start", id=call_id, agent=agent, caller=caller, case_id=case_id, ask=text[:600])
    answer, ok, usage = "", False, {}
    try:
        if agent == "data":
            async with _data_slots:
                answer, usage = await asyncio.wait_for(asyncio.to_thread(_invoke, agent, text), AGENT_CALL_TIMEOUT_S)
        else:
            answer, usage = await asyncio.wait_for(asyncio.to_thread(_invoke, agent, text), AGENT_CALL_TIMEOUT_S)
        ok = True
        entry.update(ok=True, answer=answer[:300], **usage)
        return answer
    except Exception as exc:
        entry.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        answer = _failure_text(agent, exc)
        return answer
    finally:
        entry["seconds"] = round(time.time() - started, 1)
        trace.append(entry)
        emit("agent_end", id=call_id, agent=agent, caller=caller, case_id=case_id, ok=ok, seconds=entry["seconds"], answer=answer[:800], **usage)


def _with_case(text: str, case_id: str) -> str:
    return f"[case_id: {case_id}]\n{text}" if case_id else text


# Foundry cuts any single MCP tool call off after 100 seconds, and a specialist with real Genie behind it can take several minutes.
# So a call runs as a background job: the tool waits up to WAIT_SECONDS and, if the specialist is still working, hands back a job id
# that the caller keeps waiting on with wait_for_agent. The job itself is never cancelled by the call that started it.
WAIT_SECONDS = 80
_jobs: dict[str, tuple[asyncio.Task, str]] = {}
_data_slots = asyncio.Semaphore(int(os.getenv("DATA_PARALLELISM", "4")))  # keeps a burst of questions from swamping the SQL warehouse


async def _wait(job_id: str) -> str:
    task, agent = _jobs[job_id]
    done, _pending = await asyncio.wait({task}, timeout=WAIT_SECONDS)
    if task in done:
        _jobs.pop(job_id, None)
        return task.result()
    return (f"STILL RUNNING: the {agent} agent has not finished yet (job {job_id}). Call wait_for_agent with job_id \"{job_id}\" to keep waiting. "
            "Do not send the same request again.")


async def _submit(agent: str, text: str, case_id: str) -> str:
    job_id = "J-" + uuid.uuid4().hex[:8]
    _jobs[job_id] = (asyncio.create_task(_call(agent, text, case_id)), agent)
    return await _wait(job_id)


def _harness_deps() -> harness_runtime.Deps:
    async def ask_data(question: str, case_id: str) -> str:
        return await _call("data", _with_case(question, case_id), case_id)

    return harness_runtime.Deps(ask_data=ask_data, worker=harness_workers.call_worker, save_finding=lambda *a: case_context.save_finding(*a), get_case=case_context.snapshot)


async def _run_harness(name: str, brief: str, case_id: str) -> str:
    """Runs the Analysis or Research harness as one agent call, so the admin view and the trace show it like any other specialist."""
    started = time.time()
    entry = {"at": time.strftime("%H:%M:%S"), "agent": name, "case_id": case_id, "ask": brief[:300]}
    call_id = uuid.uuid4().hex[:8]
    emit("agent_start", id=call_id, agent=name, caller="cx", case_id=case_id, ask=brief[:600])
    answer, ok = "", False
    try:
        run = await asyncio.wait_for(harness.execute(name, brief, case_id, _harness_deps()), AGENT_CALL_TIMEOUT_S)
        if run.status == "done" and run.result:
            answer, ok = run.result["markdown"], True
            entry.update(ok=True, answer=run.result["headline"][:300], run=run.id)
        else:
            entry.update(ok=False, error=run.error[:200], run=run.id)
            answer = f"ERROR: the {name} could not be completed ({run.error}). Try a narrower question, or continue without it."
    except Exception as exc:
        entry.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        answer = f"ERROR: the {name} could not be completed ({type(exc).__name__}). Try a narrower question, or continue without it."
    finally:
        entry["seconds"] = round(time.time() - started, 1)
        trace.append(entry)
        emit("agent_end", id=call_id, agent=name, caller="cx", case_id=case_id, ok=ok, seconds=entry["seconds"], answer=answer[:800])
    return answer


async def _submit_harness(name: str, brief: str, case_id: str) -> str:
    job_id = "J-" + uuid.uuid4().hex[:8]
    _jobs[job_id] = (asyncio.create_task(_run_harness(name, brief, case_id)), name)
    return await _wait(job_id)


@mcp.tool()
async def wait_for_agent(job_id: str) -> str:
    """Keep waiting for a specialist agent that an earlier call reported as STILL RUNNING. Returns its answer, or STILL RUNNING again
    (then call this again with the same job_id)."""
    if job_id not in _jobs:
        return f"ERROR: unknown job {job_id}. It may already have been collected; check the case with get_case instead."
    return await _wait(job_id)


@mcp.tool()
async def ask_data_batch(questions: list[str], case_id: str = "") -> str:
    """Ask the Data agent several INDEPENDENT questions at once. They run in parallel, which is far faster than asking one by one. Each question must be
    self-contained (exact dates, scope, measures, grouping, column names). Up to 12 per call. Returns one answer per question, each carrying its result id;
    any that are not finished after about 80 seconds come back as STILL RUNNING with a job id: collect those with wait_for_agent."""
    questions = [q for q in questions if q and q.strip()][:12]
    if not questions:
        return "ERROR: no questions given."
    jobs = []
    for q in questions:
        job_id = "J-" + uuid.uuid4().hex[:8]
        _jobs[job_id] = (asyncio.create_task(_call("data", _with_case(q, case_id), case_id)), "data")
        jobs.append((q, job_id))
    await asyncio.wait({_jobs[j][0] for _q, j in jobs}, timeout=WAIT_SECONDS)
    out = []
    for i, (q, job_id) in enumerate(jobs, 1):
        task = _jobs[job_id][0]
        if task.done():
            _jobs.pop(job_id, None)
            out.append(f"### Q{i}: {q[:160]}\n{task.result()}")
        else:
            out.append(f"### Q{i}: {q[:160]}\nSTILL RUNNING (job {job_id}). Call wait_for_agent with job_id \"{job_id}\" to collect it.")
    return "\n\n".join(out)


@mcp.tool()
async def ask_data_agent(question: str, case_id: str = "") -> str:
    """Ask the Data agent for data. It queries Croma's governed data (sales, stock, prices, costs, schemes, margins, offers, pricing plans) and
    returns the figures with their source (table, period, filters) and caveats. Ask one focused question at a time, with the period, scope
    and measure spelled out. Independent questions can be asked in parallel. If the answer is STILL RUNNING, call wait_for_agent."""
    return await _submit("data", _with_case(question, case_id), case_id)


@mcp.tool()
async def ask_lookup_agent(question: str, case_id: str = "") -> str:
    """Hand a plain data lookup to the Lookup agent: one question that a single query can answer, for example 'how many sales did we do in March' or
    'NMV by zone last month'. It fetches the figure through the Data agent and returns it with the basis it used (period, scope, definitions).
    It answers NEEDS ANALYSIS when the question needs explaining or several steps. If the answer is STILL RUNNING, call wait_for_agent."""
    return await _submit("lookup", _with_case(question, case_id), case_id)


@mcp.tool()
async def ask_research_agent(brief: str, case_id: str) -> str:
    """Hand a 'why did this happen' question to Research. It turns the question into hypotheses, tests each one against figures it computes exactly from the
    Data agent's results, ranks what the data supports, rules out what it does not, and has the conclusion challenged before it comes back. Returns the finished
    research brief (a verdict for every explanation, the ranked causes, what is still unknown) with its evidence ids, and saves the findings on the case. Give it
    the question, scope, period, comparison basis and the agreed interpretation. This takes several minutes: if the answer is STILL RUNNING, call wait_for_agent
    until it returns."""
    if HARNESS_MODE == "agent":
        return await _submit("research", _with_case(brief, case_id), case_id)
    return await _submit_harness("research", brief, case_id)


@mcp.tool()
async def ask_analysis_agent(brief: str, case_id: str) -> str:
    """Hand a business KPI to Analysis. It plans the analysis, pulls the data through the Data agent, computes what moved and why with exact methods (change by
    segment, rate against mix, price against volume against mix, target gaps), checks every figure against the data, and has the result challenged before it comes
    back. Returns the finished analysis brief (headline, driver table, ranked drivers, what was checked and is not a driver, caveats) with its evidence ids, and saves
    the findings on the case. Give it the KPI, scope, period, comparison basis and the agreed interpretation. This takes several minutes: if the answer is STILL
    RUNNING, call wait_for_agent until it returns."""
    if HARNESS_MODE == "agent":
        return await _submit("analysis", _with_case(brief, case_id), case_id)
    return await _submit_harness("analysis", brief, case_id)


@mcp.tool()
async def ask_report_specialist(role: str, brief: str, case_id: str) -> str:
    """Editor only: hand work to a member of the report team. role is one of: planner (designs the report blueprint), insight (finds and ranks the drivers
    and anomalies, with deep reasoning), strategist (turns insights into prescriptive, sized actions), writer (writes report sections from the evidence),
    reviewer (independent critique). Each reads and writes the shared report workspace for the case, and returns a short status. Independent briefs
    (for example several writers on different sections) can run in parallel. If the answer is STILL RUNNING, call wait_for_agent."""
    role = role.strip().lower()
    if role not in REPORT_TEAM:
        return f"ERROR: unknown role '{role}'. Use one of: {', '.join(REPORT_TEAM)}."
    return await _submit(role, _with_case(brief, case_id), case_id)


@mcp.tool()
async def ask_reporting_agent(brief: str, case_id: str) -> str:
    """Ask the Reporting agent (the editor of the report team) for a business report on a case. It plans the report, gathers any missing evidence through the
    Data agent, has the team find the drivers and actions, writes and audits the document, and publishes a designed DOCX with charts and an evidence
    appendix. It returns a short summary and the report link, and attaches the report to the case. A full report takes several minutes: if the answer is
    STILL RUNNING, call wait_for_agent until it returns. Give the brief the audience, the period, what decision it supports, and the depth wanted."""
    return await _submit("reporting", _with_case(brief, case_id), case_id)
