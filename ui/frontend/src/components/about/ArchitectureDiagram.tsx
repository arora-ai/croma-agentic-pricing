import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { BAND_X, TONES, WIDTH, buildLayout, type Placed, type PlacedPanel } from "./architecture";

const SLATE = "#475569";
const GREY = "#5B6573";
const NAVY = "#2E2A6E";

/** Where a click happened, in viewport coordinates: the details open next to it. */
interface Anchor { left: number; top: number; bottom: number }
type Select = (id: string, anchor: Anchor) => void;

const rectAnchor = (el: Element): Anchor => { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, bottom: r.bottom }; };
const pointAnchor = (e: MouseEvent): Anchor => ({ left: e.clientX - 40, top: e.clientY - 10, bottom: e.clientY + 10 });

/** Splits text over lines that fit a width, by an estimate of the glyph width (the diagram is drawn with fixed text sizes, so this has to be conservative). */
function wrap(text: string, px: number, size: number, bold = false): string[] {
  const max = Math.max(6, Math.floor(px / (size * (bold ? 0.58 : 0.54))));
  const out: string[] = [];
  let line = "";
  for (const word of text.split(" ")) {
    const next = line ? `${line} ${word}` : word;
    if (next.length > max && line) {
      out.push(line);
      line = word;
    } else line = next;
  }
  if (line) out.push(line);
  return out;
}

function Pill({ x, y, text, fill, align = "left" }: { x: number; y: number; text: string; fill: string; align?: "left" | "right" }) {
  const w = text.length * 5.9 + 12;
  const x0 = align === "left" ? x : x - w;
  return (
    <g pointerEvents="none">
      <rect x={x0} y={y - 7.5} width={w} height={15} rx={7.5} fill={fill} />
      <text x={x0 + w / 2} y={y} textAnchor="middle" dominantBaseline="central" fontSize={9} fontWeight={700} letterSpacing="0.04em" fill="#fff">{text}</text>
    </g>
  );
}

const badgeFill = (text: string, planned: boolean) => (planned ? GREY : text === "PLAN CHECK" ? "#0E8C80" : "#6B4FBB");

function NodeView({ p, on, select }: { p: Placed; on: boolean; select: Select }) {
  const { node, rect } = p;
  const tone = TONES[node.tone];
  const shape = node.shape ?? "box";
  const isChip = shape === "chip";
  const dash = node.planned ? "6 4" : undefined;
  const cap = shape === "cylinder" ? 7 : 0; // the height of the rim of a cylinder
  const titleSize = isChip ? 11 : shape === "person" ? 14 : 13;
  const inner = rect.w - (isChip ? 12 : 20);
  const title = wrap(node.title, inner, titleSize, !isChip);
  const lines = (node.lines ?? []).flatMap((l) => wrap(l, inner, 11.5));
  const lhT = titleSize + 3, lhL = 13.4;
  const block = title.length * lhT + lines.length * lhL + (lines.length ? 2 : 0);
  const top = rect.y + cap * 1.4 + (rect.h - cap * 2.4 - block) / 2;
  const sub = shape === "person" ? "#DCEBFA" : node.planned ? "#6B7280" : "#4B5563";
  const corner = node.tag ?? (node.planned && !node.badge ? "planned" : "");
  const body = shape === "cylinder"
    ? <>
        <path className="shape" d={`M${rect.x},${rect.y + cap} L${rect.x},${rect.y + rect.h - cap} A${rect.w / 2},${cap} 0 0 0 ${rect.x + rect.w},${rect.y + rect.h - cap} L${rect.x + rect.w},${rect.y + cap}`} fill={tone.fill} stroke={tone.stroke} strokeWidth={1.6} />
        <ellipse className="shape" cx={rect.x + rect.w / 2} cy={rect.y + cap} rx={rect.w / 2} ry={cap} fill={tone.fill} stroke={tone.stroke} strokeWidth={1.6} />
      </>
    : <rect className="shape" x={rect.x} y={rect.y} width={rect.w} height={rect.h} rx={shape === "box" ? 8 : rect.h / 2} fill={tone.fill} stroke={tone.stroke} strokeWidth={isChip ? 1.1 : 1.6} strokeDasharray={dash} />;
  const interactive = !isChip;
  const keys = (e: KeyboardEvent<SVGGElement>) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(node.id, rectAnchor(e.currentTarget)); } };
  return (
    <g
      className={`arch-node${on ? " on" : ""}${interactive ? " click" : ""}`}
      {...(interactive ? { onClick: (e: MouseEvent<SVGGElement>) => { e.stopPropagation(); select(node.id, rectAnchor(e.currentTarget)); }, tabIndex: 0, role: "button", onKeyDown: keys, "aria-label": `${node.title}${node.planned ? " (planned)" : ""}` } : {})}
    >
      {body}
      {title.map((t, i) => <text key={`t${i}`} x={rect.x + rect.w / 2} y={top + (i + 0.5) * lhT} textAnchor="middle" dominantBaseline="central" fontSize={titleSize} fontWeight={isChip ? 500 : 650} fill={tone.text} pointerEvents="none">{t}</text>)}
      {lines.map((t, i) => <text key={`l${i}`} x={rect.x + rect.w / 2} y={top + title.length * lhT + 2 + (i + 0.5) * lhL} textAnchor="middle" dominantBaseline="central" fontSize={11.5} fill={sub} pointerEvents="none">{t}</text>)}
      {node.v2 && !isChip && <Pill x={rect.x + 8} y={rect.y} text="v2" fill="#1F2933" />}
      {node.badge && <Pill x={rect.x + rect.w - 8} y={rect.y} text={node.badge} fill={badgeFill(node.badge, !!node.planned)} align="right" />}
      {corner && <text x={rect.x + rect.w - 9} y={rect.y + rect.h - 7} textAnchor="end" fontSize={9.5} fontStyle="italic" fill="#6B7280" pointerEvents="none">{corner}</text>}
    </g>
  );
}

