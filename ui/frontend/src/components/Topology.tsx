import { useMemo } from "react";
import { HARNESS_STEPS } from "../lib";
import { NODES, WORKERS, type Call, type NodeKey, type StageRow } from "../types";

const W = 104;
const H = 50;
const TW = 100;
type Placed = Exclude<NodeKey, "scoper" | "analyst" | "critic">;  // the harness workers are drawn as small dots inside the Research and Analysis nodes
const POS: Record<Placed, { x: number; y: number; w?: number }> = {
  memory: { x: 56, y: 42 },
  cx: { x: 221, y: 42 },
  context: { x: 386, y: 42 },
  lookup: { x: 56, y: 150 },
  research: { x: 166, y: 150 },
  analysis: { x: 276, y: 150 },
  reporting: { x: 386, y: 150 },
  data: { x: 221, y: 258 },
  genie: { x: 221, y: 366 },
  planner: { x: 560, y: 112, w: TW },
  insight: { x: 560, y: 172, w: TW },
  strategist: { x: 560, y: 232, w: TW },
  reviewer: { x: 560, y: 292, w: TW },
  writer: { x: 560, y: 352, w: TW },
  studio: { x: 560, y: 412, w: TW },
};

const EDGES: { from: Placed; to: Placed; dashed?: boolean }[] = [
  { from: "cx", to: "lookup" },
  { from: "cx", to: "research" },
  { from: "cx", to: "analysis" },
  { from: "cx", to: "reporting" },
  { from: "lookup", to: "data" },
  { from: "research", to: "data" },
  { from: "analysis", to: "data" },
  { from: "reporting", to: "data" },
  { from: "data", to: "genie" },
  { from: "cx", to: "context", dashed: true },
  { from: "cx", to: "memory", dashed: true },
  { from: "reporting", to: "planner" },
  { from: "reporting", to: "insight" },
  { from: "reporting", to: "strategist" },
  { from: "reporting", to: "reviewer" },
  { from: "reporting", to: "writer" },
  { from: "insight", to: "data", dashed: true },
  { from: "strategist", to: "data", dashed: true },
  { from: "writer", to: "studio", dashed: true },
];

const TEAM_SET = new Set<Placed>(["planner", "insight", "strategist", "reviewer", "writer"]);

function edgePath(from: Placed, to: Placed): string {
  const a = POS[from];
  const b = POS[to];
  if (from === "cx" && to === "context") return `M ${a.x + W / 2} ${a.y} L ${b.x - (b.w ?? W) / 2} ${b.y}`;
  if (from === "cx" && to === "memory") return `M ${a.x - W / 2} ${a.y} L ${b.x + (b.w ?? W) / 2} ${b.y}`;
  if (from === "reporting" && TEAM_SET.has(to)) {
    const x1 = a.x + W / 2;
    const x2 = b.x - TW / 2;
    const mid = (x1 + x2) / 2;
    return `M ${x1} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${x2} ${b.y}`;
  }
  if (TEAM_SET.has(from) && to === "data") {
    const x1 = a.x - TW / 2;
    const x2 = b.x + W / 2;
    const mid = (x1 + x2) / 2;
    return `M ${x1} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${x2} ${b.y}`;
  }
  if (TEAM_SET.has(from) && to === "studio") return `M ${a.x} ${a.y + H / 2} L ${b.x} ${b.y - H / 2}`;
  const y1 = a.y + H / 2;
  const y2 = b.y - H / 2;
  const mid = (y1 + y2) / 2;
  return `M ${a.x} ${y1} C ${a.x} ${mid}, ${b.x} ${mid}, ${b.x} ${y2}`;
}

