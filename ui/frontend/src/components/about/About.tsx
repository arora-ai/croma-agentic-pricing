import { Activity, ArrowRight, Calculator, Download, ExternalLink, FileDown, FileText, GitBranch, MessageCircleQuestion, Network, Scale, ShieldCheck, Users, Workflow, X } from "lucide-react";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import type { State } from "../../store";
import { ArchitectureDiagram } from "./ArchitectureDiagram";
import { ComponentReference } from "./ComponentReference";
import "./about.css";

// The sample report is a static file shipped with the app (made by tests/sanitise_sample_report.py), under /assets so that it is also reachable through the API gateway.
const SAMPLE = "/assets/sample/weekly-commercial-pulse";
const THUMBS = [
  { src: `${SAMPLE}-p1.png`, alt: "Page 1 of the sample report: the bottom line and the decisions" },
  { src: `${SAMPLE}-p3.png`, alt: "Page 3 of the sample report: a channel bridge chart and table" },
  { src: `${SAMPLE}-p8.png`, alt: "Page 8 of the sample report: next-week actions with owners" },
];

type Who = "code" | "worker" | "data" | "agent";
const WHO: Record<Who, { label: string; color: string }> = {
  code: { label: "Code", color: "#475569" },
  worker: { label: "Model worker", color: "#6B4FBB" },
  data: { label: "Data and SharePoint agents", color: "#B45309" },
  agent: { label: "Agent", color: "#BE185D" },
};

interface Step { who: Who; name: string; note: string }
interface Lane { id: string; name: string; tag: string; blurb: string; steps: Step[]; foot?: string }

const ANALYSIS_STEPS: Step[] = [
  { who: "worker", name: "Plan", note: "The Scoper writes a typed plan: questions, steps, materiality" },
  { who: "data", name: "Get data", note: "Governed questions in parallel to each source the plan names (Databricks, and SharePoint when connected), one ledger result each; plans can join the two" },
  { who: "code", name: "Compute", note: "Rate versus mix, price versus volume versus mix, contribution, reconcile" },
];
const CONCLUDE_STEPS: Step[] = [
  { who: "worker", name: "Conclude", note: "The Analyst writes using only the engine's figures" },
  { who: "code", name: "Verify figures", note: "Every number traced to the ledger; untraceable text removed" },
  { who: "worker", name: "Challenge", note: "The Critic accepts, asks for a revision, or asks for one follow-up round" },
  { who: "code", name: "Deliver", note: "The brief with its evidence line; findings saved to the case" },
];

const LANES: Lane[] = [
  {
    id: "lookup", name: "Lookup", tag: "fast path", blurb: "One figure, with the basis it used.",
    steps: [
      { who: "agent", name: "Lookup agent", note: "States how it read the question" },
      { who: "data", name: "Ask once", note: "One governed question through the Data agent" },
      { who: "code", name: "Answer", note: "The figure and one line on the basis" },
    ],
  },
  { id: "analysis", name: "Analysis", tag: "harness", blurb: "What moved, and by how much.", steps: [...ANALYSIS_STEPS, ...CONCLUDE_STEPS] },
  {
    id: "research", name: "Research", tag: "harness", blurb: "Why it moved, and which explanations hold up.",
    steps: [...ANALYSIS_STEPS, { who: "code", name: "Test hypotheses", note: "Rules over the computed figures: supported, ruled out or inconclusive" }, ...CONCLUDE_STEPS],
  },
  {
    id: "reporting", name: "Reporting", tag: "report team", blurb: "A designed Word and HTML report.",
    steps: [
      { who: "agent", name: "Plan", note: "The Planner designs sections and data requests" },
      { who: "data", name: "Gather", note: "The Data agent answers the requests in parallel" },
      { who: "agent", name: "Think", note: "Insight decomposes and ranks, with the analytics engine as a tool" },
      { who: "agent", name: "Recommend", note: "The Strategist writes sized actions with owners" },
      { who: "agent", name: "Write", note: "Writers draft each section from the evidence" },
      { who: "agent", name: "Review", note: "The Reviewer critiques; repair rounds if needed" },
      { who: "code", name: "Audit and publish", note: "The studio checks every figure and renders Word and HTML with charts" },
    ],
    foot: "The editor model still chooses the order of these stages; the studio's checks are code. The next step is to run Reporting on the harness runtime too.",
  },
];

const FLOW = [
  ["Ask and sign in", "The browser sends the question with the user's Entra ID token."],
  ["Front gateway", "API Management checks the token, applies the user's rate limits and quotas, and keeps the stream open."],
  ["Chat API", "Takes the caller's identity only from the gateway and keeps that user's history and memory scope."],
  ["Plan check, then the journey", "The CX agent restates the question, asks only what changes the answer, shows its plan and waits for \"go ahead\" or a change. Then it calls Lookup, Analysis, Research or Reporting."],
  ["Tool gateway into the hub", "The call reaches the hub's agent bridge through API Management: allow-listed routes, a revocable key, a trace per tool."],
  ["Governed data", "Inside a harness, each question goes to the source the plan names: through the Data agent, the data tool and the data gateway to Genie (SQL on Unity Catalog tables), or through the SharePoint agent and tools to a file in the library. Either way the result is stored in the evidence ledger."],
  ["Answer with its evidence", "Code computes and traces every figure to the ledger, the Critic challenges, and the brief streams back with its evidence line. Reports open as Word and HTML."],
  ["Everything is traced", "Gateway logs per user and per tool, hub events and harness stages feed Log Analytics and the admin view."],
];