function PanelView({ pp, selected, select }: { pp: PlacedPanel; selected: string | null; select: Select }) {
  const { panel, rect } = pp;
  const tone = TONES[panel.tone];
  const nodes = pp.nodes.map((n) => <NodeView key={n.node.id} p={n} on={selected === n.node.id} select={select} />);
  if (panel.bare) return <>{nodes}</>;
  const keys = (e: KeyboardEvent<SVGGElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const a = rectAnchor(e.currentTarget);
      select(panel.id, { left: a.left, top: a.top, bottom: a.top + 28 });
    }
  };
  const tagW = panel.tag ? panel.tag.length * 5.9 + 12 : 0;
  return (
    <g>
      <g className={`arch-panel click${selected === panel.id ? " on" : ""}`} onClick={(e) => { e.stopPropagation(); select(panel.id, pointAnchor(e)); }} tabIndex={0} role="button" onKeyDown={keys} aria-label={panel.title}>
        <rect className="shape" x={rect.x} y={rect.y} width={rect.w} height={rect.h} rx={12} fill={tone.fill} stroke={tone.stroke} strokeWidth={1.5} />
        <text x={rect.x + 14} y={rect.y + 15} dominantBaseline="central" fontSize={12.5} fontWeight={650} fill={tone.text} pointerEvents="none">{panel.title}</text>
        {panel.tag && <Pill x={rect.x + rect.w - 10} y={rect.y + 15} text={panel.tag} fill={tone.stroke} align="right" />}
        {panel.v2 && <Pill x={rect.x + rect.w - 10 - tagW - (panel.tag ? 6 : 0)} y={rect.y + 15} text="v2" fill="#1F2933" align="right" />}
      </g>
      {nodes}
    </g>
  );
}

interface Open { id: string; left: number; top: number; above: boolean; width: number }

