import { api } from "../api.js";
import { DependencyGraph } from "../graph.js";
import {
  ErrorNote, HealthCard, LANES, RiskBadge, html, navigate, useCallback, useLiveUpdates, useLoad, useState,
} from "../lib.js";
import { Loading } from "../ui.js";
import { WhyPanel } from "./why.js";

export function ProjectPage({ projectId, canEdit, clockNow }) {
  const project = useLoad(() => api.project(projectId), [projectId]);
  const items = useLoad(() => api.workItems(projectId), [projectId]);
  const briefs = useLoad(() => api.briefs(projectId), [projectId]);
  const drafts = useLoad(() => api.drafts(projectId), [projectId]);
  const health = useLoad(() => api.health(), []);
  const graph = useLoad(() => api.graph(projectId), [projectId]);
  const [selected, setSelected] = useState(null);
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => {
    items.reload();
    health.reload();
    graph.reload();
    setVersion((v) => v + 1);
  }, [items.reload, health.reload, graph.reload]);
  useLiveUpdates(refresh);

  if (project.error) return html`<${ErrorNote} error=${project.error} />`;
  if (!project.data) return html`<${Loading} label="Opening the project" />`;

  return html`
    <div class="eyebrow-sm">Project</div>
    <h1>${project.data.name}</h1>
    <${ProjectHealth} summary=${health.data} projectId=${projectId} />
    <div class=${selected ? "with-panel" : ""}>
      <div>
        <${Lanes} items=${items.data ? items.data.items : []} error=${items.error} onSelect=${setSelected} />
        <${Summaries} projectId=${projectId} canEdit=${canEdit} clockNow=${clockNow} />
        <${Duplicates} canEdit=${canEdit} />
        <section>
          <h2>Dependencies</h2>
          <${ErrorNote} error=${graph.error} />
          ${graph.data && html`<${DependencyGraph} nodes=${graph.data.nodes} edges=${graph.data.edges} onSelect=${setSelected} />`}
        </section>
      </div>
      ${selected && html`<${WhyPanel} itemId=${selected} version=${version} canEdit=${canEdit} onClose=${() => setSelected(null)} />`}
    </div>
    <section>
      <h2>Briefs</h2>
      <${ErrorNote} error=${briefs.error} />
      <${BriefList} projectId=${projectId} briefs=${briefs.data || []} canEdit=${canEdit} />
      ${canEdit && html`<${BriefForm} projectId=${projectId} onAdded=${briefs.reload} />`}
    </section>
    <section>
      <h2>Plan drafts</h2>
      <${ErrorNote} error=${drafts.error} />
      ${drafts.data && drafts.data.length === 0 && html`<p class="muted">No drafts yet.</p>`}
      <ul class="plain">
        ${(drafts.data || []).map((d) => html`
          <li key=${d.id}>
            <a href=${`#/projects/${projectId}/plan/${d.id}`}>${new Date(d.created_at).toLocaleString()}</a>
            <span class="muted"> · ${d.plan.items.length} items · ${d.plan.open_questions.length} open questions · ${d.status}</span>
          </li>`)}
      </ul>
    </section>
  `;
}

function ProjectHealth({ summary, projectId }) {
  if (!summary) return null;
  const mine = summary.projects.find((p) => p.project_id === projectId);
  if (!mine) return null;
  const detail = [mine.at_risk && `${mine.at_risk} at risk`, mine.watch && `${mine.watch} to watch`].filter(Boolean).join(" · ");
  return html`<${HealthCard} title="Project health" health=${mine.health} detail=${detail} />`;
}

function Lanes({ items, error, onSelect }) {
  return html`
    <${ErrorNote} error=${error} />
    <div class="lanes">
      ${LANES.map((lane) => {
        const laneItems = items.filter((i) => i.lane === lane);
        return html`
          <section class="lane" key=${lane}>
            <h2>${lane}</h2>
            ${laneItems.length === 0 && html`<p class="muted">Nothing here yet.</p>`}
            <ol class="items">
              ${laneItems.map((i) => html`
                <li key=${i.id} class=${`clickable risk-${i.risk_level}`} tabIndex="0" onClick=${() => onSelect(i.id)}
                    onKeyDown=${(e) => e.key === "Enter" && onSelect(i.id)}>
                  <span class="title">${i.title} <${RiskBadge} level=${i.risk_level} score=${i.risk_score} /></span>
                  <span class="meta">${[i.owner_role, i.estimate_days && `${i.estimate_days}d`, i.status].filter(Boolean).join(" · ")}</span>
                </li>`)}
            </ol>
          </section>`;
      })}
    </div>
  `;
}

