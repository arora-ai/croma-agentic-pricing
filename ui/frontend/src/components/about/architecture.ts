// The architecture the About page draws: every box with what it does and where it lives, plus the arithmetic that places them. Data first, drawing second
// (ArchitectureDiagram.tsx), so the picture is corrected by editing this file. A solid box is built and verified in v2; a dashed box is planned or outside this build.

export interface Rect { x: number; y: number; w: number; h: number }

export type Tone =
  | "user" | "exp" | "expBox" | "gateway" | "chip" | "foundry" | "agent" | "dataAgent" | "worker" | "hub" | "hubBox" | "harness"
  | "dbx" | "dbxBox" | "ext" | "trust" | "trustBox" | "pilot";

export const TONES: Record<Tone, { fill: string; stroke: string; text: string }> = {
  user: { fill: "#0F6CBD", stroke: "#0F6CBD", text: "#FFFFFF" },
  exp: { fill: "#EAF2FB", stroke: "#0F6CBD", text: "#0B4F8A" },
  expBox: { fill: "#FFFFFF", stroke: "#0F6CBD", text: "#1F2933" },
  gateway: { fill: "#E7F6F3", stroke: "#0E8C80", text: "#0A6B61" },
  chip: { fill: "#FFFFFF", stroke: "#7CC4BB", text: "#14544C" },
  foundry: { fill: "#F2EEFB", stroke: "#6B4FBB", text: "#4B3790" },
  agent: { fill: "#FFFFFF", stroke: "#6B4FBB", text: "#1F2933" },
  dataAgent: { fill: "#FFF7E6", stroke: "#B45309", text: "#1F2933" },
  worker: { fill: "#EDE7FA", stroke: "#6B4FBB", text: "#1F2933" },
  hub: { fill: "#F1F5F9", stroke: "#475569", text: "#334155" },
  hubBox: { fill: "#FFFFFF", stroke: "#475569", text: "#1F2933" },
  harness: { fill: "#FFFFFF", stroke: "#6B4FBB", text: "#1F2933" },
  dbx: { fill: "#EDF2F9", stroke: "#2B5797", text: "#1F3F73" },
  dbxBox: { fill: "#FFFFFF", stroke: "#2B5797", text: "#1F2933" },
  ext: { fill: "#FFFFFF", stroke: "#5B6573", text: "#1F2933" },
  trust: { fill: "#ECEBF6", stroke: "#2E2A6E", text: "#2E2A6E" },
  trustBox: { fill: "#FFFFFF", stroke: "#2E2A6E", text: "#1F2933" },
  pilot: { fill: "#F2F3F5", stroke: "#5B6573", text: "#3F4854" },
};

export interface Node {
  id: string;
  title: string;
  lines?: string[];
  tone: Tone;
  flex?: number;
  planned?: boolean;
  v2?: boolean; // new or changed in v2: drawn with a small "v2" marker
  badge?: string;
  tag?: string; // a small italic label in the corner (a planned box says "planned" unless this says otherwise)
  shape?: "box" | "cylinder" | "person" | "chip";
  detail?: string; // what it does (a chip has none: it is described by its panel)
  where?: string; // where it lives in the repository
}

export interface Row { h: number; nodes: Node[] }

export interface Panel {
  id: string;
  title: string;
  tone: Tone;
  rows: Row[];
  tag?: string; // a small label at the right of the header
  v2?: boolean;
  w?: number;
  center?: boolean;
  bare?: boolean; // no frame and no header: the node stands alone
  gapBefore?: number;
  detail?: string;
  where?: string;
}

export interface Flow { from: string; to: string; n?: number; label?: string; n2?: number; label2?: string; x?: number; dashed?: boolean; heads?: "both" | "to" }

export const WIDTH = 1240;
export const MAIN_X = 18;
export const MAIN_W = 876;
export const BAND_X = 930;
export const BAND_W = 292;
export const GAP = 34;
export const PAD = 12;
export const HEAD = 32;
const ROW_GAP = 10;
const COL_GAP = 10;

const chip = (id: string, title: string): Node => ({ id, title, tone: "chip", shape: "chip" });

