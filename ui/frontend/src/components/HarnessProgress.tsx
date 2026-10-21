import { HARNESS_STEPS } from "../lib";
import type { StageRow } from "../types";

/** The stages of an Analysis or Research run, lit from the hub's stage events. The harness (code), not a model, decides these stages and their order. */
export function HarnessProgress({ stages }: { stages: StageRow[] }) {
  if (stages.length === 0) return null;
  const last = stages[stages.length - 1];
  const rows = stages.filter((s) => s.runId === last.runId);
  const steps = HARNESS_STEPS.map((step) => {
    const seen = rows.filter((r) => step.stages.includes(r.stage));
    const active = seen.find((r) => r.end === undefined);
    const state = active ? "active" : seen.some((r) => r.ok === false) ? "failed" : seen.length > 0 ? "done" : "todo";
    const note = (active ?? seen[seen.length - 1])?.summary ?? "";
    return { ...step, state, note };
  });
  const lastTouched = steps.reduce((acc, s, i) => (s.state !== "todo" ? i : acc), -1);
  return (
    <ol className="rprogress" aria-label={`${last.harness === "research" ? "Research" : "Analysis"} progress`}>
      {steps.map((s, i) => {
        const state = s.state === "todo" && i < lastTouched ? "done" : s.state;
        return (
          <li key={s.key} className={state} title={s.note || undefined}>
            <i />
            <span>{s.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
