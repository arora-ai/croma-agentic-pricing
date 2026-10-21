import { Brain, Check, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { addMemory, clearMemory, deleteMemory, listMemory } from "../api";
import { relativeTime } from "../lib";
import type { MemoryItem } from "../types";

const SECTIONS: { kind: string; title: string; hint: string }[] = [
  { kind: "user_profile", title: "Preferences", hint: "Used as defaults in new conversations" },
  { kind: "procedural", title: "How you like things done", hint: "" },
  { kind: "chat_summary", title: "Conversation summaries", hint: "Short recaps of past chats" },
];

export function MemoryPane({ tick }: { tick: number }) {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);

  const load = useCallback(() => {
    listMemory().then((list) => { setItems(list); setError(null); }).catch(() => setError("Could not read memory."));
  }, []);

  useEffect(() => {
    load();
    if (tick === 0) return;
    // Foundry extracts memories shortly after a conversation goes quiet, so look again a couple of times.
    const timers = [8000, 25000].map((ms) => setTimeout(load, ms));
    return () => timers.forEach(clearTimeout);
  }, [tick, load]);

  const add = async () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    await addMemory(text).catch(() => setError("Could not save that preference."));
    load();
  };
  const remove = async (id: string) => {
    setItems((list) => list?.filter((m) => m.id !== id) ?? null);
    await deleteMemory(id).catch(() => load());
  };
  const clear = async () => {
    setConfirmClear(false);
    setItems([]);
    await clearMemory().catch(() => load());
  };

  return (
    <div className="memory">
      <div className="memory-intro">
        <Brain size={15} />
        <p>What the assistant remembers about you. It uses these as defaults and tells you when it does.</p>
        <button className="icon-btn" onClick={load} aria-label="Refresh"><RefreshCw size={13} /></button>
      </div>

      <div className="memory-add">
        <input
          placeholder="Add a preference, e.g. “Compare against the same month last year”"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <button className="ghost sm" onClick={add} disabled={!draft.trim()}><Plus size={14} />Add</button>
      </div>

      {error && <div className="error-note">{error}</div>}
      {items === null && !error && <p className="memory-empty">Loading…</p>}
      {items !== null && items.length === 0 && <p className="memory-empty">Nothing saved yet. Tell the assistant how you like to work, or add a preference above.</p>}

      {SECTIONS.map((sec) => {
        const rows = (items ?? []).filter((m) => m.kind === sec.kind).sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
        if (rows.length === 0) return null;
        return (
          <div key={sec.kind} className="memory-section">
            <div className="memory-title">{sec.title}<span>{rows.length}</span></div>
            {sec.hint && <div className="memory-hint">{sec.hint}</div>}
            {rows.map((m) => (
              <div key={m.id} className="memory-item">
                <p>{m.content}</p>
                <div className="memory-meta">
                  {Date.now() - (m.updated_at ?? 0) * 1000 < 120000 && <span className="new">New</span>}
                  <span>{relativeTime(m.updated_at)}</span>
                  <button className="icon-btn" onClick={() => remove(m.id)} aria-label="Forget this"><Trash2 size={13} /></button>
                </div>
              </div>
            ))}
          </div>
        );
      })}

      {items && items.length > 0 && (
        <div className="memory-foot">
          {confirmClear ? (
            <>
              <span>Forget everything?</span>
              <button className="icon-btn danger" onClick={clear} aria-label="Confirm"><Check size={14} /></button>
              <button className="icon-btn" onClick={() => setConfirmClear(false)} aria-label="Cancel"><X size={14} /></button>
            </>
          ) : (
            <button className="link" onClick={() => setConfirmClear(true)}>Forget everything</button>
          )}
        </div>
      )}
    </div>
  );
}