// ------------------------------------------------------------------ the main stack, top to bottom

export const MAIN: Panel[] = [
  {
    id: "users", title: "", tone: "user", bare: true, w: 470, center: true,
    rows: [{ h: 46, nodes: [{ id: "users", title: "Business users", lines: ["category merchandisers · pricing and finance · CEO office"], tone: "user", shape: "person",
      detail: "The people who ask: category merchandisers, pricing and finance teams, the CEO office, zone and category heads. They ask in plain business language and never see agents, tools or SQL." }] }],
  },
  {
    id: "exp", title: "Experience layer", tone: "exp", gapBefore: 56,
    detail: "Where people reach the assistant. The chat web app is built; the Teams channel and scheduled nudges are planned and would reach the same CX agent through the same front gateway.",
    where: "ui/",
    rows: [{ h: 54, nodes: [
      { id: "web", title: "Chat web app", lines: ["sign-in · chat · stage stepper · admin view"], tone: "expBox", flex: 1.5, v2: true,
        detail: "A React single-page app served by the chat API. It streams the answer, shows the Plan / Get data / Compute / Conclude / Verify / Challenge / Deliver stepper and the full brief, opens reports, and has an admin view (agent network, activity, logs, case file, memory). Entra ID sign-in is optional and switched on at build time.", where: "ui/frontend, ui/backend" },
      { id: "teams", title: "Microsoft Teams", lines: ["same CX agent · cards"], tone: "expBox", planned: true,
        detail: "A Teams channel on Foundry that reaches the same CX agent, with adaptive cards for the plan check and for report links. Planned: easy on Foundry, not built yet." },
      { id: "nudges", title: "Nudges and schedules", lines: ["triggered runs · own identity"], tone: "expBox", planned: true,
        detail: "Scheduled or event-triggered runs under the system's own identity that open a conversation with a finding (\"Stores margin fell a point this week; want the drivers?\"). Planned." },
    ] }],
  },
  {
    id: "g1", title: "Front gateway · Azure API Management", tag: "AI gateway", v2: true, tone: "gateway",
    detail: "Azure API Management in front of the chat API. It checks the user's Entra ID token (or a subscription key for a channel), tells the chat API who the caller is, rate-limits per user (chat turns per minute, optional daily quota), keeps streamed answers unbuffered with keep-alives, and puts a correlation id on every call. Static web files load before sign-in.",
    where: "infra/apim, docs/APIM.md",
    rows: [{ h: 22, nodes: [chip("g1a", "Entra ID token check"), chip("g1b", "per-user limits and quotas"), chip("g1c", "streamed answers kept open"), chip("g1d", "correlation id on every call")] }],
  },
  {
    id: "api", title: "Chat API · container app", v2: true, tone: "hub",
    detail: "A FastAPI container app. It believes the identity headers only when they carry the gateway's shared key, keeps each signed-in user's conversation history and memory scope, talks to Foundry with its managed identity, and relays run progress, the case file and report files to the browser (the hub's key never reaches the browser).",
    where: "ui/backend, ui/Dockerfile",
    rows: [{ h: 22, nodes: [chip("api1", "identity only from the gateway"), chip("api2", "history per user"), chip("api3", "memory scope per user"), chip("api4", "relays runs and reports")] }],
  },
  {
    id: "foundry", title: "Microsoft Foundry · Agent Service", tone: "foundry",
    detail: "The agents. The CX agent talks to the user; Lookup, the Reporting editor and its team, the Data agent and the three harness workers are Foundry agents with their own instructions and tools. Models are chosen at deploy time: a conversational model for the front agents, a deep-reasoning model for the workers and the report team.",
    where: "agents/",
    rows: [
      { h: 62, nodes: [
        { id: "cx", title: "CX agent · the face of the system", lines: ["asks only what changes the answer", "shows the plan and waits for the go-ahead"], tone: "agent", badge: "PLAN CHECK", v2: true, flex: 2.4,
          detail: "Restates the question, asks only the few things whose answers would change the analysis, then shows the plan it will run (exact dates, scope and defaults, measure, cut, and every assumption it made) and waits for \"go ahead\" or a change. It routes to the right journey and relays the finished brief unchanged. It never queries data or produces a number itself.", where: "agents/prompts/cx.md" },
        { id: "memory", title: "Memory", lines: ["preferences per user"], tone: "agent", flex: 1, v2: true,
          detail: "A Foundry memory store holding preferences: units, the usual zone, how this user defines a term like \"category drivers\". Remembered preferences only set the defaults the CX proposes in its plan; they never skip the plan check. Scoped per user when the agents are deployed with the user-scoped memory option.", where: "agents/memory_store.py" },
      ] },
      { h: 66, nodes: [
        { id: "lookup", title: "Lookup agent", lines: ["fast path · one figure"], tone: "agent",
          detail: "The fast path for plain \"talk to my data\" questions: one question, one figure, with the basis it used. If the question needs explaining it hands back to the CX agent, which routes to Analysis.", where: "agents/prompts/lookup.md" },
        { id: "reporting", title: "Reporting agent · editor", lines: ["plans, gathers, writes, audits, publishes"], tone: "agent",
          detail: "The editor of a report team. It plans the report, gets evidence through the Data agent, directs the team, has the studio audit every number and publishes a designed Word and HTML report. Its stage order is still chosen by a model; moving it onto the harness runtime is the next step.", where: "agents/prompts/reporting.md" },
        { id: "team", title: "Report team", lines: ["Planner · Insight · Strategist", "Writer · Reviewer"], tone: "agent", flex: 1.3,
          detail: "Five agents on the deep-reasoning model. The Planner designs the report, Insight finds the drivers (with the analytics engine as a tool), the Strategist writes sized and owned actions, Writers draft sections, the Reviewer critiques independently. They work through the report studio, which keeps the evidence.", where: "agents/prompts/{planner,insight,strategist,writer,reviewer}.md" },
      ] },
      { h: 62, nodes: [
        { id: "data", title: "Data agent · the only door to data", lines: ["adds the standing scope and units · checks the SQL"], tone: "dataAgent", flex: 1.5, v2: true,
          detail: "The single path to data. It frames the question for Genie, adds the standing scope and the unit, calls the data tool, checks that the SQL applied the scope and returns the rows with their source. It does not analyse or recommend.", where: "agents/prompts/data.md" },
        { id: "workers", title: "Harness workers · no tools", lines: ["Scoper · Analyst · Critic", "one typed JSON message in, one out"], tone: "worker", badge: "MODEL", flex: 1.3, v2: true,
          detail: "Three Foundry agents with no tools. The harness runtime calls them with one JSON message and expects one typed JSON reply; a reply that does not fit the schema is sent back once with the exact problem. The Scoper plans, the Analyst writes only from engine figures, the Critic challenges.", where: "agents/prompts/{scoper,analyst,critic}.md · hub/harness/workers.py" },
      ] },
    ],
  },
  {
    id: "g2", title: "Tool gateway · Azure API Management", tag: "AI gateway", v2: true, tone: "gateway",
    detail: "Azure API Management between the agents and the hub's MCP servers. Only the four MCP routes are exposed; each consumer has its own revocable subscription key, and the agents never hold the hub's own key; every tool call is traced with its tool name. Foundry follows the connection's target, so the agents themselves needed no change.",
    where: "infra/apim, infra/create_hub_connection.ps1",
    rows: [{ h: 22, nodes: [chip("g2a", "MCP routes allow-listed"), chip("g2b", "per-tool traces"), chip("g2c", "revocable key per consumer"), chip("g2d", "agents never hold the hub key")] }],
  },
  {
    id: "hub", title: "Tools hub · container app · MCP servers and journey harnesses", tone: "hub",
    detail: "One container app with four MCP servers (data tool, agent bridge, case context, report studio) and the code that runs the Analysis and Research journeys as harnesses. State (cases, runs, the ledger) is in memory and can be exported and restored.",
    where: "hub/",
    rows: [
      { h: 70, nodes: [
        { id: "h_analysis", title: "Analysis harness", lines: ["what moved, and by how much", "plan → data → exact maths → write → verify → challenge"],         tone: "harness", badge: "HARNESS", v2: true,
          detail: "The journey for \"what happened and how big\". The Scoper plans, the Data agent fetches governed data, code computes exact decompositions (rate versus mix, price versus volume versus mix, contribution, target gap), the Analyst writes using only engine figures, code traces every figure, the Critic challenges, with at most one follow-up round.", where: "hub/harness/analysis.py" },
        { id: "h_research", title: "Research harness", lines: ["why it moved · is X true", "same stages, plus hypothesis rules decided by code"],         tone: "harness", badge: "HARNESS", v2: true,
          detail: "The journey for \"why\" and \"is it X or Y\". The same stages as Analysis, plus hypotheses with testable rules: code evaluates each rule over the computed figures and marks it supported, ruled out or inconclusive; the Analyst can only explain those verdicts.", where: "hub/harness/research.py" },
        { id: "h_report", title: "Reporting harness", lines: ["next: the report team on the same runtime"], tone: "harness", planned: true, badge: "NEXT",
          detail: "Planned. Reporting today is an editor-led team whose stages the Editor model orders; the studio already enforces evidence, the number audit and rendering in code. The next step is to make the report a third spec of the harness runtime: plan, evidence, insight, actions, one writer per section in a fixed order, review, render. That should cut the 35 to 60 minutes a full report takes and make it less fragile." },
      ] },
      { h: 62, nodes: [
        { id: "bridge", title: "Agent bridge", lines: ["ask_* tools start runs"], tone: "hubBox", v2: true,
          detail: "The MCP server that lets one agent call another. ask_analysis_agent and ask_research_agent start harness runs; ask_lookup_agent, ask_reporting_agent and ask_data_agent call Foundry agents; long calls run as background jobs that the caller keeps waiting on with wait_for_agent.", where: "hub/agent_bridge.py" },
        { id: "runtime", title: "Harness runtime", lines: ["stages · typed contracts", "token governor · resumable"],         tone: "hubBox", flex: 1.15, v2: true,
          detail: "Runs a harness as stages (scope, evidence, evaluate, conclude, verify, challenge, deliver). Each stage's output is saved so a run resumes where it stopped; replies are validated against pydantic schemas with one repair; a token governor keeps bursts under the model quota; every stage emits an event that the chat's stepper reads. It calls the workers and the Data agent in Foundry directly.", where: "hub/harness/" },
        { id: "engine", title: "Analytics engine", lines: ["exact maths · parts sum to the total"], tone: "hubBox", v2: true,
          detail: "Pure Python with no I/O: contribution, rate versus mix, price versus volume versus mix, target gap, concentration, trend, reconcile and materiality. The parts of every decomposition sum to the total exactly (property-tested). Also a tool for the report team's Insight agent.", where: "hub/studio/analytics.py" },
        { id: "ledger", title: "Evidence ledger and audit", lines: ["every figure traced to the data"], tone: "hubBox", flex: 1.15,
          detail: "Every data result (R), derived table (T) and calculation (D) has an id. The number audit traces every figure in a written text to the ledger and removes what it cannot trace: a wrong figure is worse than a missing sentence. Results keep the SQL that produced them.", where: "hub/studio/ledger.py, audit.py" },
      ] },
      { h: 54, nodes: [
        { id: "case", title: "Case file", lines: ["shared findings"], tone: "hubBox",
          detail: "The shared record of a request: the confirmed request, the findings specialists saved, the reports published. One case per request.", where: "hub/case_context.py" },
        { id: "studio", title: "Report studio", lines: ["charts · Word and HTML · audit"], tone: "hubBox", flex: 1.3,
          detail: "The tools the report team works through: evidence, calculations, section drafts, the number audit, charts, and the Word and HTML renderers with an evidence appendix.", where: "hub/studio/" },
        { id: "datatool", title: "Data tool", lines: ["question in · rows and SQL out · to Genie"], tone: "hubBox", flex: 1.7,
          detail: "Hands a question to the configured backend (a Databricks Genie space, your own function, or a stub) and registers the result in the ledger. Switching to the data gateway is a setting (DATABRICKS_AUTH=apim).", where: "hub/genie_gateway.py, hub/backends/" },
      ] },
    ],
  },
  {
    id: "g3", title: "Data gateway · Azure API Management", tag: "AI gateway", v2: true, tone: "gateway",
    detail: "Azure API Management between the hub and Databricks. Only the three Genie routes are open, only one space is allowed, new questions are rate-limited, and API Management calls Databricks with its own managed identity, so no Databricks credential reaches the hub.",
    where: "infra/apim, hub/backends/databricks_genie.py",
    rows: [{ h: 22, nodes: [chip("g3a", "Genie routes only"), chip("g3b", "one space allowed"), chip("g3c", "question rate limit"), chip("g3d", "API Management's own identity")] }],
  },
  {
    id: "dbx", title: "Data platform · Azure Databricks", tone: "dbx",
    detail: "Where the data lives and the SQL runs. The data layer is pluggable: a trimmed Genie space is the default, but the hub's data tool accepts any backend.",
    where: "integrations/genie/",
    rows: [
      { h: 72, nodes: [
        { id: "genie", title: "Genie space · trimmed", lines: ["natural language to SQL · read only"], tone: "dbxBox",
          detail: "A Genie space reduced to natural language to SQL on curated tables, with instructions and example queries. Its SQL varies between runs, so the harness reconciles against the data space's own figures and the Critic compares the filters each query applied.", where: "integrations/genie/README.md" },
        { id: "wh", title: "SQL warehouse", lines: ["runs the generated SQL"], tone: "dbxBox", flex: 0.8,
          detail: "The SQL warehouse behind the Genie space." },
        { id: "uc", title: "Unity Catalog data products", lines: ["sales · stock · prices · offers", "costs · margin bands · rules"], tone: "dbxBox", shape: "cylinder", flex: 1.4,
          detail: "Governed gold tables with lineage and row and column security. Data calls run as the hub's (or API Management's) identity today; passing the signed-in user's permissions through is not built." },
      ] },
      { h: 46, nodes: [
        { id: "pricing", title: "Pricing engine and decision log", lines: ["demand model · optimiser · rules → published as tables Genie can read"], tone: "dbxBox", planned: true,
          detail: "A separate workstream: the demand model, optimiser and rules check run as Databricks jobs and publish plans and decisions as tables. The assistant only reads what they publish." },
      ] },
    ],
  },
  {
    id: "croma", title: "", tone: "ext", bare: true, w: 330, gapBefore: 50,
    rows: [{ h: 56, nodes: [{ id: "croma", title: "Croma systems", lines: ["SAP · POS · offer engine"], tone: "ext",
      detail: "The source systems that feed the data platform with daily data. In this build the data is synthetic." }] }],
  },
];

