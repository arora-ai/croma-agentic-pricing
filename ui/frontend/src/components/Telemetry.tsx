import { Activity, Check, ChevronRight, CircleAlert, Copy, FolderOpen, ScrollText } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchCase } from "../api";
import { buildCalls, callSummary, callTitle, fmtClock, fmtSeconds, nodeOf } from "../lib";
import type { State } from "../store";
import { NODES, type Call, type HubEvent } from "../types";
import { ReportCardView } from "./ReportCard";
import { Boundary } from "./Boundary";
import { MemoryPane } from "./Memory";

type Tab = "activity" | "logs" | "case" | "memory";

function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function Stats({ calls, running }: { calls: Call[]; running: boolean }) {
  const now = useNow(running);
  const cx = calls.find((c) => c.node === "cx");
  const elapsed = cx ? ((cx.end ?? now) - cx.start) / 1000 : 0;
  const agents = calls.filter((c) => c.kind === "agent" && c.node !== "cx").length;
  const queries = calls.filter((c) => c.node === "genie").length;
  const fileReads = calls.filter((c) => c.node === "files" && c.tool === "read_sharepoint_file").length;
  const tokens = calls.reduce((n, c) => n + (c.tokensIn ?? 0) + (c.tokensOut ?? 0), 0);
  const items: [string, string][] = [
    ["Elapsed", cx ? fmtSeconds(elapsed) : "–"],
    ["Agent calls", String(agents)],
    ["Data queries", String(queries)],
    ...(fileReads > 0 ? [["Files read", String(fileReads)] as [string, string]] : []),
    ["Tokens", tokens ? (tokens > 9999 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens)) : "–"],
  ];
  return (
    <div className="stats">
      {items.map(([k, v]) => (
        <div key={k}><b>{v}</b><span>{k}</span></div>
      ))}
    </div>
  );
}

function Json({ value }: { value: unknown }) {
  return <pre className="code">{JSON.stringify(value, null, 2)}</pre>;
}

function Detail({ c }: { c: Call }) {
  if (c.kind === "agent") {
    return (
      <div className="detail">
        <label>Ask</label><div className="text">{c.ask}</div>
        {c.answer !== undefined && <><label>Answer</label><div className="text">{c.answer}</div></>}
        {(c.tokensIn || c.tokensOut) && <div className="chips"><span>in {c.tokensIn ?? 0} tokens</span><span>out {c.tokensOut ?? 0} tokens</span></div>}
      </div>
    );
  }
  if (c.node === "genie") {
    const d = c.detail ?? {};
    const rows: unknown[][] = d.rows ?? [];
    const cols: string[] = d.columns ?? [];
    return (
      <div className="detail">
        <label>Question</label><div className="text">{c.args?.query}</div>
        {d.sql && <><label>SQL generated</label><pre className="code">{d.sql}</pre></>}
        {rows.length > 0 && (
          <>
            <label>Result · {d.row_count} rows{d.truncated ? " (capped)" : ""}</label>
            <div className="mini-table"><table>
              <thead><tr>{cols.map((x) => <th key={x}>{x}</th>)}</tr></thead>
              <tbody>{rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>)}</tbody>
            </table></div>
          </>
        )}
        {d.error && <div className="error-note">{d.error}</div>}
      </div>
    );
  }
  if (c.node === "files") {
    const d = c.detail ?? {};
    const rows: unknown[][] = d.rows ?? [];
    const cols: string[] = d.columns ?? [];
    return (
      <div className="detail">
        {c.tool === "search_sharepoint" ? (
          <>
            <label>Search</label><div className="text">{c.args?.query || "(list the library)"}</div>
            {(d.files ?? []).length > 0 && <><label>Files found</label><ul className="files">{(d.files as string[]).map((f) => <li key={f}>{f}</li>)}</ul></>}
          </>
        ) : (
          <>
            <label>File</label><div className="text">{d.file}{d.sheet ? ` · sheet ${d.sheet}` : ""}{d.focus ? ` · about ${d.focus}` : ""}</div>
            {d.origin && <><label>Source</label><div className="text">{d.origin}</div></>}
            {d.filter && <><label>Filter</label><pre className="code">{d.filter}</pre></>}
            {rows.length > 0 && (
              <>
                <label>{d.kind === "document" ? "Passages" : "Rows"} · {d.row_count}{d.truncated ? " (capped)" : ""}</label>
                <div className="mini-table"><table>
                  <thead><tr>{cols.map((x) => <th key={x}>{x}</th>)}</tr></thead>
                  <tbody>{rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>)}</tbody>
                </table></div>
              </>
            )}
          </>
        )}
        {d.error && <div className="error-note">{d.error}</div>}
      </div>
    );
  }
  if (c.node === "memory") {
    return (
      <div className="detail">
        <label>Memory search</label><div className="text">The CX agent searched your saved preferences before answering. Foundry also schedules an update, so anything new you said is saved shortly after the chat goes quiet.</div>
        <label>Reference</label><Json value={c.detail} />
      </div>
    );
  }
  return (
    <div className="detail">
      <label>Arguments</label><Json value={c.args} />
      {c.detail?.result !== undefined && <><label>Result</label><Json value={c.detail.result} /></>}
    </div>
  );
}

