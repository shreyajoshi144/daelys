import { api } from "../api.js";
import { ErrorNote, html, safeExternal, useLoad, useState } from "../lib.js";
import { Loading, toast } from "../ui.js";

const RULES = {
  stale_pr: "Linked PR has gone quiet",
  ci_failing_near_due: "CI failing close to the due date",
  overdue_upstream: "Something it depends on is overdue or blocked",
  explicit_blocker: "Blocker reported",
  owner_overloaded: "Owner has too much due this week",
};

export function evidenceHref(ev) {
  const external = safeExternal(ev.payload && ev.payload.url) || safeExternal(ev.item_url);
  return external ? { href: external, target: "_blank", rel: "noopener noreferrer" } : { href: `#/events/${ev.id}` };
}

function Detail({ details }) {
  const parts = Object.entries(details || {})
    .filter(([, v]) => v !== null && v !== "" && typeof v !== "object")
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`);
  return parts.length ? html`<p class="muted small">${parts.join(" · ")}</p>` : null;
}

function Cited({ sentence, byId }) {
  const pieces = sentence.text.split(/\[evt:(\d+)\]/);
  return html`<p>${pieces.map((piece, n) => {
    if (n % 2 === 0) return piece;
    const ev = byId[Number(piece)];
    return ev ? html`<a key=${n} class="cite" ...${evidenceHref(ev)}>[${piece}]</a>` : null;
  })}</p>`;
}

function SignalBlock({ signal }) {
  const [explanation, setExplanation] = useState(signal.explanation);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const byId = Object.fromEntries(signal.evidence.map((e) => [e.id, e]));

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setExplanation(await api.explain(signal.id));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return html`
    <section class="signal">
      <h3><span class=${`sev ${signal.severity}`}>${signal.severity}</span> ${RULES[signal.rule] || signal.rule}</h3>
      <${Detail} details=${signal.details} />
      ${explanation && html`
        <div class="explanation">
          ${explanation.sentences.map((s, n) => html`<${Cited} key=${n} sentence=${s} byId=${byId} />`)}
          ${explanation.sentences.length === 0 && html`<p class="muted">No sentence could be backed by evidence.</p>`}
        </div>`}
      ${!explanation && html`<button class="link" onClick=${run} disabled=${busy}>${busy ? "Explaining…" : "Explain in plain words"}</button>`}
      <${ErrorNote} error=${error} />
      <h4>Evidence</h4>
      <ol class="evidence">
        ${signal.evidence.map((ev) => html`
          <li key=${ev.id}>
            <a ...${evidenceHref(ev)}>[${ev.id}] ${ev.type}</a>
            <span class="muted"> · ${new Date(ev.occurred_at).toLocaleString()}${ev.item_title ? ` · ${ev.item_title}` : ""}</span>
          </li>`)}
      </ol>
    </section>`;
}

function ProposeButton({ itemId }) {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    setError(null);
    try {
      setState(await api.generateProposals(itemId));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return html`
    <div class="propose">
      <button onClick=${run} disabled=${busy}>${busy ? "Asking the agent…" : "Propose fixes"}</button>
      ${state && html`<p class="muted small">${state.created.length} proposal(s) for review${state.rejected.length ? `, ${state.rejected.length} rejected by validation` : ""}. <a href="#/proposals">Open the inbox</a></p>`}
      <${ErrorNote} error=${error} />
    </div>`;
}

export function WhyPanel({ itemId, onClose, version, canEdit }) {
  const { data, error } = useLoad(() => api.itemSignals(itemId), [itemId, version]);
  return html`
    <aside class="why" aria-label="Why this item is at risk">
      <button class="link close" onClick=${onClose}>Close</button>
      <${ErrorNote} error=${error} />
      ${data && html`
        <h2>${data.item.title}</h2>
        <p class="muted">Risk ${data.item.risk_score}/100${data.item.projected_slip_days > 0 ? ` · projected slip ${data.item.projected_slip_days.toFixed(1)} days` : ""}</p>
        ${data.signals.length === 0 && html`<p class="muted">No open signals. ${data.item.projected_slip_days > 0 ? "Its risk comes from delay upstream (see the dependency graph)." : ""}</p>`}
        ${data.signals.map((s) => html`<${SignalBlock} key=${s.id} signal=${s} />`)}
        ${canEdit && data.item.status !== "done" && html`<${ProposeButton} itemId=${itemId} />`}
        <${Comments} itemId=${itemId} version=${version} />
      `}
    </aside>`;
}

export function EventPage({ eventId }) {
  const { data, error } = useLoad(() => api.event(eventId), [eventId]);
  if (error) return html`<${ErrorNote} error=${error} />`;
  if (!data) return html`<${Loading} />`;
  const external = safeExternal(data.payload && data.payload.url) || safeExternal(data.item_url);
  return html`
    <h1>Event ${data.id}</h1>
    <dl class="facts">
      <dt>Type</dt><dd>${data.type}</dd>
      <dt>When</dt><dd>${new Date(data.occurred_at).toLocaleString()}</dd>
      <dt>Source</dt><dd>${data.source}</dd>
      <dt>Actor</dt><dd>${data.actor || "—"}</dd>
      <dt>About</dt><dd>${data.item_title || data.subject_key || "—"}</dd>
      ${external && html`<dt>Link</dt><dd><a href=${external} target="_blank" rel="noopener noreferrer">${external}</a></dd>`}
    </dl>
    <h2>Payload</h2>
    <pre class="payload">${JSON.stringify(data.payload, null, 2)}</pre>`;
}

function Comments({ itemId, version }) {
  const { data, reload } = useLoad(() => api.comments(itemId), [itemId, version]);
  const [body, setBody] = useState("");
  const [error, setError] = useState(null);
  async function submit(e) {
    e.preventDefault();
    try { await api.addComment(itemId, body.trim()); setBody(""); reload(); toast("Comment posted"); } catch (err) { setError(err); }
  }
  return html`
    <section class="comments">
      <h4>Comments</h4>
      ${(data || []).map((c) => html`<p key=${c.id} class="small"><b>${c.author}</b> ${c.body} <span class="muted">${new Date(c.created_at).toLocaleString()}</span></p>`)}
      <form class="inline" onSubmit=${submit}><input value=${body} onInput=${(e) => setBody(e.target.value)} placeholder="Add a comment" maxlength="4000" required /><button type="submit">Post</button></form>
      <${ErrorNote} error=${error} />
    </section>`;
}