export const FLOWS: Flow[] = [
  { from: "users", to: "exp", n: 1, label: "ask in chat · sign in", n2: 7, label2: "the answer returns the same way, with its evidence" },
  { from: "exp", to: "g1", n: 2, label: "Entra ID token · per-user limits" },
  { from: "g1", to: "api", n: 3, label: "caller identity added" },
  { from: "api", to: "foundry", n: 4, label: "CX agent: plan check, then the right journey" },
  { from: "foundry", to: "g2", n: 5, label: "tool calls (MCP) into the hub" },
  { from: "g2", to: "hub" },
  { from: "hub", to: "g3", n: 6, label: "governed question to Genie" },
  { from: "g3", to: "dbx" },
  { from: "croma", to: "dbx", label: "daily data", dashed: true, heads: "to", x: MAIN_X + 140 },
];

// the panels that have a dotted line to the band on the right ("every call is traced")
export const TRACED = ["exp", "g1", "api", "foundry", "g2", "hub", "g3", "dbx"];

// ------------------------------------------------------------------ the band on the right and the pilot box under it

export const BAND: Panel = {
  id: "trust", title: "Trust and AgentOps · every layer", tone: "trust",
  detail: "What applies to every layer. Each main layer has a dotted line to this band: every call is traced and logged, and identities, keys and guardrails are enforced at each hop.",
  rows: [
    { h: 0, nodes: [{ id: "t_id", title: "Identity and access", lines: ["Entra ID sign-in for users", "managed identity for each component", "one revocable key per consumer"], tone: "trustBox", v2: true,
      detail: "Users sign in with Entra ID; the chat app, the hub and API Management each use a managed identity; every consumer of a gateway has its own revocable key; with the data gateway, Databricks is reached with API Management's own identity, not a shared token.", where: "docs/APIM.md" }] },
    { h: 0, nodes: [{ id: "t_guard", title: "Guardrails", lines: ["plan check before anything runs", "scope and unit in every question", "ratios reconciled with the data space", "number audit · independent Critic"], tone: "trustBox", v2: true,
      detail: "The checks that came out of live runs: the CX plan check; the standing scope and the unit in every data question; each result analysed in the unit the user is shown; ratio decompositions reconciled with the data space's own figure; every written figure traced to the ledger; an independent Critic.", where: "docs/V2.md section 3" }] },
    { h: 0, nodes: [{ id: "t_ops", title: "AgentOps", lines: ["API Management logs per user and tool", "App Insights · Log Analytics", "hub event feed · harness stage log", "admin view in the chat"], tone: "trustBox", v2: true,
      detail: "API Management sends request telemetry and per-tool traces to Application Insights and Log Analytics. The hub keeps an event feed (agent calls, tool calls, harness stages, the SQL) that the chat's admin view shows live: agent network, activity, logs, case file, memory.", where: "hub/events.py, ui/frontend" }] },
    { h: 0, nodes: [{ id: "t_models", title: "Models", lines: ["conversational: CX, Lookup, Data", "deep-reasoning: workers, report team", "embeddings: memory", "token governor protects the quota"], tone: "trustBox",
      detail: "Deployments in the Foundry account, chosen at deploy time: a conversational model for the CX, Lookup and Data agents, a deep-reasoning model for the harness workers and the report team, and an embedding model for memory. The token governor and the retry on a rate limit protect the shared quota." }] },
    { h: 0, nodes: [{ id: "t_eval", title: "Evaluations", lines: ["golden questions with known answers", "a run after every change", "certified Genie measures"], tone: "trustBox", planned: true,
      detail: "Planned. A set of golden questions with known answers that runs after every change to a prompt, the data space or a model, plus certified Genie measures for the metrics that matter." }] },
    { h: 0, nodes: [{ id: "t_prot", title: "Data protection", lines: ["secrets in Key Vault", "private endpoints · Private Link", "Purview classification"], tone: "trustBox", planned: true,
      detail: "Planned for the Croma tenant: secrets in Key Vault, private endpoints (Standard v2 API Management adds network integration), and Purview for classification." }] },
    { h: 0, nodes: [{ id: "t_ship", title: "Build and ship", lines: ["Bicep, PowerShell and Python scripts", "unit tests in CI, no Azure needed", "v1 and v2 side by side (name prefix)"], tone: "trustBox", v2: true,
      detail: "Deployment scripts for the hub, the agents, the gateways and the chat app (Bicep for the gateways, PowerShell for the hub and the chat app, Python for the agents), unit tests that run in CI without Azure, and a name prefix that lets v2 run next to v1 with its own agents, memory and connections. The Foundry account, project and model deployments are created by the runbook's own steps.", where: "infra/, tests/, docs/DEPLOYMENT.md" }] },
  ],
};

