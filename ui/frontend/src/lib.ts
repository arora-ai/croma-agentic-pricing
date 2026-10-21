import { TEAM, WORKERS, type Call, type HubEvent, type NodeKey, type StageRow } from "./types";

const POST: NodeKey[] = ["lookup", "research", "analysis", "reporting"];

export function nodeOf(e: HubEvent): NodeKey {
  if (e.type === "agent_start" || e.type === "agent_end") return (e.agent as NodeKey) ?? "cx";
  if (e.server === "genie") return "genie";
  if (e.server === "studio") return "studio";
  return e.server === "memory" ? "memory" : "context";
}

function depthOf(node: NodeKey, caller: string): number {
  if (node === "cx" || node === "memory") return node === "cx" ? 0 : 1;
  if (POST.includes(node)) return 1;
  if (TEAM.includes(node) || WORKERS.includes(node)) return 2;
  if (node === "data") return caller === "reporting" || TEAM.includes(caller as NodeKey) ? 3 : 2;
  if (node === "genie") return 4;
  if (node === "studio") return TEAM.includes(caller as NodeKey) ? 3 : 2;
  // case context tools sit one level under whoever called them
  if (caller === "cx") return 1;
  return POST.includes(caller as NodeKey) ? 2 : 1;
}

/** Pairs start and end events into one row per call, in start order. */
export function buildCalls(events: HubEvent[]): Call[] {
  const calls = new Map<string, Call>();
  for (const e of events) {
    if (e.type === "stage_start" || e.type === "stage_end") continue;
    const node = nodeOf(e);
    const isTool = e.type.startsWith("tool");
    if (e.type === "agent_start" || e.type === "tool_start") {
      calls.set(e.id, {
        id: e.id, turn: e.turn, node, caller: (e.caller as Call["caller"]) ?? "user", kind: isTool ? "tool" : "agent", tool: e.tool,
        start: e.ts, ask: e.ask, args: e.args, caseId: e.case_id || undefined, depth: depthOf(node, e.caller ?? ""),
      });
    } else {
      const c = calls.get(e.id);
      if (!c) continue;
      Object.assign(c, {
        end: e.ts, ok: e.ok, seconds: e.seconds, answer: e.answer ?? c.answer, summary: e.summary, detail: e.detail,
        caseId: e.case_id || c.caseId, tokensIn: e.input_tokens ?? undefined, tokensOut: e.output_tokens ?? undefined,
      });
    }
  }
  return [...calls.values()].sort((a, b) => a.start - b.start);
}

/** Pairs the stage events of Analysis and Research runs into one row per stage, in start order. */
export function buildStages(events: HubEvent[]): StageRow[] {
  const rows = new Map<string, StageRow>();
  for (const e of events) {
    if (e.type === "stage_start") {
      rows.set(e.id, { id: e.id, turn: e.turn, harness: e.harness ?? "", runId: e.run_id ?? "", caseId: e.case_id || undefined, stage: e.stage ?? "", start: e.ts });
    } else if (e.type === "stage_end") {
      const r = rows.get(e.id);
      if (r) Object.assign(r, { end: e.ts, ok: e.ok, seconds: e.seconds, summary: e.summary });
    }
  }
  return [...rows.values()].sort((a, b) => a.start - b.start);
}

/** The steps a user sees for a harness run, each fed by one or more of the hub's internal stages. */
export const HARNESS_STEPS: { key: string; label: string; stages: string[] }[] = [
  { key: "scope", label: "Plan", stages: ["scope"] },
  { key: "data", label: "Get data", stages: ["gather", "gather_2"] },
  { key: "compute", label: "Compute", stages: ["columns", "compute", "evaluate", "columns_2", "compute_2"] },
  { key: "conclude", label: "Conclude", stages: ["conclude", "conclude_2"] },
  { key: "verify", label: "Verify figures", stages: ["verify"] },
  { key: "challenge", label: "Challenge", stages: ["challenge", "revise", "verify_2"] },
  { key: "deliver", label: "Deliver", stages: ["deliver"] },
];

export function callTitle(c: Call): string {
  if (c.kind === "agent") return c.node === "cx" ? "CX agent" : `${c.node[0].toUpperCase()}${c.node.slice(1)} agent`;
  if (c.node === "genie") return "Genie query";
  if (c.node === "memory") return "Memory recall";
  if (c.node === "studio") return `Studio: ${(c.tool ?? "").replace(/_/g, " ")}`;
  return c.tool ? c.tool.replace(/_/g, " ") : "Case context";
}

export function callSummary(c: Call): string {
  if (c.kind === "agent") return (c.ask ?? "").replace(/^\[case_id: [^\]]+\]\s*/, "");
  if (c.node === "genie") return c.args?.query ?? c.detail?.question ?? "";
  if (c.node === "memory") return c.summary ?? "Searching your saved preferences";
  const a = c.args ?? {};
  if (c.node === "studio") return a.label ?? a.result_id ?? a.items?.[0]?.label ?? a.title ?? a.case_id ?? "";
  return a.title ?? a.request_summary ?? a.case_id ?? "";
}

export function fmtClock(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

export function fmtSeconds(s: number): string {
  return s < 10 ? `${s.toFixed(1)}s` : `${Math.round(s)}s`;
}

export function isReport(text: string): boolean {
  return /^#\s/m.test(text) && /##\s*Summary/i.test(text);
}

export function relativeTime(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "";
  const s = Date.now() / 1000 - epochSeconds;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
}

/** Groups conversations like ChatGPT does: Today, Yesterday, Previous 7 days, Older. */
export function groupByRecency<T extends { updated_at: number }>(items: T[]): { label: string; items: T[] }[] {
  const startOfToday = new Date().setHours(0, 0, 0, 0) / 1000;
  const buckets: [string, (t: number) => boolean][] = [
    ["Today", (t) => t >= startOfToday],
    ["Yesterday", (t) => t >= startOfToday - 86400 && t < startOfToday],
    ["Previous 7 days", (t) => t >= startOfToday - 7 * 86400 && t < startOfToday - 86400],
    ["Older", (t) => t < startOfToday - 7 * 86400],
  ];
  return buckets.map(([label, test]) => ({ label, items: items.filter((i) => test(i.updated_at)) })).filter((g) => g.items.length > 0);
}

