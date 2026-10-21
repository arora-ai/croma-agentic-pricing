import type { ConvSummary, HubEvent, MemoryItem } from "./types";

export interface StreamHandlers {
  onTurn: (d: { turn_id: string; conversation_id: string; turn: number; title: string; new: boolean }) => void;
  onTitle: (d: { conversation_id: string; title: string }) => void;
  onDelta: (text: string) => void;
  onFinal: (d: { text: string; seconds: number }) => void;
  onHub: (e: Omit<HubEvent, "turn">) => void;
  onLog: (d: { level: "info" | "debug" | "warn" | "error"; msg: string }) => void;
  onError: (message: string) => void;
  onEnd: () => void;
}

/** POSTs one chat turn and dispatches the server-sent events as they arrive. */
export async function streamChat(message: string, conversationId: string | null, h: StreamHandlers, signal: AbortSignal): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (!res.ok || !res.body) {
    h.onError(`Request failed (${res.status})`);
    h.onEnd();
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const kind = /^event: (.*)$/m.exec(frame)?.[1];
      const data = /^data: (.*)$/m.exec(frame)?.[1];
      if (!kind || !data) continue;
      const payload = JSON.parse(data);
      if (kind === "turn") h.onTurn(payload);
      else if (kind === "title") h.onTitle(payload);
      else if (kind === "delta") h.onDelta(payload.text);
      else if (kind === "final") h.onFinal(payload);
      else if (kind === "hub") h.onHub(payload);
      else if (kind === "log") h.onLog(payload);
      else if (kind === "error") h.onError(payload.message);
      else if (kind === "end") h.onEnd();
    }
  }
}

export async function fetchCase(caseId: string): Promise<any | null> {
  const res = await fetch(`/api/case/${caseId}`);
  return res.ok ? res.json() : null;
}

export interface HarnessRun {
  id: string;
  harness: string;
  status: string;
  error: string;
  markdown: string | null;
  stages: { name: string; status: string; seconds: number | null; summary: string }[];
}

export async function fetchRuns(caseId: string): Promise<HarnessRun[]> {
  const res = await fetch(`/api/runs/${caseId}`);
  return res.ok ? res.json() : [];
}

export const reportUrl = (caseId: string, n: number, ext: "docx" | "html") => `/api/report/${caseId}/${n}/${ext}`;

export async function fetchHealth(): Promise<{ hub: boolean; project: string; cx_agent: string; memory_scope: string; genie_backend?: string } | null> {
  try {
    const res = await fetch("/api/health");
    return res.ok ? res.json() : null;
  } catch {
    return null;
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}

export const listConversations = () => fetch("/api/conversations").then((r) => json<ConvSummary[]>(r));
export const getConversation = (id: string) => fetch(`/api/conversations/${id}`).then((r) => json<ConversationDetail>(r));
export const renameConversation = (id: string, title: string) =>
  fetch(`/api/conversations/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) }).then((r) => json(r));
export const deleteConversation = (id: string) => fetch(`/api/conversations/${id}`, { method: "DELETE" }).then((r) => json(r));

export const listMemory = () => fetch("/api/memory").then((r) => json<MemoryItem[]>(r));
export const addMemory = (content: string) =>
  fetch("/api/memory", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }) }).then((r) => json<MemoryItem>(r));
export const deleteMemory = (id: string) => fetch(`/api/memory/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => json(r));
export const clearMemory = () => fetch("/api/memory", { method: "DELETE" }).then((r) => json(r));

export interface ConversationDetail extends ConvSummary {
  messages: { turn: number; role: "user" | "assistant"; text: string; status: "streaming" | "done" | "error"; seconds: number | null }[];
  turns: { turn: number; events: Omit<HubEvent, "turn">[]; logs: { level: "info" | "debug" | "warn" | "error"; msg: string; ts: number }[] }[];
}