const NEW_IN_V2: { icon: ReactNode; title: string; text: string }[] = [
  { icon: <Workflow size={18} />, title: "Harnesses for Analysis and Research", text: "Code owns the stages, the arithmetic and the checks; three model workers reason inside them. Every stage is saved, so a run resumes where it stopped." },
  { icon: <Calculator size={18} />, title: "An exact analytics engine", text: "Rate versus mix, price versus volume versus mix, contribution, target gap, concentration, trend. The parts always sum to the total." },
  { icon: <ShieldCheck size={18} />, title: "Every figure traced", text: "Numbers in the text are checked against an evidence ledger. What cannot be traced is removed, not shown." },
  { icon: <Scale size={18} />, title: "Guardrails from live runs", text: "Ratios are reconciled with the data space's own figure, the standing scope and units go into every question, and results are analysed in the unit you are shown." },
  { icon: <MessageCircleQuestion size={18} />, title: "A plan check before anything runs", text: "The CX agent shows exact dates, scope, defaults and assumptions, and waits for \"go ahead\" or your changes." },
  { icon: <Network size={18} />, title: "Three API gateways", text: "Front, tool and data gateways on Azure API Management: Entra sign-in, per-user limits, allow-listed routes, per-tool traces, and the hub's own key kept out of the agents' hands." },
  { icon: <Users size={18} />, title: "Per-user identity", text: "Conversation history and the memory scope follow the signed-in user." },
  { icon: <Activity size={18} />, title: "See inside the run", text: "A stage stepper, the agent network, the full brief and a live log show what is happening and why." },
  { icon: <GitBranch size={18} />, title: "v1 stays untouched", text: "A name prefix gives v2 its own agents, memory and connections, so both versions run side by side." },
];

const NEXT = [
  "Reporting on the harness runtime. A full report takes 35 to 60 minutes today because a model orders the stages.",
  "A Microsoft Teams channel and scheduled nudges that open a conversation with a finding.",
  "Evaluations: golden questions that run after every change, and certified Genie measures for the metrics that matter.",
  "Durable state, so runs, cases and the evidence ledger survive a restart (they are in memory today).",
  "The signed-in user's own data permissions passed through to Databricks.",
  "Private networking and Key Vault for the Croma tenant.",
];

function SampleViewer({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="viewer" role="dialog" aria-label="Sample report">
      <div className="viewer-bar">
        <FileText size={16} />
        <strong>Sample report: Weekly commercial pulse</strong>
        <span className="spacer" />
        <a className="ghost sm" href={`${SAMPLE}.docx`} download><Download size={14} />Download Word</a>
        <a className="ghost sm" href={`${SAMPLE}.html`} target="_blank" rel="noreferrer"><ExternalLink size={14} />New tab</a>
        <button className="ghost sm icon" onClick={onClose} aria-label="Close"><X size={15} /></button>
      </div>
      <iframe title="Sample report" src={`${SAMPLE}.html`} />
    </div>
  );
}

const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

