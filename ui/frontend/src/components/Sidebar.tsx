import { Brain, Check, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { groupByRecency } from "../lib";
import type { State } from "../store";
import type { ConvSummary } from "../types";

interface Props {
  state: State;
  disabled: boolean;
  onNew: () => void;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onMemory: () => void;
}

function Item({ c, active, disabled, onOpen, onRename, onDelete }: { c: ConvSummary; active: boolean; disabled: boolean } & Pick<Props, "onOpen" | "onRename" | "onDelete">) {
  const [mode, setMode] = useState<"view" | "rename" | "confirm">("view");
  const [draft, setDraft] = useState(c.title);
  const input = useRef<HTMLInputElement>(null);

  if (mode === "rename") {
    const commit = () => {
      if (draft.trim() && draft.trim() !== c.title) onRename(c.id, draft.trim());
      setMode("view");
    };
    return (
      <div className="conv editing">
        <input
          ref={input}
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setMode("view");
          }}
          onBlur={commit}
        />
      </div>
    );
  }

  if (mode === "confirm") {
    return (
      <div className="conv confirming">
        <span>Delete this chat?</span>
        <button className="icon-btn danger" onClick={() => onDelete(c.id)} aria-label="Confirm delete"><Check size={14} /></button>
        <button className="icon-btn" onClick={() => setMode("view")} aria-label="Cancel"><X size={14} /></button>
      </div>
    );
  }

  return (
    <div className={`conv ${active ? "active" : ""}`}>
      <button className="conv-main" disabled={disabled && !active} onClick={() => onOpen(c.id)} title={c.title}>
        <span className="conv-title">{c.title}</span>
      </button>
      <div className="conv-actions">
        <button className="icon-btn" onClick={() => { setDraft(c.title); setMode("rename"); }} aria-label="Rename"><Pencil size={13} /></button>
        <button className="icon-btn" disabled={disabled} onClick={() => setMode("confirm")} aria-label="Delete"><Trash2 size={13} /></button>
      </div>
    </div>
  );
}

export function Sidebar({ state, disabled, onNew, onOpen, onRename, onDelete, onMemory }: Props) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? state.conversations.filter((c) => c.title.toLowerCase().includes(q) || (c.last_question ?? "").toLowerCase().includes(q)) : state.conversations;
    return groupByRecency(list);
  }, [state.conversations, query]);

  return (
    <aside className="sidebar">
      <button className="new-chat" onClick={onNew} disabled={disabled || state.messages.length === 0}><Plus size={16} />New chat</button>
      <label className="search">
        <Search size={14} />
        <input placeholder="Search chats" value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      <div className="conv-list">
        {groups.length === 0 && <p className="conv-empty">{query ? "No chats match." : "Your conversations will appear here."}</p>}
        {groups.map((g) => (
          <div key={g.label} className="conv-group">
            <div className="conv-label">{g.label}</div>
            {g.items.map((c) => (
              <Item key={c.id} c={c} active={c.id === state.conversationId} disabled={disabled} onOpen={onOpen} onRename={onRename} onDelete={onDelete} />
            ))}
          </div>
        ))}
      </div>
      <div className="side-foot">
        <div className="user">
          <span className="user-avatar">You</span>
          <span className="user-name">{state.health?.memory_scope ?? "user"}<em>Single-user test</em></span>
        </div>
        <button className="ghost icon" onClick={onMemory} title="What the assistant remembers about you" aria-label="Memory"><Brain size={16} /></button>
      </div>
    </aside>
  );
}
