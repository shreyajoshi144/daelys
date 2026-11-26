import { api } from "../api.js";
import { ErrorNote, LANES, html, navigate, useLoad, useMemo, useState } from "../lib.js";
import { Loading } from "../ui.js";

export function PlanReviewPage({ projectId, draftId, canEdit }) {
  const draft = useLoad(() => api.draft(projectId, draftId), [projectId, draftId]);
  const artifactId = draft.data && draft.data.artifact_id;
  const brief = useLoad(() => (artifactId ? api.brief(projectId, artifactId) : Promise.resolve(null)), [artifactId]);
  const [active, setActive] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (draft.error) return html`<${ErrorNote} error=${draft.error} />`;
  if (!draft.data) return html`<${Loading} label="Reading the draft" />`;
  const { plan, dropped, status, repaired } = draft.data;
  const titles = Object.fromEntries(plan.items.map((i) => [i.local_id, i.title]));
  const deps = plan.items.flatMap((i) => i.depends_on.map((d) => ({ from: i, to: d })));

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      await api.acceptPlan(projectId, draftId);
      navigate(`/projects/${projectId}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  const hover = (spans) => ({ onMouseEnter: () => setActive(spans), onMouseLeave: () => setActive([]),
    onFocus: () => setActive(spans), onBlur: () => setActive([]), tabIndex: 0 });

  return html`
    <p><a href=${`#/projects/${projectId}`}>← Project</a></p>
    <h1>Plan review <span class="muted">(${status}${repaired ? ", auto-repaired once" : ""})</span></h1>
    <${ErrorNote} error=${error} />
    ${canEdit && status === "draft" && html`
      <p><button onClick=${accept} disabled=${busy}>${busy ? "Accepting…" : `Accept plan (${plan.items.length} items)`}</button></p>`}
    <div class="review">
      <div>
        ${LANES.map((lane) => {
          const laneItems = plan.items.filter((i) => i.lane === lane);
          return laneItems.length > 0 && html`
            <section key=${lane}>
              <h2>${lane}</h2>
              <ol class="items">
                ${laneItems.map((i) => html`
                  <li key=${i.local_id} ...${hover([i.source_span])} class=${active.includes(i.source_span) ? "on" : ""}>
                    <span class="title">${i.title}</span>
                    <span class="meta">${i.owner_role} · ${i.estimate_days}d</span>
                  </li>`)}
              </ol>
            </section>`;
        })}
        <section>
          <h2>Dependencies</h2>
          ${deps.length === 0 && html`<p class="muted">None.</p>`}
          <ul class="plain">
            ${deps.map((d) => html`<li key=${d.from.local_id + d.to}>${d.from.title} <span class="muted">needs</span> ${titles[d.to]}</li>`)}
          </ul>
        </section>
        <section class="questions">
          <h2>Open questions</h2>
          ${plan.open_questions.length === 0 && html`<p class="muted">None.</p>`}
          <ul class="plain">
            ${plan.open_questions.map((q, n) => html`<li key=${n} ...${hover(q.source_spans)}>${q.text}</li>`)}
          </ul>
        </section>
        ${dropped.length > 0 && html`
          <details>
            <summary>${dropped.length} dropped by validation</summary>
            <ul class="plain">${dropped.map((d, n) => html`<li key=${n}><b>${d.kind}</b> ${d.ref}: ${d.reason}</li>`)}</ul>
          </details>`}
      </div>
      <aside class="brief">
        <h2>${brief.data ? brief.data.title : "Brief"}</h2>
        <${ErrorNote} error=${brief.error} />
        ${brief.data && html`<${Highlighted} text=${brief.data.content} spans=${active} />`}
      </aside>
    </div>
  `;
}

function Highlighted({ text, spans }) {
  const parts = useMemo(() => {
    const sorted = [...spans].sort((a, b) => a.start - b.start);
    const out = [];
    let pos = 0;
    for (const s of sorted) {
      const start = Math.max(s.start, pos);
      if (start >= s.end) continue;
      out.push(text.slice(pos, start), html`<mark key=${start}>${text.slice(start, s.end)}</mark>`);
      pos = s.end;
    }
    out.push(text.slice(pos));
    return out;
  }, [text, spans]);
  return html`<pre class="brief-text">${parts}</pre>`;
}
