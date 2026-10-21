import { Download, ExternalLink, FileText, ShieldCheck, X } from "lucide-react";
import { useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { fetchCase, reportUrl } from "../api";
import { apiFetch, authActive } from "../auth";
import { TEAM, type Call } from "../types";
import { Markdown } from "./Markdown";

interface Report {
  n: number;
  title: string;
  markdown: string;
  artifact?: { n: number; docx: string; html: string; audit?: { numbers_checked: number; traced: number; untraced: number; pass: boolean }; sections?: number; evidence?: number };
}

// The report files are behind the same sign-in as the rest of the API. A plain link or iframe cannot send the access token, so with sign-in on
// the file is fetched with it and handed to the browser as a local blob. With sign-in off these are ordinary links, as before.
async function fetchBlobUrl(url: string): Promise<string | null> {
  const res = await apiFetch(url);
  return res.ok ? URL.createObjectURL(await res.blob()) : null;
}

function FileLink({ url, filename, className, newTab, children }: { url: string; filename: string; className: string; newTab?: boolean; children: ReactNode }) {
  if (!authActive) return <a className={className} href={url} {...(newTab ? { target: "_blank", rel: "noreferrer" } : {})}>{children}</a>;
  const open = async (e: MouseEvent) => {
    e.preventDefault();
    const tab = newTab ? window.open("", "_blank") : null; // opened now, while the click still counts as the user's, and pointed at the file below
    const blobUrl = await fetchBlobUrl(url);
    if (!blobUrl) { tab?.close(); return; }
    if (tab) { tab.location.href = blobUrl; return; }
    const link = document.createElement("a");
    link.href = blobUrl;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
  };
  return <a className={className} href={url} onClick={open}>{children}</a>;
}

function ReportFrame({ url, title }: { url: string; title: string }) {
  const [src, setSrc] = useState<string | null>(authActive ? null : url);
  useEffect(() => {
    if (!authActive) return;
    let live = true;
    let made: string | null = null;
    fetchBlobUrl(url).then((b) => {
      made = b;
      if (live) setSrc(b);
      else if (b) URL.revokeObjectURL(b);
    });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [url]);
  return src ? <iframe title={title} src={src} /> : <div className="viewer-wait">Loading the report…</div>;
}

function Viewer({ caseId, report, onClose }: { caseId: string; report: Report; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const n = report.artifact?.n ?? report.n;
  return (
    <div className="viewer" role="dialog" aria-label={report.title}>
      <div className="viewer-bar">
        <FileText size={16} />
        <strong>{report.title}</strong>
        <span className="spacer" />
        <FileLink className="ghost sm" url={reportUrl(caseId, n, "docx")} filename={`${caseId}-report-${n}.docx`}><Download size={14} />Download Word</FileLink>
        <FileLink className="ghost sm" url={reportUrl(caseId, n, "html")} filename={`${caseId}-report-${n}.html`} newTab><ExternalLink size={14} />New tab</FileLink>
        <button className="ghost sm icon" onClick={onClose} aria-label="Close"><X size={15} /></button>
      </div>
      <ReportFrame url={reportUrl(caseId, n, "html")} title={report.title} />
    </div>
  );
}

export function ReportCardView({ caseId, report, compact }: { caseId: string; report: Report; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const a = report.artifact;
  if (!a) return <div className="report-card"><Markdown>{report.markdown}</Markdown></div>;
  return (
    <div className="rcard">
      <div className="rcard-head">
        <span className="rcard-icon"><FileText size={18} /></span>
        <div className="rcard-title">
          <strong>{report.title}</strong>
          <span>
            {a.sections ?? "-"} sections · {a.evidence ?? "-"} evidence items
            {a.audit && (
              <em className={a.audit.pass ? "ok" : "bad"}><ShieldCheck size={12} />{a.audit.traced} of {a.audit.numbers_checked} figures traced</em>
            )}
          </span>
        </div>
      </div>
      {!compact && <div className="rcard-sum"><Markdown>{report.markdown}</Markdown></div>}
      <div className="rcard-actions">
        <button className="primary sm" onClick={() => setOpen(true)}><FileText size={14} />Open report</button>
        <FileLink className="ghost sm" url={reportUrl(caseId, a.n, "docx")} filename={`${caseId}-report-${a.n}.docx`}><Download size={14} />Download Word</FileLink>
      </div>
      {open && <Viewer caseId={caseId} report={report} onClose={() => setOpen(false)} />}
    </div>
  );
}

/** Fetches the case and shows the report that a turn published. */
export function ReportCard({ caseId, n }: { caseId: string; n?: number }) {
  const [report, setReport] = useState<Report | null>(null);
  useEffect(() => {
    let live = true;
    fetchCase(caseId).then((c) => {
      const list: Report[] = (c?.reports ?? []).filter((r: Report) => r.artifact);
      const found = list.find((r) => r.artifact?.n === n) ?? list[list.length - 1];
      if (live && found) setReport(found);
    });
    return () => { live = false; };
  }, [caseId, n]);
  return report ? <ReportCardView caseId={caseId} report={report} /> : null;
}

const STAGES: { key: string; label: string; match: (c: Call) => boolean }[] = [
  { key: "plan", label: "Plan", match: (c) => c.node === "planner" && c.kind === "agent" },
  { key: "gather", label: "Gather evidence", match: (c) => c.node === "genie" },
  { key: "think", label: "Find drivers", match: (c) => c.node === "insight" && c.kind === "agent" },
  { key: "act", label: "Recommend", match: (c) => c.node === "strategist" && c.kind === "agent" },
  { key: "write", label: "Write", match: (c) => c.node === "writer" && c.kind === "agent" },
  { key: "check", label: "Review and audit", match: (c) => (c.node === "reviewer" && c.kind === "agent") || c.tool === "audit_report" },
  { key: "publish", label: "Publish", match: (c) => c.tool === "render_report" },
];

export function isReportRun(calls: Call[]): boolean {
  return calls.some((c) => TEAM.includes(c.node) && c.kind === "agent") || calls.some((c) => c.node === "reporting" && c.kind === "agent");
}

/** The stages of a report run, lit from the calls seen so far. */
export function ReportProgress({ calls }: { calls: Call[] }) {
  const rows = STAGES.map((s) => {
    const mine = calls.filter(s.match);
    const active = mine.some((c) => c.end === undefined);
    const state = active ? "active" : mine.length > 0 ? "done" : "todo";
    return { ...s, state, count: mine.length };
  });
  const lastTouched = rows.reduce((acc, r, i) => (r.state !== "todo" ? i : acc), -1);
  return (
    <ol className="rprogress" aria-label="Report progress">
      {rows.map((r, i) => {
        const state = r.state === "todo" && i < lastTouched ? "done" : r.state;
        return (
          <li key={r.key} className={state}>
            <i />
            <span>{r.label}{r.key === "gather" && r.count > 0 ? ` (${r.count})` : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}