function BriefList({ projectId, briefs, canEdit }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  async function plan(briefId) {
    setBusy(briefId);
    setError(null);
    try {
      const draft = await api.generatePlan(projectId, briefId);
      navigate(`/projects/${projectId}/plan/${draft.id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return html`
    <ul class="plain">
      ${briefs.map((b) => html`
        <li key=${b.id}>
          ${b.title} <span class="muted">· ${b.content.length.toLocaleString()} characters</span>
          ${canEdit && html` <button class="link" disabled=${busy !== null} onClick=${() => plan(b.id)}>
            ${busy === b.id ? "Planning… (can take a minute)" : "Generate plan"}
          </button>`}
        </li>`)}
    </ul>
    <${ErrorNote} error=${error} />
  `;
}

function BriefForm({ projectId, onAdded }) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);

  async function run(action) {
    setError(null);
    try {
      const result = await action();
      setStatus(`Saved: ${result.chunks} chunks${result.embedded ? "" : " (not embedded: no embedding key configured)"}.`);
      onAdded();
      return true;
    } catch (err) {
      setError(err);
      return false;
    }
  }

  async function paste(e) {
    e.preventDefault();
    if (await run(() => api.pasteBrief(projectId, title.trim(), content))) {
      setTitle("");
      setContent("");
    }
  }

  async function upload(e) {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) await run(() => api.uploadBrief(projectId, file));
  }

  return html`
    <form class="brief-form" onSubmit=${paste}>
      <input value=${title} onInput=${(e) => setTitle(e.target.value)} placeholder="Brief title" maxlength="200" required />
      <textarea value=${content} onInput=${(e) => setContent(e.target.value)} rows="10" placeholder="Paste the brief (markdown)" required></textarea>
      <div class="row">
        <button type="submit">Save pasted brief</button>
        <label class="file">or upload .md / .txt / .pdf <input type="file" accept=".md,.markdown,.txt,.pdf" onChange=${upload} /></label>
      </div>
      ${status && html`<p class="muted">${status}</p>`}
      <${ErrorNote} error=${error} />
    </form>
  `;
}

function mondayOf(d) {
  const day = new Date(d);
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  const pad = (n) => String(n).padStart(2, "0");
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

function Summaries({ projectId, canEdit, clockNow }) {
  const { data, error, reload } = useLoad(() => api.summaries(projectId), [projectId]);
  const [week, setWeek] = useState(mondayOf(clockNow ? new Date(clockNow) : new Date())) // the workspace clock, not today;
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  async function generate(lane) {
    setBusy(lane);
    setErr(null);
    try { await api.createSummary(projectId, lane, week); reload(); } catch (e) { setErr(e); } finally { setBusy(null); }
  }
  const latest = {};
  for (const s of data || []) if (!latest[s.lane] || latest[s.lane].version < s.version) latest[s.lane] = s;
  return html`
    <section>
      <h2>Weekly summary</h2>
      ${canEdit && html`<div class="row"><label class="inline-label">Week of <input type="date" value=${week} onInput=${(e) => setWeek(e.target.value)} /></label>
        ${LANES.map((lane) => html`<button key=${lane} class="link" disabled=${busy !== null} onClick=${() => generate(lane)}>${busy === lane ? "Writing…" : `${latest[lane] ? "Regenerate" : "Write"} ${lane}`}</button>`)}</div>`}
      <${ErrorNote} error=${error || err} />
      ${LANES.filter((lane) => latest[lane]).map((lane) => html`
        <div key=${lane} class="summary"><b>${lane}</b> <span class="muted small">week of ${latest[lane].week_start} · v${latest[lane].version}${latest[lane].dropped ? ` · ${latest[lane].dropped} uncited sentence(s) removed` : ""}</span>
          ${latest[lane].sentences.map((s, n) => html`<p key=${n}>${s.text.split(/\[evt:(\d+)\]/).map((part, i) => i % 2 ? html`<a key=${i} class="cite" href=${`#/events/${part}`}>[${part}]</a>` : part)}</p>`)}
        </div>`)}
    </section>`;
}

function Duplicates({ canEdit }) {
  const { data, reload } = useLoad(() => api.duplicates(), []);
  if (!data || data.length === 0) return null;
  const decide = (id, d) => api.decideDuplicate(id, d).then(reload);
  return html`
    <section>
      <h2>Possible duplicates</h2>
      <p class="muted small">Suggestions only. Nothing is merged automatically.</p>
      <ul class="plain">${data.map((d) => html`<li key=${d.id}>"${d.item_title}" looks like "${d.duplicate_of_title}" <span class="muted small">(${d.reason})</span>
        ${canEdit && html` <button class="link" onClick=${() => decide(d.id, "confirm")}>Yes, duplicate</button> <button class="link" onClick=${() => decide(d.id, "dismiss")}>Not a duplicate</button>`}</li>`)}</ul>
    </section>`;
}
