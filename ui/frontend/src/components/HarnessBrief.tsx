import { useEffect, useState } from "react";
import { fetchRuns, type HarnessRun } from "../api";
import { fmtSeconds } from "../lib";
import type { StageRow } from "../types";
import { Markdown } from "./Markdown";

/** The finished brief of an Analysis or Research run: the tables, the checks that passed, the caveats and the evidence, which the chat reply only summarises. */
export function HarnessBrief({ stages }: { stages: StageRow[] }) {
  const last = stages[stages.length - 1];
  const [run, setRun] = useState<HarnessRun | null>(null);
  useEffect(() => {
    if (!last?.caseId) return;
    let live = true;
    fetchRuns(last.caseId).then((runs) => {
      if (live) setRun(runs.find((r) => r.id === last.runId) ?? null);
    });
    return () => { live = false; };
  }, [last?.caseId, last?.runId]);
  if (!run?.markdown) return null;
  const seconds = run.stages.reduce((n, s) => n + (s.seconds ?? 0), 0);
  return (
    <details className="brief">
      <summary>
        Full {run.harness === "research" ? "research" : "analysis"} brief
        <span>{run.stages.length} stages · {fmtSeconds(seconds)} · every figure traced to the data</span>
      </summary>
      <Markdown>{run.markdown}</Markdown>
      <ol className="brief-stages">
        {run.stages.map((s) => (
          <li key={s.name}><b>{s.name.replace(/_/g, " ")}</b>{s.summary ? ` ${s.summary}` : ""}{s.seconds ? ` · ${fmtSeconds(s.seconds)}` : ""}</li>
        ))}
      </ol>
    </details>
  );
}
