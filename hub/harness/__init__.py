"""Analysis and Research as harnesses: code owns the stages, the arithmetic and the checks; language-model workers (scoper, analyst, critic) do the reasoning.

    execute("analysis" | "research", brief, case_id, deps)  ->  the finished (or failed) Run; run.result["markdown"] is what the CX agent relays."""
from __future__ import annotations

from harness import analysis, research, runtime
from harness.pipeline import run as run_pipeline

SPECS = {"analysis": analysis.SPEC, "research": research.SPEC}


async def execute(name: str, brief: str, case_id: str, deps: runtime.Deps) -> runtime.Run:
    spec = SPECS[name]

    async def pipeline(ctx: runtime.Ctx) -> dict:
        return await run_pipeline(ctx, spec)

    return await runtime.execute(name, brief, case_id, pipeline, deps)
