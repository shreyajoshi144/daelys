import { api } from "./api.js";
import { Icon, initials } from "./icons.js";
import { html, useEffect, useLiveUpdates, useLoad, useState } from "./lib.js";
import { Crop } from "./ui.js";

const SECTIONS = { projects: "Projects", proposals: "Proposals", meetings: "Meetings", ask: "Ask Daelys", audit: "Audit log", events: "Event" };
const HEALTH_CLASS = { "At risk": "at_risk", Watch: "watch", "On track": "ok" };

function SideLink({ href, icon, label, on, count }) {
  return html`<a class=${`side-link${on ? " on" : ""}`} href=${href} aria-current=${on ? "page" : undefined}>
    <${Icon} name=${icon} />${label}${count ? html`<span class="count" aria-label=${`${count} pending`}>${count}</span>` : null}</a>`;
}

// Demo and replay workspaces run on the scenario's clock; showing it explains dates like "due in 2 days".
function WorkspaceClock({ clockNow }) {
  if (!clockNow) return null;
  const at = new Date(clockNow);
  if (Math.abs(Date.now() - at.getTime()) < 36e5) return null; // a live workspace: the user's own clock is right
  const label = at.toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  return html`<span class="clock" title="This workspace runs on its scenario's clock">SCENARIO TIME · ${label}</span>`;
}

export function Shell({ me, route, onSignOut, children }) {
  const [open, setOpen] = useState(false);
  const projects = useLoad(() => api.projects(), []);
  const health = useLoad(() => api.health(), []);
  const pending = useLoad(() => api.proposals("pending"), [route[0]]);
  useLiveUpdates(health.reload);
  useEffect(() => {
    setOpen(false);
  }, [route.join("/")]);

  const canEdit = me.role === "owner" || me.role === "lead";
  const section = route[0] || "projects";
  const projectId = section === "projects" ? route[1] : null;
  const project = projectId && (projects.data || []).find((p) => p.id === projectId);
  const healthOf = (id) => health.data && health.data.projects.find((x) => x.project_id === id);
  const pendingCount = Array.isArray(pending.data) ? pending.data.length : 0;

  return html`
    <div class="shell">
      <aside class=${`sidebar${open ? " open" : ""}`} aria-label="Workspace">
        <div class="side-cover"><${Crop} name="water" /></div>
        <div class="ws">
          <span class="ws-avatar">${initials(me.tenant_name).slice(0, 1)}</span>
          <div style=${{ minWidth: 0 }}><div class="ws-name">${me.tenant_name}</div>
            <div class="ws-sub">${me.is_demo ? "Demo workspace" : "Workspace"}</div></div>
        </div>
        <div class="side-body">
          <a class="side-link side-ask" href="#/ask"><${Icon} name="search" />Ask Daelys</a>
          <${SideLink} href="#/projects" icon="home" label="Projects" on=${section === "projects" && !projectId} />
          <${SideLink} href="#/proposals" icon="inbox" label="Proposals" on=${section === "proposals"} count=${pendingCount} />
          <${SideLink} href="#/meetings" icon="mic" label="Meetings" on=${section === "meetings"} />
          ${canEdit && html`<${SideLink} href="#/audit" icon="shield" label="Audit log" on=${section === "audit"} />`}

          <div class="side-section">Projects</div>
          ${(projects.data || []).map((p) => {
            const h = healthOf(p.id);
            return html`<a key=${p.id} class=${`side-link side-project${p.id === projectId ? " on" : ""}`} href=${`#/projects/${p.id}`}
              title=${h ? `${p.name}: ${h.health}` : p.name}>
              <span class=${`dot ${h ? HEALTH_CLASS[h.health] || "unknown" : "unknown"}`}></span><span class="name">${p.name}</span></a>`;
          })}
          ${projects.data && projects.data.length === 0 && html`<p class="ws-sub" style=${{ padding: "0 .6rem" }}>No projects yet.</p>`}

          <div class="side-foot">
            <span class="avatar">${initials(me.display_name)}</span>
            <div class="who"><b>${me.display_name}</b><span>${me.role}</span></div>
            <button class="ghost" onClick=${onSignOut} title="Sign out" aria-label="Sign out"><${Icon} name="logout" /></button>
          </div>
        </div>
      </aside>
      <div class=${`scrim${open ? " open" : ""}`} onClick=${() => setOpen(false)}></div>

      <div class="main">
        <header class="topbar">
          <button class="ghost menu-btn" onClick=${() => setOpen(true)} aria-label="Open navigation"><${Icon} name="menu" /></button>
          <nav class="crumbs" aria-label="Breadcrumb">
            <a href="#/projects">${me.tenant_name}</a><span class="sep">/</span>
            ${project
              ? html`<a href="#/projects">Projects</a><span class="sep">/</span><span class="here">${project.name}${route[2] === "plan" ? " · Plan review" : ""}</span>`
              : html`<span class="here">${SECTIONS[section] || "Projects"}</span>`}
          </nav>
          <${WorkspaceClock} clockNow=${me.clock_now} />
        </header>
        <main class="page">${children}</main>
      </div>
    </div>`;
}
