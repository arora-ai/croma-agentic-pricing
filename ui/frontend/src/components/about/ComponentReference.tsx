import { BAND, MAIN, PILOT, type Node, type Panel } from "./architecture";

/** What every box in the diagram does and where it lives. It is the text that opens when a box is clicked, laid out as a list so that a printed copy
 *  (Save as PDF) keeps it. Hidden on screen; shown only when printing. Built from the diagram's own data, so the two cannot drift apart. */

interface Row { id: string; title: string; planned: boolean; v2: boolean; text: string; where?: string }
interface Group { id: string; title: string; tag?: string; v2: boolean; planned: boolean; text?: string; where?: string; includes: string[]; rows: Row[] }

const rowOf = (n: Node): Row => ({ id: n.id, title: n.title, planned: !!n.planned, v2: !!n.v2, text: n.detail ?? "", where: n.where });

function groupOf(p: Panel): Group {
  const nodes = p.rows.flatMap((r) => r.nodes);
  if (p.bare) {
    // a panel that is only its one box (the users, Croma's systems): the box carries the text
    const n = nodes[0];
    return { id: p.id, title: n.title, v2: !!n.v2, planned: !!n.planned, text: n.detail, where: n.where, includes: [], rows: [] };
  }
  return {
    id: p.id, title: p.title, tag: p.tag, v2: !!p.v2, planned: false, text: p.detail, where: p.where,
    includes: nodes.filter((n) => n.shape === "chip").map((n) => n.title),
    rows: nodes.filter((n) => n.shape !== "chip" && n.detail).map(rowOf),
  };
}

const GROUPS: Group[] = [
  ...MAIN.map(groupOf),
  groupOf(BAND),
  { id: PILOT.id, title: PILOT.title, v2: false, planned: true, text: PILOT.detail, where: PILOT.where, includes: [], rows: [] },
];

const status = (planned: boolean) => (planned ? "Planned or outside this build" : "Built");

export function ComponentReference() {
  return (
    <section className="ref print-only" aria-label="Component reference">
      <h2>Component reference</h2>
      <p className="sub">What each box in the architecture diagram does, and where it lives in the repository. The same text opens when a box is clicked in the app.</p>
      {GROUPS.map((g) => (
        <div className="ref-group" key={g.id}>
          <h3>
            {g.title}
            {g.tag && <b className="tag teal">{g.tag}</b>}
            {g.v2 && <b className="tag dark">v2</b>}
            {g.planned && <span className="arch-status planned">{status(true)}</span>}
          </h3>
          {g.text && <p>{g.text}</p>}
          {g.includes.length > 0 && <p className="ref-includes">Includes: {g.includes.join(" · ")}</p>}
          {g.where && <code>{g.where}</code>}
          {g.rows.length > 0 && (
            <table>
              <tbody>
                {g.rows.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">
                      {r.title}
                      <span className="ref-meta">
                        <span className={`arch-status ${r.planned ? "planned" : "built"}`}>{status(r.planned)}</span>
                        {r.v2 && <b className="tag dark">v2</b>}
                      </span>
                    </th>
                    <td>{r.text}{r.where && <code>{r.where}</code>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </section>
  );
}
