export type NodeKey =
  | "cx" | "lookup" | "research" | "analysis" | "reporting" | "data" | "genie" | "context" | "memory"
  | "planner" | "insight" | "strategist" | "writer" | "reviewer" | "studio";

export interface HubEvent {
  seq: number;
  ts: number;
  type: "agent_start" | "agent_end" | "tool_start" | "tool_end";
  id: string;
  agent?: string;
  caller?: string;
  server?: string;
  tool?: string;
  case_id?: string;
  ask?: string;
  answer?: string;
  ok?: boolean;
  seconds?: number;
  summary?: string;
  detail?: Record<string, any>;
  args?: Record<string, any>;
  input_tokens?: number | null;
  output_tokens?: number | null;
  turn: number;
}

export interface Call {
  id: string;
  turn: number;
  node: NodeKey;
  caller: NodeKey | "user";
  kind: "agent" | "tool";
  tool?: string;
  start: number;
  end?: number;
  ok?: boolean;
  seconds?: number;
  ask?: string;
  answer?: string;
  summary?: string;
  detail?: Record<string, any>;
  args?: Record<string, any>;
  caseId?: string;
  tokensIn?: number;
  tokensOut?: number;
  depth: number;
}

export interface LogLine {
  ts: number;
  level: "info" | "debug" | "warn" | "error";
  msg: string;
  turn: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  status: "streaming" | "done" | "error";
  turn: number;
  seconds?: number;
}

export const NODES: Record<NodeKey, { label: string; sub: string; color: string }> = {
  cx: { label: "CX agent", sub: "Pre · front of the system", color: "#0f766e" },
  lookup: { label: "Lookup", sub: "Post agent", color: "#0369a1" },
  research: { label: "Research", sub: "Post agent", color: "#2563eb" },
  analysis: { label: "Analysis", sub: "Post agent", color: "#7c3aed" },
  reporting: { label: "Reporting", sub: "Editor", color: "#be185d" },
  planner: { label: "Planner", sub: "Designs", color: "#c026d3" },
  insight: { label: "Insight", sub: "Analyses", color: "#7e22ce" },
  strategist: { label: "Strategist", sub: "Prescribes", color: "#a21caf" },
  reviewer: { label: "Reviewer", sub: "Critiques", color: "#be123c" },
  writer: { label: "Writer", sub: "Drafts", color: "#db2777" },
  studio: { label: "Studio", sub: "Ledger", color: "#9d174d" },
  data: { label: "Data agent", sub: "Only door to data", color: "#b45309" },
  genie: { label: "Genie", sub: "Databricks", color: "#c2410c" },
  context: { label: "Case file", sub: "Shared findings", color: "#475569" },
  memory: { label: "Memory", sub: "Preferences", color: "#0e7490" },
};

export const AGENT_NODES: NodeKey[] = ["cx", "lookup", "research", "analysis", "reporting", "planner", "insight", "strategist", "writer", "reviewer", "data"];
export const TEAM: NodeKey[] = ["planner", "insight", "strategist", "writer", "reviewer"];

export interface ConvSummary {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  last_question?: string | null;
}

export interface MemoryItem {
  id: string;
  kind: "user_profile" | "chat_summary" | "procedural" | string;
  content: string;
  updated_at: number | null;
}