export function ArchitectureDiagram() {
  const layout = useMemo(buildLayout, []);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<Open | null>(null);

  useEffect(() => {
    if (!open) return;
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open]);

  const select: Select = (id, a) => {
    if (open?.id === id) return setOpen(null);
    const box = wrapRef.current?.getBoundingClientRect();
    if (!box) return;
    const width = Math.min(460, box.width - 16);
    const bottom = a.bottom - box.top;
    const above = bottom > box.height - 250; // low in the diagram: open the card above the box so it stays on screen
    setOpen({ id, width, above, left: Math.max(8, Math.min(a.left - box.left, box.width - width - 8)), top: above ? a.top - box.top - 10 : bottom + 10 });
  };

  const detail = open ? layout.details[open.id] : null;
  const c = layout.container;
  const band = layout.band;
  const selected = open?.id ?? null;
  return (
    <div className="arch" ref={wrapRef}>
      <div className="arch-scroll" onClick={() => setOpen(null)}>
        <svg className="arch-svg" viewBox={`0 0 ${WIDTH} ${layout.height}`} role="group" aria-label="Architecture of the Croma Pricing Assistant, v2">
          <defs>
            <marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill={SLATE} /></marker>
            <marker id="arch-arrow-grey" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill={GREY} /></marker>
          </defs>

          <rect x={c.x} y={c.y} width={c.w} height={c.h} rx={16} fill="#FBFBFE" stroke={NAVY} strokeWidth={1.8} strokeDasharray="8 5" />
          <text x={c.x + 16} y={c.y + 17} dominantBaseline="central" fontSize={12.5} fontWeight={650} fill={NAVY}>Azure solution · secured and observed end to end</text>

          {layout.traces.map((t, i) => (
            <g key={i} pointerEvents="none">
              <line x1={t.x1} y1={t.y} x2={t.x2} y2={t.y} stroke={NAVY} strokeWidth={1.3} strokeDasharray="2 4" />
              <circle cx={t.x2} cy={t.y} r={3.2} fill={NAVY} />
            </g>
          ))}

          <g>
            <g className={`arch-panel click${selected === band.panel.id ? " on" : ""}`} onClick={(e) => { e.stopPropagation(); select(band.panel.id, pointAnchor(e)); }} tabIndex={0} role="button" aria-label={band.panel.title}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const a = rectAnchor(e.currentTarget); select(band.panel.id, { left: a.left, top: a.top, bottom: a.top + 28 }); } }}>
              <rect className="shape" x={band.rect.x} y={band.rect.y} width={band.rect.w} height={band.rect.h} rx={12} fill={TONES.trust.fill} stroke={TONES.trust.stroke} strokeWidth={1.5} />
              <text x={band.rect.x + 14} y={band.rect.y + 15} dominantBaseline="central" fontSize={12.5} fontWeight={650} fill={NAVY} pointerEvents="none">{band.panel.title}</text>
              <g pointerEvents="none">
                <circle cx={band.rect.x + band.rect.w - 20} cy={band.rect.y + 15} r={8.5} fill="#fff" stroke={NAVY} strokeWidth={1.3} />
                <text x={band.rect.x + band.rect.w - 20} y={band.rect.y + 15.5} textAnchor="middle" dominantBaseline="central" fontSize={10.5} fontWeight={700} fill={NAVY}>8</text>
              </g>
            </g>
            {band.nodes.map((n) => <NodeView key={n.node.id} p={n} on={selected === n.node.id} select={select} />)}
          </g>

          {layout.panels.map((pp) => <PanelView key={pp.panel.id} pp={pp} selected={selected} select={select} />)}

          {layout.arrows.map(({ x, y1, y2, flow }, i) => {
            const mid = (y1 + y2) / 2;
            const grey = flow.dashed;
            const col = grey ? GREY : SLATE;
            const two = flow.n2 !== undefined;
            const marker = `url(#${grey ? "arch-arrow-grey" : "arch-arrow"})`;
            const badge = (n: number, y: number, text?: string) => (
              <g key={`${i}-${n}`} pointerEvents="none">
                <circle cx={x} cy={y} r={8.5} fill="#fff" stroke={col} strokeWidth={1.3} />
                <text x={x} y={y + 0.5} textAnchor="middle" dominantBaseline="central" fontSize={10.5} fontWeight={700} fill={col}>{n}</text>
                {text && <text x={x + 16} y={y + 0.5} dominantBaseline="central" fontSize={11.5} fill="#334155" stroke="#fff" strokeWidth={3.5} paintOrder="stroke" strokeLinejoin="round">{text}</text>}
              </g>
            );
            return (
              <g key={i}>
                <line x1={x} y1={y1} x2={x} y2={y2} stroke={col} strokeWidth={1.6} strokeDasharray={grey ? "5 4" : undefined} markerEnd={marker} markerStart={flow.heads === "to" ? undefined : marker} />
                {flow.n !== undefined && badge(flow.n, two ? mid - 11 : mid, flow.label)}
                {two && badge(flow.n2 as number, mid + 11, flow.label2)}
                {flow.n === undefined && flow.label && <text x={x + 12} y={mid} dominantBaseline="central" fontSize={11.5} fill="#334155" stroke="#fff" strokeWidth={3.5} paintOrder="stroke">{flow.label}</text>}
              </g>
            );
          })}

          <NodeView p={layout.pilot} on={selected === "pilot"} select={select} />
          <text x={BAND_X + 2} y={layout.pilot.rect.y - 12} fontSize={10.5} fill="#6B7280" pointerEvents="none">Context from the pilot design</text>
        </svg>
      </div>
      {open && detail && (
        <div className={`arch-pop${open.above ? " above" : ""}`} style={{ left: open.left, top: open.top, width: open.width }} role="dialog" aria-label={detail.title}>
          <div className="arch-pop-head">
            <span className={`arch-status ${detail.planned ? "planned" : "built"}`}>{detail.planned ? "Planned or outside this build" : "Built"}</span>
            <strong>{detail.title}</strong>
            <button className="link" onClick={() => setOpen(null)} aria-label="Close">Close</button>
          </div>
          <p>{detail.text}</p>
          {detail.where && <code>{detail.where}</code>}
        </div>
      )}
    </div>
  );
}
