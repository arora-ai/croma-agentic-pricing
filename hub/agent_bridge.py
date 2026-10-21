"""Agent bridge: lets one Foundry agent call another as a tool (MCP), so the post agents can ask the Data agent and the CX agent can hand
off to the post agents. Which agent may call which is set by each agent's allowed_tools in agents/definitions.py, not here."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import deque

from mcp.server.fastmcp import FastMCP

from azure_clients import project_openai
from events import emit, infer_caller
from settings import AGENT_CALL_TIMEOUT_S, AGENT_NAMES, REPORT_TEAM

mcp = FastMCP("agent-bridge", host="0.0.0.0", stateless_http=True, json_response=True)

# Recent calls, so a test run can show which agents talked to which (no fixed order, so this is the evidence).
trace: deque[dict] = deque(maxlen=500)


def _invoke(agent: str, text: str) -> tuple[str, dict]:
    """Runs the agent as a Foundry background response and polls it: a long run (a report takes many minutes) as one open HTTP request gets cut
    by idle-connection limits, whereas short polls do not. A run that fails on the model's token rate limit is retried after a pause."""
    client = project_openai()
    ref = {"agent_reference": {"name": AGENT_NAMES[agent], "type": "agent_reference"}}
    for attempt in range(1, 7):
        resp = client.responses.create(input=text, background=True, extra_body=ref)
        failures = 0
        while resp.status in ("queued", "in_progress"):
            time.sleep(3)
            try:
                resp = client.responses.retrieve(resp.id)
                failures = 0
            except Exception:
                failures += 1  # a dropped poll is not a failed run; try again a few times
                if failures > 5:
                    raise
        if resp.status == "completed":
            usage = getattr(resp, "usage", None)
            return resp.output_text, {"input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None)}
        detail = getattr(getattr(resp, "error", None), "message", None) or str(resp.status)
        if "rate limit" in detail.lower() and attempt < 6:
            time.sleep(65)  # the token quota renews every 60 seconds, so waiting a full window is what actually helps
            continue
        raise RuntimeError(f"the {agent} agent run did not complete ({detail})")
    raise RuntimeError(f"the {agent} agent run did not complete")


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
        answer = f"ERROR: the {agent} agent could not answer ({type(exc).__name__}). Try a smaller question or continue without it."
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
    """Hand a research brief to the Research agent: it investigates a topic, anomaly or hypothesis, pulling data through the Data agent, and
    saves its findings on the case. Returns a short summary of what it found. This can take minutes: if the answer is STILL RUNNING,
    call wait_for_agent until it returns."""
    return await _submit("research", _with_case(brief, case_id), case_id)


@mcp.tool()
async def ask_analysis_agent(brief: str, case_id: str) -> str:
    """Hand a business KPI to the Analysis agent: it analyses drivers, trends and variance, pulling data through the Data agent, and saves its
    findings on the case. Returns a short summary of what it found. This can take minutes: if the answer is STILL RUNNING,
    call wait_for_agent until it returns."""
    return await _submit("analysis", _with_case(brief, case_id), case_id)


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
