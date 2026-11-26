import { api } from "../api.js";
import { Icon } from "../icons.js";
import { confirmDialog, Crop, Empty, toast } from "../ui.js";
import { ErrorNote, HealthCard, html, navigate, useLiveUpdates, useLoad, useState } from "../lib.js";

export function ProjectsPage({ canEdit, isOwner, isDemo, me }) {
  const { data: projects, error, reload } = useLoad(() => api.projects(), []);
  const health = useLoad(() => api.health(), []);
  useLiveUpdates(health.reload);
  const [name, setName] = useState("");
  const [createError, setCreateError] = useState(null);

  async function create(e) {
    e.preventDefault();
    try {
      const project = await api.createProject(name.trim());
      setName("");
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setCreateError(err);
      reload();
    }
  }

  const firstName = (me.display_name || "").split(" ")[0];
  return html`
    <div class="page-head">
      <div><h1>${greeting()}${firstName ? `, ${firstName}` : ""}</h1>
        <p>Every launch in ${me.tenant_name}, with health computed from what actually happened.</p></div>
    </div>
    ${health.data && html`<${HealthCard} title="Workspace health" health=${health.data.health}
      detail=${`${health.data.counts.at_risk} at risk · ${health.data.counts.watch} to watch`} />`}
    <${ErrorNote} error=${error} />
    <h2>Projects</h2>
    <div class="cards">
      ${(projects || []).map((p, i) => {
        const h = health.data && health.data.projects.find((x) => x.project_id === p.id);
        return html`<a key=${p.id} class="card project-card" href=${`#/projects/${p.id}`}>
          <span class="band" aria-hidden="true"><${Crop} name=${BANDS[i % BANDS.length]} /></span>
          <span class="name">${p.name}</span>
          ${h && html`<span class="status"><span class=${`dot ${HEALTH_CLASS[h.health] || "ok"}`}></span>${h.health}</span>`}
          <span class="foot">Created ${new Date(p.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
            <span class="spacer"></span><${Icon} name="arrow" small /></span>
        </a>`;
      })}
      ${canEdit && html`
        <form class="card new-card" onSubmit=${create}>
          <span class="status"><${Icon} name="plus" small />New project</span>
          <input value=${name} onInput=${(e) => setName(e.target.value)} placeholder="Project name" maxlength="200" required aria-label="New project name" />
          <button type="submit">Create project</button>
          <${ErrorNote} error=${createError} />
        </form>`}
    </div>
    ${projects && projects.length === 0 && !canEdit && html`<${Empty} title="No projects yet">A lead or owner can create the first one.<//>`}
    ${isOwner && html`
      <h2>Workspace tools</h2>
      <div class="tools">
        ${isDemo && html`<${ResetDemo} onDone=${health.reload} />`}
        <${ReplayForm} />
      </div>`}
  `;
}

const HEALTH_CLASS = { "At risk": "at_risk", Watch: "watch", "On track": "ok" };
const BANDS = ["water", "blossom", "leafshadow", "clouds", "leaf"];

function greeting() {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function ReplayForm() {
  const [scenario, setScenario] = useState("tiffin_box");
  const [timelines, setTimelines] = useState("week1,week2");
  const [speed, setSpeed] = useState("600");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function start(e) {
    e.preventDefault();
    setError(null);
    try {
      setResult(await api.replay(scenario.trim(), timelines.split(",").map((t) => t.trim()).filter(Boolean), Number(speed) || 0));
    } catch (err) {
      setError(err);
    }
  }

  return html`
    <section class="card">
      <h3><${Icon} name="clock" small /> Time machine</h3>
      <p class="muted small">Replay a scenario into a fresh copy of this workspace: same people, projects and briefs, no events.</p>
      <form class="row replay" onSubmit=${start}>
        <label class="inline-label small">Scenario<input value=${scenario} onInput=${(e) => setScenario(e.target.value)} required /></label>
        <label class="inline-label small">Timelines<input value=${timelines} onInput=${(e) => setTimelines(e.target.value)} required /></label>
        <label class="inline-label small">Speed<input value=${speed} onInput=${(e) => setSpeed(e.target.value)} inputmode="numeric" size="5" /></label>
        <button type="submit">Replay</button>
      </form>
      ${result && html`<p class="small">Replaying into workspace <b>${result.tenant_slug}</b>. ${result.detail}</p>`}
      <${ErrorNote} error=${error} />
    </section>
  `;
}

function ResetDemo({ onDone }) {
  const [state, setState] = useState(null);
  async function reset() {
    const ok = await confirmDialog({
      title: "Reset the demo?",
      body: "The workspace goes back to the end of week 1. Proposals, meetings and comments are cleared; people and projects stay.",
      confirmLabel: "Reset scenario",
    });
    if (!ok) return;
    try {
      const res = await api.resetScenario();
      setState({ message: `Reset: ${res.replayed_events} week-1 events replayed. Use the time machine to replay week 2 and watch the risk flip.` });
      toast("Demo reset to week 1");
      onDone();
    } catch (err) {
      setState(err);
    }
  }
  return html`<section class="card">
    <h3><${Icon} name="pulse" small /> Demo scenario</h3>
    <p class="muted small">Return this demo workspace to the start of week 1. Proposals, meetings and comments are cleared.</p>
    <button onClick=${reset}>Reset scenario</button>
    ${state && html`<p class=${state.status ? "error" : "muted small"}>${state.message}</p>`}</section>`;
}