export const PILOT: Node = {
  id: "pilot", title: "Pilot scope · outside this build", lines: ["Fabric OneLake · Power BI review", "price file → SAP → pilot stores"], tone: "pilot", planned: true, badge: "NEXT PHASE",
  detail: "From the pricing-pilot design: Fabric OneLake with mirrored pricing tables and a gold layer, a Power BI review report, and the approved price file uploaded to SAP and trialled in pilot stores. Not part of this app; shown so the picture connects to the pilot.",
};

// ------------------------------------------------------------------ layout

export interface Placed { node: Node; rect: Rect }
export interface PlacedPanel { panel: Panel; rect: Rect; nodes: Placed[] }
export interface Detail { id: string; title: string; planned: boolean; text: string; where?: string }

export interface Layout {
  height: number;
  container: Rect;
  panels: PlacedPanel[];
  band: PlacedPanel;
  pilot: Placed;
  arrows: { x: number; y1: number; y2: number; flow: Flow }[];
  traces: { x1: number; x2: number; y: number }[];
  details: Record<string, Detail>;
}

function placeRows(panel: Panel, x: number, y: number, w: number): { nodes: Placed[]; bottom: number } {
  const nodes: Placed[] = [];
  let cy = y + (panel.bare ? 0 : HEAD);
  for (const row of panel.rows) {
    const gaps = (row.nodes.length - 1) * COL_GAP;
    const total = row.nodes.reduce((s, n) => s + (n.flex ?? 1), 0);
    const inner = w - (panel.bare ? 0 : 2 * PAD) - gaps;
    let cx = x + (panel.bare ? 0 : PAD);
    for (const node of row.nodes) {
      const nw = (inner * (node.flex ?? 1)) / total;
      nodes.push({ node, rect: { x: cx, y: cy, w: nw, h: row.h } });
      cx += nw + COL_GAP;
    }
    cy += row.h + ROW_GAP;
  }
  return { nodes, bottom: cy - ROW_GAP + (panel.bare ? 0 : PAD) };
}

