import { ArrowUp, Check, Copy, Download, FileText, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { buildCalls, fmtSeconds, isReport } from "../lib";
import type { State } from "../store";
import { NODES, type Call, type Message } from "../types";
import { Markdown } from "./Markdown";
import { ReportCard, ReportProgress, isReportRun } from "./ReportCard";

// Starter prompts shown on an empty chat. Dates are relative on purpose so they work against any data set; edit freely.
const SUGGESTIONS = [
  { title: "Why did store margin move?", text: "Why did merchandise margin % for Stores move last month versus the month before? Show the biggest category drivers." },
  { title: "Tracking against AOP", text: "How are we tracking against AOP month to date? Show NMV achievement by channel and the categories furthest behind." },
  { title: "Zones gaining or losing share", text: "Which zones are gaining or losing share in store volume, comparing the latest full week with the same week last year?" },
  { title: "Weekly performance report", text: "Create a standard weekly performance report for the CEO office for the latest full week versus the same week last year, enterprise level, with channel, category and zone drivers and recommended actions." },
  { title: "Category deep-dive report", text: "I want a deep-dive report on one category for last month versus the same month last year: where the gap to plan sits by zone, what is driving mix and price, and what to do about it." },
];

/** One line saying who is working right now, from the in-flight calls. */
function workingLine(calls: Call[]): string {
  const open = calls.filter((c) => c.end === undefined);
  const last = [...open].reverse().find((c) => c.node !== "cx") ?? open[open.length - 1];
  if (!last) return "Understanding your request";
  if (last.node === "genie") return "Querying the data";
  if (last.node === "memory") return "Recalling your preferences";
  if (last.node === "context") return "Updating the case notes";
  if (last.node === "studio") return "Report studio is working on the evidence";
  if (last.node === "data") return "Data agent is fetching figures";
  if (last.node === "cx") return "CX agent is reviewing your request";
  if (last.node === "reporting") return "Editor is running the report team";
  return `${NODES[last.node].label} agent is working`;
}

function ReportActions({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "croma-report.md";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="report-actions">
      <span className="report-tag"><FileText size={13} /> Report</span>
      <button className="ghost sm" onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "Copied" : "Copy"}</button>
      <button className="ghost sm" onClick={download}><Download size={14} />Download .md</button>
    </div>
  );
}

function Bubble({ m, calls, running }: { m: Message; calls: Call[]; running: boolean }) {
  if (m.role === "user") return <div className="msg user"><div className="bubble">{m.text}</div></div>;
  const waiting = m.status === "streaming" && !m.text;
  const report = m.status === "done" && isReport(m.text);
  const published = calls.find((c) => c.tool === "render_report" && c.end !== undefined && c.ok !== false && c.caseId);
  return (
    <div className="msg assistant">
      <div className="avatar"><Sparkles size={15} /></div>
      <div className="body">
        {waiting && (
          <div className="working">
            <span className="dots"><i /><i /><i /></span>
            <span>{workingLine(calls)}</span>
          </div>
        )}
        {m.status === "streaming" && isReportRun(calls) && <ReportProgress calls={calls} />}
        {m.text && (m.status === "error" ? <div className="error-note">{m.text}</div> : <>{report && <ReportActions text={m.text} />}<Markdown>{m.text}</Markdown></>)}
        {published && <ReportCard caseId={published.caseId!} n={published.detail?.result?.report_number} />}
        {m.status === "streaming" && m.text && running && <span className="caret" />}
        {m.status === "done" && m.seconds !== undefined && (
          <div className="meta">
            {fmtSeconds(m.seconds)} · {calls.filter((c) => c.kind === "agent" && c.node !== "cx").length} agent calls · {calls.filter((c) => c.node === "genie").length} data queries
            {calls.some((c) => c.node === "memory") && " · used your saved preferences"}
          </div>
        )}
      </div>
    </div>
  );
}

export function Chat({ state, onSend }: { state: State; onSend: (t: string) => void }) {
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const callsByTurn = useMemo(() => {
    const map = new Map<number, Call[]>();
    for (const c of buildCalls(state.events)) map.set(c.turn, [...(map.get(c.turn) ?? []), c]);
    return map;
  }, [state.events]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [state.messages, state.events.length]);

  const submit = () => {
    if (!draft.trim() || state.running) return;
    onSend(draft);
    setDraft("");
  };

  return (
    <section className="chat">
      <div className="scroll" ref={scroller}>
        {state.loading ? (
          <div className="empty"><p>Loading conversation…</p></div>
        ) : state.messages.length === 0 ? (
          <div className="empty">
            <div className="mark"><Sparkles size={22} /></div>
            <h1>How can I help with pricing and profit?</h1>
            <p>Ask a business question. I'll confirm the details, then bring in the right specialists.</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s.title} className="suggestion" onClick={() => onSend(s.text)}>
                  <strong>{s.title}</strong>
                  <span>{s.text}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="thread">
            {state.messages.map((m) => <Bubble key={m.id} m={m} calls={callsByTurn.get(m.turn) ?? []} running={state.running} />)}
          </div>
        )}
      </div>
      <div className="composer-wrap">
        <div className="composer">
          <textarea
            rows={1}
            value={draft}
            placeholder={state.running ? "Working…" : "Ask about margin, sales, prices, schemes…"}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button className="send" onClick={submit} disabled={!draft.trim() || state.running} aria-label="Send"><ArrowUp size={18} /></button>
        </div>
        <div className="hint">Answers come from Croma's governed data through specialist agents on Microsoft Foundry. Synthetic data.</div>
      </div>
    </section>
  );
}
