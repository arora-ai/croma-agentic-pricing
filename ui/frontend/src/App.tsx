import { PanelLeft, PanelRight, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { Boundary } from "./components/Boundary";
import { Chat } from "./components/Chat";
import { Sidebar } from "./components/Sidebar";
import { Telemetry, type Tab } from "./components/Telemetry";
import { Topology } from "./components/Topology";
import { buildCalls } from "./lib";
import { useChat } from "./store";

export default function App() {
  const { state, send, reset, open, remove, rename } = useChat();
  const [sidebar, setSidebar] = useState(true);
  const [admin, setAdmin] = useState(true);
  const [tab, setTab] = useState<Tab>("activity");
  const latest = useMemo(() => buildCalls(state.events).filter((c) => c.turn === state.turn), [state.events, state.turn]);
  const live = state.running;

  const showMemory = () => {
    setAdmin(true);
    setTab("memory");
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <button className={`ghost icon ${sidebar ? "on" : ""}`} onClick={() => setSidebar(!sidebar)} aria-label="Toggle chat history" title="Chat history"><PanelLeft size={17} /></button>
          <span className="logo">C</span>
          <span className="name">Croma <em>Pricing Assistant</em></span>
        </div>
        <div className="actions">
          <span className={`conn ${state.health?.hub ? "up" : "down"}`} title={state.health ? `Foundry project ${state.health.project}` : "Checking…"}>
            <i />{state.health ? (state.health.hub ? "Connected" : "Hub unreachable") : "Connecting…"}
          </span>
          {!sidebar && <button className="ghost" onClick={reset} disabled={state.running || state.messages.length === 0}><Plus size={15} />New chat</button>}
          <button className={`ghost icon ${admin ? "on" : ""}`} onClick={() => setAdmin(!admin)} aria-label="Toggle admin view" title="Admin view"><PanelRight size={17} /></button>
        </div>
      </header>

      <main className={`layout ${sidebar ? "sb" : ""} ${admin ? "ad" : ""}`}>
        {sidebar && (
          <Sidebar state={state} disabled={state.running} onNew={reset} onOpen={open} onRename={rename} onDelete={remove} onMemory={showMemory} />
        )}
        <Chat state={state} onSend={send} />
        {admin && (
          <aside className="admin">
            <div className="admin-head">
              <span>Admin view</span>
              <span className={`live ${live ? "on" : ""}`}><i />{live ? "Live" : "Idle"}</span>
            </div>
            <div className="card network">
              <div className="card-title">Agent network<span>{state.health?.project ?? ""}</span></div>
              <Boundary label="The network view"><Topology calls={latest} running={live} genieBackend={state.health?.genie_backend} /></Boundary>
            </div>
            <Telemetry state={state} tab={tab} setTab={setTab} />
          </aside>
        )}
      </main>
    </div>
  );
}