export function buildLayout(): Layout {
  const panels: PlacedPanel[] = [];
  let y = 8;
  for (const [i, panel] of MAIN.entries()) {
    y += i === 0 ? 0 : panel.gapBefore ?? GAP;
    const w = panel.w ?? MAIN_W;
    const x = panel.center ? MAIN_X + (MAIN_W - w) / 2 : MAIN_X;
    const { nodes, bottom } = placeRows(panel, x, y, w);
    panels.push({ panel, rect: { x, y, w, h: bottom - y }, nodes });
    y = bottom;
  }
  const by = (id: string) => panels.find((p) => p.panel.id === id)!;
  const exp = by("exp").rect, dbx = by("dbx").rect, croma = by("croma").rect;
  const container: Rect = { x: 8, y: exp.y - 52, w: WIDTH - 16, h: dbx.y + dbx.h + 16 - (exp.y - 52) };

  // the band spans the stack from the experience layer to the data platform; its boxes share the height
  const bandTop = exp.y, bandH = dbx.y + dbx.h - exp.y;
  const n = BAND.rows.length, boxH = (bandH - HEAD - 2 * PAD - (n - 1) * ROW_GAP) / n;
  const bandNodes: Placed[] = BAND.rows.map((row, i) => ({ node: row.nodes[0], rect: { x: BAND_X + PAD, y: bandTop + HEAD + i * (boxH + ROW_GAP), w: BAND_W - 2 * PAD, h: boxH } }));
  const band: PlacedPanel = { panel: BAND, rect: { x: BAND_X, y: bandTop, w: BAND_W, h: bandH }, nodes: bandNodes };
  const pilot: Placed = { node: PILOT, rect: { x: BAND_X, y: croma.y, w: BAND_W, h: croma.h } };

  const arrows = FLOWS.map((flow) => {
    const a = by(flow.from).rect, b = by(flow.to).rect;
    const x = flow.x ?? MAIN_X + MAIN_W / 2;
    const down = b.y > a.y;
    return { x, y1: down ? a.y + a.h : a.y, y2: down ? b.y : b.y + b.h, flow };
  });
  const traces = TRACED.map((id) => {
    const r = by(id).rect;
    return { x1: r.x + r.w, x2: BAND_X, y: r.y + Math.min(r.h / 2, 30) };
  });

  const details: Record<string, Detail> = {};
  const add = (id: string, title: string, planned: boolean, text?: string, where?: string) => { if (text) details[id] = { id, title, planned, text, where }; };
  for (const p of panels) {
    add(p.panel.id, p.panel.title || p.nodes[0].node.title, false, p.panel.detail, p.panel.where);
    for (const { node } of p.nodes) add(node.id, node.title, !!node.planned, node.detail, node.where);
  }
  add(BAND.id, BAND.title, false, BAND.detail);
  for (const { node } of band.nodes) add(node.id, node.title, !!node.planned, node.detail, node.where);
  add(PILOT.id, PILOT.title, true, PILOT.detail);

  return { height: croma.y + croma.h + 10, container, panels, band, pilot, arrows, traces, details };
}
