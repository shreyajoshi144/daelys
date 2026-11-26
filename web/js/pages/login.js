import { api } from "../api.js";
import { Mark } from "../icons.js";
import { ErrorNote, html, navigate, useState } from "../lib.js";
import { Collage } from "../ui.js";

const LAST_WORKSPACE = "daelys.lastWorkspace";

function remembered() {
  try {
    return window.localStorage.getItem(LAST_WORKSPACE) || "";
  } catch {
    return "";
  }
}

function remember(slug) {
  try {
    window.localStorage.setItem(LAST_WORKSPACE, slug);
  } catch {
    // Storage can be blocked (private mode); remembering the workspace is only a convenience.
  }
}

// The sign-in screen: the collage frames a paper card, the same sage paper the workspace is made of.
export function AuthFrame({ children }) {
  return html`<div class="auth on-collage">
    <div class="collage-top"><nav class="nav-pill" aria-label="Main">
      <a class="home" href="#/" aria-label="Daelys home"><${Mark} /></a>
      <a class="navlink" href="#/">Back to home</a>
      <button class="primary" onClick=${() => navigate("/login/tiffin-box")}>Open the demo <span class="arrow-chip">→</span></button>
    </nav></div>
    <${Collage}>${children}<//>
    <p class="collage-foot"><span>Daelys · invite-only pilot</span></p>
  </div>`;
}

function SignIn({ workspace, onSignedIn, show }) {
  const [form, setForm] = useState({ tenant: workspace || remembered(), email: "", password: "" });
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const tenant = form.tenant.trim().toLowerCase();
      await api.login(tenant, form.email.trim(), form.password);
      remember(tenant);
      onSignedIn(await api.me());
    } catch (err) {
      setError(err.status === 401 ? new Error("That workspace, email and password don't match.") : err);
    } finally {
      setBusy(false);
    }
  }

  const demo = form.tenant.trim() === "tiffin-box";
  return html`<section class="paper-card" aria-labelledby="auth-title">
    <h1 id="auth-title">Welcome back</h1>
    <p class="sub">${demo ? "You're entering the demo workspace." : "Sign in to your workspace."}</p>
    <form onSubmit=${submit}>
      <label>Workspace
        <div class="field-prefix"><span>daelys /</span>
          <input value=${form.tenant} onInput=${set("tenant")} placeholder="your-team" autocomplete="organization"
            autocapitalize="none" spellcheck="false" aria-label="Workspace" required /></div>
      </label>
      <label>Work email<input type="email" value=${form.email} onInput=${set("email")} placeholder="you@company.com"
        autocomplete="username" required /></label>
      <label class="pw">Password<input type=${showPassword ? "text" : "password"} value=${form.password}
        onInput=${set("password")} autocomplete="current-password" aria-label="Password" required />
        <button type="button" onClick=${() => setShowPassword(!showPassword)} aria-label=${showPassword ? "Hide password" : "Show password"}>
          ${showPassword ? "Hide" : "Show"}</button>
      </label>
      <${ErrorNote} error=${error} />
      <button type="submit" disabled=${busy}>${busy ? "Signing in…" : "Continue"}</button>
    </form>
    <div class="alt">
      <button class="link" onClick=${() => show("forgot")}>Forgot password?</button>
      <button class="link" onClick=${() => show("access")}>Need access?</button>
    </div>
    ${demo && html`<p class="hint">The demo login comes from whoever set Daelys up for you (the seeded owner is
      meera@tiffinbox.example).</p>`}
  </section>`;
}

// No self-service reset or signup exists in the pilot; these screens say what actually happens.
function Info({ title, children, show }) {
  return html`<section class="paper-card" aria-labelledby="auth-title">
    <h1 id="auth-title">${title}</h1>
    ${children}
    <div class="alt"><button class="link" onClick=${() => show("signin")}>← Back to sign in</button></div>
  </section>`;
}

export function LoginPage({ workspace, onSignedIn }) {
  const [view, setView] = useState("signin");
  return html`<${AuthFrame}>
    ${view === "signin" && html`<${SignIn} key=${workspace || "none"} workspace=${workspace} onSignedIn=${onSignedIn} show=${setView} />`}
    ${view === "forgot" && html`<${Info} title="Forgot your password?" show=${setView}>
      <p class="sub">During the pilot, passwords are set by your workspace owner. Ask them to reset yours, and
        you'll be back in within a minute.</p>
      <p class="sub">For the demo workspace, the password is the <code>SEED_USER_PASSWORD</code> in the server's
        <code>.env</code>.</p><//>`}
    ${view === "access" && html`<${Info} title="Getting access" show=${setView}>
      <p class="sub">Daelys is invite-only while we pilot it with a few teams. Your workspace owner adds you, and
        your role decides what you can approve.</p>
      <p class="sub">Just looking around? The demo workspace has a full launch to explore.</p>
      <button class="primary" onClick=${() => { setView("signin"); navigate("/login/tiffin-box"); }}>Open the demo</button><//>`}
  <//>`;
}
