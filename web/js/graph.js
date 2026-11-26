import { html } from "./lib.js";

const W = 210, H = 54, GAP_X = 70, GAP_Y = 18;

// Layered layout: an item's column is the length of its longest chain of prerequisites.
function layout(nodes, edges) {
  const prereqs = Object.fromEntries(nodes.map((n) => [n.id, []]));
  for (const e of edges) if (prereqs[e.src_id] && prereqs[e.dst_id] !== undefined) prereqs[e.src_id].push(e.dst_id);
  const depth = {};
  const visit = (id, seen) => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0; // cycle guard
    seen.add(id);
    depth[id] = prereqs[id].length ? 1 + Math.max(...prereqs[id].map((p) => visit(p, seen))) : 0;
    return depth[id];
  };
  nodes.forEach((n) => visit(n.id, new Set()));
  const rows = {};
  const pos = {};
  for (const n of [...nodes].sort((a, b) => (a.lane || "").localeCompare(b.lane || "") || a.title.localeCompare(b.title))) {
    const col = depth[n.id];
    const row = (rows[col] = (rows[col] || 0) + 1) - 1;
    pos[n.id] = { x: col * (W + GAP_X), y: row * (H + GAP_Y) };
  }
  return pos;
}

export function DependencyGraph({ nodes, edges, onSelect }) {
  const linked = new Set(edges.flatMap((e) => [e.src_id, e.dst_id]));
  const shown = nodes.filter((n) => linked.has(n.id));
  if (shown.length === 0) return html`<p class="muted">No dependencies between this project's items yet.</p>`;
  const pos = layout(shown, edges);
  const width = Math.max(...Object.values(pos).map((p) => p.x)) + W + 10;
  const height = Math.max(...Object.values(pos).map((p) => p.y)) + H + 10;
  return html`
    <div class="graph-wrap">
      <svg class="graph" viewBox=${`-5 -5 ${width} ${height}`} width=${width} height=${height} role="img"
           aria-label="Dependency graph: arrows point from an item to what it needs first">
        <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrowhead" /></marker></defs>
        ${edges.filter((e) => pos[e.src_id] && pos[e.dst_id]).map((e, n) => {
          const a = pos[e.dst_id], b = pos[e.src_id];
          return html`<line key=${n} class=${`edge ${e.kind}`} x1=${a.x + W} y1=${a.y + H / 2} x2=${b.x} y2=${b.y + H / 2} marker-end="url(#arrow)" />`;
        })}
        ${shown.map((n) => {
          const p = pos[n.id];
          return html`
            <g key=${n.id} class=${`node ${n.risk_level} ${n.status}`} transform=${`translate(${p.x},${p.y})`}
               onClick=${() => onSelect(n.id)} tabIndex="0" onKeyDown=${(e) => e.key === "Enter" && onSelect(n.id)}>
              <rect width=${W} height=${H} rx="4" />
              <text x="8" y="21">${n.title.length > 30 ? n.title.slice(0, 29) + "…" : n.title}</text>
              <text x="8" y="40" class="sub">${[n.kind, n.status, n.risk_score ? `risk ${n.risk_score}` : null].filter(Boolean).join(" · ")}</text>
            </g>`;
        })}
      </svg>
    </div>`;
}