export function About({ health, onOpenChat }: { health: State["health"]; onOpenChat: () => void }) {
  const [viewer, setViewer] = useState(false);
  return (
    <div className="about">
      <div className="about-inner">
        <header className="about-hero">
          <span className="eyebrow">About this app</span>
          <h1>Croma Pricing Assistant<span className="ver">v2 · harness edition</span></h1>
          <p className="lead">
            Ask a business question about sales, margin, markdown or plan targets in plain language. A conversational agent confirms what you mean and shows its plan.
            Then specialists fetch governed data, compute the answer with exact methods and check every figure before you see it.
          </p>
          <ul className="chips">
            <li>Microsoft Foundry agents</li><li>Azure Databricks data</li><li>Azure API Management gateways</li><li>Synthetic data in this demo</li>
          </ul>
          {health && (
            <p className="connected">
              <i className={health.hub ? "up" : "down"} />
              <span>This copy is connected to Foundry project <b>{health.project}</b>, CX agent <b>{health.cx_agent}</b>{health.genie_backend ? <>, data backend <b>{health.genie_backend}</b></> : null}.</span>
            </p>
          )}
          <div className="hero-actions">
            <button className="primary" onClick={onOpenChat}>Start a chat<ArrowRight size={15} /></button>
            <button className="ghost" onClick={() => window.print()} title="Opens the print dialog: choose Save as PDF, paper A4, and switch off Headers and footers"><FileDown size={15} />Save as PDF</button>
            <nav className="jump" aria-label="On this page">
              <button onClick={() => jump("about-sample")}>Sample report</button>
              <button onClick={() => jump("about-arch")}>Architecture</button>
              <button onClick={() => jump("about-journeys")}>Journeys</button>
              <button onClick={() => jump("about-new")}>What is new in v2</button>
              <button onClick={() => jump("about-next")}>What is next</button>
            </nav>
          </div>
        </header>

        <section id="about-sample" className="sample">
          <div>
            <h2>See a sample report</h2>
            <p>
              A real report from a live run on synthetic data: the weekly commercial pulse for the CEO office, 5 to 11 October 2026 against the weekday-aligned week of 2025.
              Fifteen pages with a bottom line, decisions, four charts, thirteen tables, actions with owners and an evidence appendix.
              All 135 figures in it were traced to the data, and the independent reviewer passed it. The queries behind the figures are left out of this copy.
            </p>
            <ul className="facts"><li>15 pages</li><li>135 of 135 figures traced</li><li>Reviewer: pass</li><li>Word and HTML</li></ul>
            <div className="row">
              <button className="primary" onClick={() => setViewer(true)}><FileText size={15} />Open sample report</button>
              <a className="ghost" href={`${SAMPLE}.docx`} download><Download size={15} />Download Word</a>
              <a className="ghost" href={`${SAMPLE}.html`} target="_blank" rel="noreferrer"><ExternalLink size={15} />Open in a new tab</a>
            </div>
          </div>
          <div className="thumbs">
            {THUMBS.map((t) => <button key={t.src} className="thumb" onClick={() => setViewer(true)} aria-label="Open the sample report"><img src={t.src} alt={t.alt} /></button>)}
          </div>
        </section>

        <section id="about-arch">
          <h2>How it is built</h2>
          <p className="sub">
            One question travels down through three gateways to the data and back, with the plan check up front and the evidence checks on the way back.{" "}
            <span className="screen-only">Click any box for details.</span>
            <span className="print-only">Every box is described in the component reference at the end.</span>
          </p>
          <ul className="legend" aria-label="How to read the diagram">
            <li><i className="sw solid" />built</li>
            <li><i className="sw dashed" />planned or outside this build</li>
            <li><b className="tag dark">v2</b>new or changed in v2</li>
            <li><b className="tag purple">HARNESS</b>code-owned stages</li>
            <li><b className="tag teal">AI gateway</b>Azure API Management</li>
            <li><i className="num">1</i>numbers follow one question</li>
          </ul>
          <ArchitectureDiagram />
          <h3 className="print-only flow-title">A question, step by step <span>the numbers match the arrows in the diagram</span></h3>
          <ol className="flow">
            {FLOW.map(([title, text], i) => <li key={title}><i className="num">{i + 1}</i><div><b>{title}</b><span>{text}</span></div></li>)}
          </ol>
        </section>

        <section id="about-journeys">
          <h2>Each journey, and who does each step</h2>
          <p className="sub">Code does the arithmetic and every check. Models do the reasoning inside stages that code defines.</p>
          <ul className="legend" aria-label="Who does a step">
            {(Object.keys(WHO) as Who[]).map((w) => <li key={w}><i className="sw fill" style={{ background: WHO[w].color }} />{WHO[w].label}</li>)}
          </ul>
          <div className="lanes">
            {LANES.map((lane) => (
              <div className="lane" key={lane.id}>
                <div className="lane-head"><b>{lane.name}</b><span className="lane-tag">{lane.tag}</span><p>{lane.blurb}</p></div>
                <ol className="steps">
                  {lane.steps.map((s, i) => (
                    <li key={`${lane.id}-${i}`} style={{ "--c": WHO[s.who].color } as CSSProperties}><b>{s.name}</b><span>{s.note}</span></li>
                  ))}
                </ol>
                {lane.foot && <p className="lane-foot">{lane.foot}</p>}
              </div>
            ))}
          </div>
        </section>

        <section id="about-new">
          <h2>What is new in v2</h2>
          <div className="new-grid">
            {NEW_IN_V2.map((c) => <div className="new-card" key={c.title}><span className="new-icon">{c.icon}</span><b>{c.title}</b><p>{c.text}</p></div>)}
          </div>
        </section>

        <section id="about-next">
          <h2>What is planned, and what is not built yet</h2>
          <ul className="next">{NEXT.map((n) => <li key={n}>{n}</li>)}</ul>
        </section>

        <ComponentReference />

        <footer className="about-foot">
          Read more in the repository: <code>docs/V2.md</code> (the design), <code>docs/APIM.md</code> (the gateways), <code>docs/architecture.md</code> (diagrams) and <code>docs/DEPLOYMENT.md</code> (the runbook).
          Every figure in this demo comes from synthetic data.
        </footer>
      </div>
      {viewer && <SampleViewer onClose={() => setViewer(false)} />}
    </div>
  );
}