function CallRow({ c, running }: { c: Call; running: boolean }) {
  const [open, setOpen] = useState(false);
  const live = c.end === undefined && running;
  const aborted = c.end === undefined && !running;
  const failed = c.ok === false || aborted;
  return (
    <div className={`call depth${c.depth}`} style={{ ["--c" as string]: NODES[c.node].color }}>
      <button className="call-head" onClick={() => setOpen(!open)}>
        <ChevronRight size={14} className={`chev ${open ? "open" : ""}`} />
        <span className={`status ${live ? "live" : failed ? "bad" : "ok"}`} />
        <span className="call-main">
          <span className="call-title">{callTitle(c)}{c.caller !== "user" && <em> from {c.caller === "cx" ? "CX" : c.caller}</em>}</span>
          <span className="call-sub">{callSummary(c)}</span>
        </span>
        <span className="call-time">{live ? "running" : c.seconds !== undefined ? fmtSeconds(c.seconds) : "–"}</span>
      </button>
      {open && <Detail c={c} />}
    </div>
  );
}

function ActivityPane({ state, calls }: { state: State; calls: Call[] }) {
  const turns = [...new Set(calls.map((c) => c.turn))];
  if (calls.length === 0) return <div className="placeholder"><Activity size={20} /><p>Calls appear here as agents work.</p></div>;
  return (
    <div className="activity">
      {turns.map((t) => {
        const q = state.messages.find((m) => m.role === "user" && m.turn === t)?.text ?? "";
        return (
          <div key={t} className="turn-group">
            <div className="turn-head">Turn {t}<span>{q}</span></div>
            {calls.filter((c) => c.turn === t).map((c) => <CallRow key={c.id} c={c} running={state.running && t === state.turn} />)}
          </div>
        );
      })}
    </div>
  );
}

function logLines(events: HubEvent[], logs: State["logs"]) {
  const lines: { ts: number; level: string; msg: string }[] = logs.map((l) => ({ ts: l.ts, level: l.level, msg: l.msg }));
  for (const e of events) {
    const n = nodeOf(e);
    const who = e.type.startsWith("tool") ? `${e.server}.${e.tool}` : n;
    if (e.type === "agent_start") lines.push({ ts: e.ts, level: "info", msg: `agent.start   ${who} ← ${e.caller}  ${(e.ask ?? "").replace(/\s+/g, " ").slice(0, 110)}` });
    else if (e.type === "agent_end") lines.push({ ts: e.ts, level: e.ok === false ? "error" : "info", msg: `agent.end     ${who}  ${e.ok === false ? "FAILED" : "ok"}  ${e.seconds ?? "?"}s${e.input_tokens ? `  tokens in=${e.input_tokens} out=${e.output_tokens}` : ""}` });
    else if (e.type === "tool_start") lines.push({ ts: e.ts, level: "debug", msg: `tool.call     ${who}  caller=${e.caller}${e.case_id ? `  case=${e.case_id}` : ""}` });
    else if (e.type === "stage_start") lines.push({ ts: e.ts, level: "info", msg: `stage.start   ${e.harness}.${e.stage}  run=${e.run_id}` });
    else if (e.type === "stage_end") lines.push({ ts: e.ts, level: e.ok === false ? "error" : "info", msg: `stage.end     ${e.harness}.${e.stage}  ${e.ok === false ? "FAILED" : "ok"}  ${e.seconds ?? "?"}s${e.summary ? `  ${e.summary}` : ""}` });
    else lines.push({ ts: e.ts, level: e.ok === false ? "error" : "info", msg: `tool.done     ${who}  ${e.ok === false ? "FAILED" : "ok"}${e.summary && e.summary !== "ok" ? `  ${e.summary}` : ""}  ${e.seconds ?? "?"}s` });
  }
  return lines.sort((a, b) => a.ts - b.ts);
}

