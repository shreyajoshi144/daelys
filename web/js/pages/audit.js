import { api } from "../api.js";
import { ErrorNote, html, useLoad, useState } from "../lib.js";

function Summary({ value }) {
  if (value === null || value === undefined) return html`<span class="muted">—</span>`;
  return html`<code class="small">${JSON.stringify(value).slice(0, 160)}</code>`;
}

export function AuditPage() {
  const [actor, setActor] = useState("");
  const [tier, setTier] = useState("");
  const params = Object.fromEntries(Object.entries({ actor_type: actor, tier }).filter(([, v]) => v !== ""));
  const { data, error } = useLoad(() => api.audit(params), [actor, tier]);
  return html`
    <h1>Audit log</h1>
    <div class="filters">
      <label class="inline-label">Actor
        <select value=${actor} onChange=${(e) => setActor(e.target.value)}>
          <option value="">all</option><option value="human">human</option><option value="agent">agent</option><option value="system">system</option>
        </select></label>
      <label class="inline-label">Tier
        <select value=${tier} onChange=${(e) => setTier(e.target.value)}>
          <option value="">all</option><option value="2">2 (workspace)</option><option value="3">3 (external)</option>
        </select></label>
    </div>
    <${ErrorNote} error=${error} />
    <table class="audit">
      <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Tier</th><th>Before</th><th>After</th><th>Model</th></tr></thead>
      <tbody>
        ${(data ? data.items : []).map((a) => html`
          <tr key=${a.id}>
            <td class="small">${new Date(a.created_at).toLocaleString()}</td>
            <td>${a.actor_type}${a.actor_name ? `: ${a.actor_name}` : ""}${a.on_behalf_of_name ? html`<div class="muted small">for ${a.on_behalf_of_name}</div>` : ""}</td>
            <td>${a.action}</td>
            <td>${a.tier ?? ""}</td>
            <td><${Summary} value=${a.before} /></td>
            <td><${Summary} value=${a.after} /></td>
            <td class="small muted">${[a.model, a.prompt_version].filter(Boolean).join(" · ")}</td>
          </tr>`)}
      </tbody>
    </table>
  `;
}
