import { useCallback, useEffect, useReducer, useRef } from "react";
import { deleteConversation, fetchHealth, getConversation, listConversations, renameConversation, streamChat, type ConversationDetail } from "./api";
import type { ConvSummary, HubEvent, LogLine, Message } from "./types";

export interface State {
  messages: Message[];
  events: HubEvent[];
  logs: LogLine[];
  conversations: ConvSummary[];
  conversationId: string | null;
  turn: number;
  running: boolean;
  loading: boolean;
  caseId: string | null;
  memoryTick: number;
  health: { hub: boolean; project: string; cx_agent: string; memory_scope: string; genie_backend?: string; sharepoint_backend?: string } | null;
}

const initial: State = {
  messages: [], events: [], logs: [], conversations: [], conversationId: null, turn: 0, running: false, loading: false, caseId: null, memoryTick: 0, health: null,
};

type Action =
  | { type: "send"; text: string }
  | { type: "turn"; conversationId: string; title: string; isNew: boolean }
  | { type: "title"; conversationId: string; title: string }
  | { type: "delta"; text: string }
  | { type: "final"; text: string; seconds: number }
  | { type: "error"; message: string }
  | { type: "hub"; event: Omit<HubEvent, "turn"> }
  | { type: "log"; level: LogLine["level"]; msg: string }
  | { type: "end" }
  | { type: "reset" }
  | { type: "loading" }
  | { type: "load"; detail: ConversationDetail }
  | { type: "conversations"; list: ConvSummary[] }
  | { type: "health"; health: State["health"] };

const now = () => Date.now();

function lastCaseId(events: HubEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) if (events[i].case_id && events[i].type.endsWith("_end")) return events[i].case_id!;
  return null;
}

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "send": {
      const turn = s.turn + 1;
      return {
        ...s, turn, running: true,
        messages: [
          ...s.messages,
          { id: `u${turn}`, role: "user", text: a.text, status: "done", turn },
          { id: `a${turn}`, role: "assistant", text: "", status: "streaming", turn },
        ],
      };
    }
    case "turn": {
      const t = now() / 1000;
      const rest = s.conversations.filter((c) => c.id !== a.conversationId);
      const existing = s.conversations.find((c) => c.id === a.conversationId);
      const entry: ConvSummary = existing ? { ...existing, updated_at: t } : { id: a.conversationId, title: a.title, created_at: t, updated_at: t };
      return { ...s, conversationId: a.conversationId, conversations: [entry, ...rest] };
    }
    case "title":
      return { ...s, conversations: s.conversations.map((c) => (c.id === a.conversationId ? { ...c, title: a.title } : c)) };
    case "delta":
      return { ...s, messages: s.messages.map((m) => (m.role === "assistant" && m.turn === s.turn ? { ...m, text: m.text + a.text } : m)) };
    case "final":
      return {
        ...s,
        messages: s.messages.map((m) => (m.role === "assistant" && m.turn === s.turn ? { ...m, text: a.text || m.text, status: "done", seconds: a.seconds } : m)),
      };
    case "error":
      return {
        ...s,
        messages: s.messages.map((m) => (m.role === "assistant" && m.turn === s.turn ? { ...m, text: a.message, status: "error" } : m)),
        logs: [...s.logs, { ts: now(), level: "error", msg: a.message, turn: s.turn }],
      };
    case "hub": {
      const event = { ...a.event, turn: s.turn };
      const caseId = event.case_id && event.type.endsWith("_end") ? event.case_id : s.caseId;
      return { ...s, events: [...s.events, event], caseId: caseId || s.caseId };
    }
    case "log":
      return { ...s, logs: [...s.logs, { ts: now(), level: a.level, msg: a.msg, turn: s.turn }] };
    case "end":
      return {
        ...s, running: false, memoryTick: s.memoryTick + 1,
        messages: s.messages.map((m) => (m.status === "streaming" ? { ...m, status: m.text ? "done" : "error", text: m.text || "No answer was returned." } : m)),
      };
    case "reset":
      return { ...initial, conversations: s.conversations, health: s.health, memoryTick: s.memoryTick };
    case "loading":
      return { ...s, loading: true };
    case "load": {
      const events: HubEvent[] = a.detail.turns.flatMap((t) => t.events.map((e) => ({ ...e, turn: t.turn })));
      const logs: LogLine[] = a.detail.turns.flatMap((t) => t.logs.map((l) => ({ ...l, turn: t.turn })));
      return {
        ...s, loading: false, running: false, conversationId: a.detail.id, events, logs, caseId: lastCaseId(events),
        turn: a.detail.messages.reduce((n, m) => Math.max(n, m.turn), 0),
        messages: a.detail.messages.map((m) => ({ id: `${m.role[0]}${m.turn}`, role: m.role, text: m.text, status: m.status, turn: m.turn, seconds: m.seconds ?? undefined })),
      };
    }
    case "conversations":
      return { ...s, conversations: a.list };
    case "health":
      return { ...s, health: a.health };
  }
}

export function useChat() {
  const [state, dispatch] = useReducer(reducer, initial);
  const abort = useRef<AbortController | null>(null);
  const conversation = useRef<string | null>(null);
  conversation.current = state.conversationId;

  const refresh = useCallback(() => {
    listConversations().then((list) => dispatch({ type: "conversations", list })).catch(() => undefined);
  }, []);

  useEffect(() => {
    fetchHealth().then((health) => dispatch({ type: "health", health }));
    refresh();
  }, [refresh]);

  const send = useCallback((text: string) => {
    const message = text.trim();
    if (!message) return;
    dispatch({ type: "send", text: message });
    abort.current = new AbortController();
    streamChat(
      message, conversation.current,
      {
        onTurn: (d) => dispatch({ type: "turn", conversationId: d.conversation_id, title: d.title, isNew: d.new }),
        onTitle: (d) => dispatch({ type: "title", conversationId: d.conversation_id, title: d.title }),
        onDelta: (t) => dispatch({ type: "delta", text: t }),
        onFinal: (d) => dispatch({ type: "final", text: d.text, seconds: d.seconds }),
        onHub: (event) => dispatch({ type: "hub", event }),
        onLog: (d) => dispatch({ type: "log", level: d.level, msg: d.msg }),
        onError: (msg) => dispatch({ type: "error", message: msg }),
        onEnd: () => {
          dispatch({ type: "end" });
          refresh();
        },
      },
      abort.current.signal,
    ).catch((err) => {
      if (err?.name !== "AbortError") dispatch({ type: "error", message: String(err) });
      dispatch({ type: "end" });
    });
  }, [refresh]);

  const reset = useCallback(() => {
    abort.current?.abort();
    dispatch({ type: "reset" });
  }, []);

  const open = useCallback((id: string) => {
    abort.current?.abort();
    dispatch({ type: "loading" });
    getConversation(id).then((detail) => dispatch({ type: "load", detail })).catch(() => dispatch({ type: "reset" }));
  }, []);

  const remove = useCallback(async (id: string) => {
    await deleteConversation(id).catch(() => undefined);
    if (conversation.current === id) dispatch({ type: "reset" });
    refresh();
  }, [refresh]);

  const rename = useCallback(async (id: string, title: string) => {
    dispatch({ type: "title", conversationId: id, title });
    await renameConversation(id, title).catch(() => undefined);
  }, []);

  return { state, send, reset, open, remove, rename };
}