function LogsPane({ events, logs }: { events: HubEvent[]; logs: State["logs"] }) {
  const lines = useMemo(() => logLines(events, logs), [events, logs]);
  const end = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [lines.length]);
  if (!lines.length) return <div className="placeholder"><ScrollText size={20} /><p>Logs stream here while a request runs.</p></div>;
  const copy = async () => {
    await navigator.clipboard.writeText(lines.map((l) => `${fmtClock(l.ts)} ${l.level.toUpperCase()} ${l.msg}`).join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="console">
      <button className="copy" onClick={copy} title="Copy logs">{copied ? <Check size={14} /> : <Copy size={14} />}</button>
      {lines.map((l, i) => (
        <div key={i} className={`line ${l.level}`}><time>{fmtClock(l.ts)}</time><b>{l.level}</b><span>{l.msg}</span></div>
      ))}
      <div ref={end} />
    </div>
  );
}

function CasePane({ caseId, running }: { caseId: string | null; running: boolean }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    if (!caseId) {
      setData(null);
      return;
    }
    let live = true;
    const load = () => fetchCase(caseId).then((d) => live && d && setData(d));
    load();
    if (!running) return () => { live = false; };
    const t = setInterval(load, 3000);
    return () => { live = false; clearInterval(t); };
  }, [caseId, running]);
  if (!caseId || !data) return <div className="placeholder"><FolderOpen size={20} /><p>The case file (request, findings, report) shows here once a case is opened.</p></div>;
  return (
    <div className="case">
      <div className="case-head"><b>{data.case_id}</b><span>{data.findings?.length ?? 0} findings · {data.reports?.length ?? 0} reports</span></div>
      <label>Confirmed request</label>
      <p className="text">{data.request}</p>
      {data.findings?.length > 0 && <label>Findings</label>}
      {data.findings?.map((f: any) => (
        <div key={f.n} className="finding">
          <div><span className={`pill ${f.agent}`}>{f.agent}</span><strong>{f.title}</strong></div>
          <p>{f.finding}</p>
        </div>
      ))}
      {data.reports?.length > 0 && <label>Reports</label>}
      {data.reports?.map((r: any) => <ReportCardView key={r.n} caseId={data.case_id} report={r} compact />)}
    </div>
  );
}

export function Telemetry({ state, tab, setTab }: { state: State; tab: Tab; setTab: (t: Tab) => void }) {
  const calls = useMemo(() => buildCalls(state.events), [state.events]);
  const latest = useMemo(() => calls.filter((c) => c.turn === state.turn), [calls, state.turn]);
  const errors = state.events.filter((e) => e.ok === false).length + state.logs.filter((l) => l.level === "error").length;
  return (
    <div className="telemetry">
      <Stats calls={latest} running={state.running} />
      <div className="tabs">
        {([["activity", "Activity"], ["logs", "Logs"], ["case", "Case"], ["memory", "Memory"]] as [Tab, string][]).map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
            {label}{k === "logs" && errors > 0 && <CircleAlert size={12} className="err" />}
          </button>
        ))}
      </div>
      <div className="pane">
        {tab === "activity" && <Boundary label="Activity"><ActivityPane state={state} calls={calls} /></Boundary>}
        {tab === "logs" && <Boundary label="Logs"><LogsPane events={state.events} logs={state.logs} /></Boundary>}
        {tab === "case" && <Boundary label="The case file"><CasePane caseId={state.caseId} running={state.running} /></Boundary>}
        {tab === "memory" && <Boundary label="Memory"><MemoryPane tick={state.memoryTick} /></Boundary>}
      </div>
    </div>
  );
}

export type { Tab };