export function Topology({ calls, running, genieBackend, stages = [] }: { calls: Call[]; running: boolean; genieBackend?: string; stages?: StageRow[] }) {
  const { open, used, count, activeEdges, workerOpen, workerUsed } = useMemo(() => {
    const open = new Map<NodeKey, number>();
    const used = new Set<NodeKey>();
    const count = new Map<NodeKey, number>();
    const activeEdges = new Set<string>();
    const workerOpen = new Set<string>();
    const workerUsed = new Set<string>();
    for (const c of calls) {
      used.add(c.node);
      count.set(c.node, (count.get(c.node) ?? 0) + 1);
      if (WORKERS.includes(c.node)) {
        workerUsed.add(`${c.caller}>${c.node}`);
        if (c.end === undefined && running) workerOpen.add(`${c.caller}>${c.node}`);
      }
      if (c.end === undefined && running) {
        open.set(c.node, (open.get(c.node) ?? 0) + 1);
        if (c.caller !== "user") activeEdges.add(`${c.caller}>${c.node}`);
      }
    }
    return { open, used, count, activeEdges, workerOpen, workerUsed };
  }, [calls, running]);

  // what a harness node is doing right now, in the user's words ("get data", "conclude")
  const stageLabel = (k: NodeKey) => {
    const row = [...stages].reverse().find((s) => s.harness === k && s.end === undefined);
    return row ? HARNESS_STEPS.find((s) => s.stages.includes(row.stage))?.label.toLowerCase() : undefined;
  };

  const status = (k: NodeKey) => {
    if (open.get(k)) return k === "genie" ? "querying" : k === "context" || k === "studio" ? "writing" : k === "memory" ? "recalling" : k === "data" ? "fetching" : "working";
    return used.has(k) ? "done" : "idle";
  };

  return (
    <svg className="topology" viewBox="0 0 640 450" role="img" aria-label="Agent network">
      <rect className="team-box" x={496} y={62} width={128} height={332} rx={14} />
      <text className="team-label" x={510} y={80}>REPORT TEAM</text>
      {EDGES.map((e) => {
        const key = `${e.from}>${e.to}`;
        const on = activeEdges.has(key);
        const usedEdge = used.has(e.to) && used.has(e.from);
        return (
          <g key={key} style={{ ["--c" as string]: NODES[e.to].color }}>
            <path d={edgePath(e.from, e.to)} className={`edge ${e.dashed ? "dashed" : ""} ${usedEdge ? "used" : ""}`} />
            {on && <path d={edgePath(e.from, e.to)} className="edge flow" />}
          </g>
        );
      })}
      {(Object.keys(POS) as Placed[]).map((k) => {
        const p = POS[k];
        const w = p.w ?? W;
        const st = status(k);
        const n = count.get(k) ?? 0;
        const harness = k === "research" || k === "analysis";
        return (
          <g key={k} className={`node ${st}`} style={{ ["--c" as string]: NODES[k].color }} transform={`translate(${p.x - w / 2}, ${p.y - H / 2})`}>
            {st === "working" || st === "querying" || st === "fetching" || st === "writing" || st === "recalling" ? <rect className="halo" width={w} height={H} rx={12} /> : null}
            <rect className="box" width={w} height={H} rx={12} />
            <circle className="dot" cx={15} cy={H / 2} r={4.5} />
            <text className="label" x={28} y={H / 2 - 3}>{NODES[k].label}</text>
            <text className="sub" x={28} y={H / 2 + 12}>{open.get(k) ? `${harness ? stageLabel(k) ?? st : st}…` : k === "genie" ? ({ databricks: "Databricks", custom: "Custom", stub: "Not connected" } as Record<string, string>)[genieBackend ?? ""] ?? "Data" : NODES[k].sub.split(" · ")[0]}</text>
            {harness && WORKERS.map((wk, i) => {
              const key = `${k}>${wk}`;
              return (
                <circle key={wk} className={`wdot ${workerOpen.has(key) ? "on" : workerUsed.has(key) ? "used" : ""}`} cx={w - 12} cy={27 + i * 9} r={3.2} style={{ ["--c" as string]: NODES[wk].color }}>
                  <title>{`${NODES[wk].label}: ${NODES[wk].sub}`}</title>
                </circle>
              );
            })}
            {n > 0 && (
              <g transform={`translate(${w - 14}, 12)`}>
                <circle r={9} className="badge" />
                <text className="badge-text" textAnchor="middle" dy="3.5">{n}</text>
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );
}

