import { api } from "../api.js";
import { ErrorNote, html, useLiveUpdates, useLoad, useState } from "../lib.js";
import { Empty, toast } from "../ui.js";
import { evidenceHref } from "./why.js";

const ACTION_LABELS = {
  reassign: "Reassign", move_due: "Move due date", split: "Split into smaller items",
  notify: "Send email", comment_on_pr: "Comment on pull request",
};
const STATUSES = ["pending", "approved", "executed", "rejected", "undone", "failed", "expired"];

function fmtDate(value) {
  return value ? new Date(value).toLocaleString() : "none";
}

// What will change, shown before anyone approves. Tier 3 shows the exact external text.
function Preview({ p }) {
  const v = p.preview;
  if (p.action_type === "reassign" || p.action_type === "move_due") {
    const show = p.action_type === "move_due" ? fmtDate : (x) => x;
    return html`<div class="diff"><span class="muted">${v.field}</span>
      <del>${show(v.before)}</del> → <ins>${show(v.after)}</ins></div>`;
  }
  if (p.action_type === "split") {
    return html`<div class="diff"><del>${v.parent}</del> →
      <ul>${v.into.map((part, n) => html`<li key=${n}><ins>${part.title}</ins> <span class="muted">${part.estimate_days}d</span></li>`)}</ul></div>`;
  }
  if (p.action_type === "notify") {
    return html`<div class="diff external"><div class="muted small">${v.channel} · to ${v.to.join(", ")}</div>
      <div><b>${v.subject}</b></div><pre class="exact">${v.body}</pre></div>`;
  }
  return html`<div class="diff external"><div class="muted small">Exact comment to be posted on ${v.pull_request}</div>
    <pre class="exact">${v.text}</pre></div>`;
}

function EditForm({ p, onDone, onCancel }) {
  const [args, setArgs] = useState(p.args);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setArgs({ ...args, [k]: e.target.value });
  async function save(e) {
    e.preventDefault();
    try {
      await api.approve(p.id, args);
      onDone();
    } catch (err) {
      setError(err);
    }
  }
  let fields;
  if (p.action_type === "move_due") {
    fields = html`<label>New due date<input type="date" value=${args.new_due_date} onInput=${set("new_due_date")} required /></label>`;
  } else if (p.action_type === "comment_on_pr") {
    fields = html`<label>Comment<textarea rows="5" value=${args.body} onInput=${set("body")} required></textarea></label>`;
  } else if (p.action_type === "notify") {
    fields = html`
      <label>Subject<input value=${args.subject} onInput=${set("subject")} required /></label>
      <label>Body<textarea rows="5" value=${args.body} onInput=${set("body")} required></textarea></label>`;
  } else {
    fields = html`<p class="muted small">This action can only be approved as proposed or rejected.</p>`;
  }
  return html`<form class="edit" onSubmit=${save}>${fields}
    <div class="row"><button type="submit">Approve edited</button><button type="button" class="link" onClick=${onCancel}>Cancel</button></div>
    <${ErrorNote} error=${error} /></form>`;
}

function ProposalCard({ p, canEdit, onChange }) {
  const [mode, setMode] = useState(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState(null);
  const run = (fn, done) => async (e) => {
    if (e) e.preventDefault();
    setError(null);
    try {
      await fn();
      toast(done);
      setMode(null);
      onChange();
    } catch (err) {
      setError(err);
    }
  };
  const decidable = canEdit && p.status === "pending";
  return html`
    <article class=${`proposal ${p.status}`}>
      <header>
        <span class=${`tier t${p.tier}`}>tier ${p.tier}</span>
        <b>${ACTION_LABELS[p.action_type]}</b> · ${p.item_title}
        <span class="muted small"> · ${p.status}${p.approval_source === "policy" ? " (policy)" : ""} · expires ${fmtDate(p.expires_at)}</span>
      </header>
      <${Preview} p=${p} />
      <p class="small">${p.expected_effect}</p>
      <p class="muted small">Evidence: ${p.evidence.map((ev) => html`<a key=${ev.id} ...${evidenceHref(ev)}>[${ev.id}] ${ev.type}</a> `)}</p>
      ${p.rejection_reason && html`<p class="small">Rejected: ${p.rejection_reason}</p>`}
      ${p.execution_result && p.status !== "executed" && html`<p class="small muted">${p.execution_result.detail || ""}</p>`}
      ${decidable && mode === null && html`
        <div class="row">
          <button class="primary" onClick=${run(() => api.approve(p.id), "Approved. Daelys will carry it out once.")}>Approve</button>
          <button class="link" onClick=${() => setMode("edit")}>Edit, then approve</button>
          <button class="link" onClick=${() => setMode("reject")}>Reject</button>
        </div>`}
      ${mode === "edit" && html`<${EditForm} p=${p} onDone=${() => { setMode(null); onChange(); }} onCancel=${() => setMode(null)} />`}
      ${mode === "reject" && html`
        <form class="edit" onSubmit=${run(() => api.reject(p.id, reason.trim()), "Rejected. The agent will see your reason next time.")}>
          <label>Why? (the agent sees this next time)<textarea rows="2" value=${reason} onInput=${(e) => setReason(e.target.value)} minlength="3" required></textarea></label>
          <div class="row"><button type="submit">Reject</button><button type="button" class="link" onClick=${() => setMode(null)}>Cancel</button></div>
        </form>`}
      ${canEdit && p.status === "executed" && p.tier === 2 && html`<button class="link" onClick=${run(() => api.undo(p.id), "Undone")}>Undo</button>`}
      <${ErrorNote} error=${error} />
    </article>`;
}

export function ProposalsPage({ canEdit }) {
  const [status, setStatus] = useState("pending");
  const { data, error, reload } = useLoad(() => api.proposals(status), [status]);
  useLiveUpdates(reload);
  return html`
    <h1>Proposals</h1>
    <p class="muted">The agent only proposes. Nothing changes until an owner or the project's lead approves.</p>
    <div class="filters">${["", ...STATUSES].map((s) => html`
      <button key=${s || "all"} class=${s === status ? "chip on" : "chip"} onClick=${() => setStatus(s)}>${s || "all"}</button>`)}</div>
    <${ErrorNote} error=${error} />
    ${data && data.length === 0 && html`<${Empty} title=${status === "pending" ? "Nothing waiting for you" : "Nothing here"} crop="leafshadow">
      ${status === "pending" ? "When the agent proposes a fix for an at-risk task, it waits here for an owner or lead." : "No proposals with this status."}<//>`}
    ${(data || []).map((p) => html`<${ProposalCard} key=${p.id} p=${p} canEdit=${canEdit} onChange=${reload} />`)}
  `;
}
