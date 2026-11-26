import { Mark } from "../icons.js";
import { html, navigate } from "../lib.js";
import { Collage, Crop } from "../ui.js";

// Hash routing owns the URL fragment, so in-page links scroll by id instead of #anchors.
const scrollTo = (id) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
const demo = () => navigate("/login/tiffin-box");

// Event types Daelys really reads and writes (connectors/, core/meetings.py, workers/execute.py).
const EVENTS = [
  "pr.opened", "ci.completed", "issue.commented", "blocker.mentioned", "meeting.transcribed", "decision.made",
  "action.proposed", "calendar.event_scheduled", "plan.item_created", "pr.ready_for_review", "pr.merged",
  "action.due_moved", "issue.closed",
];

const CAPABILITIES = [
  ["A plan from a messy brief", "Paste the launch doc. Daelys proposes lanes, owners and dependencies, and every task links back to the sentence it came from.", "Plan accepted", ""],
  ["Risk you can audit", "Five rules you can read score every task from GitHub, meetings and the calendar. Slips carry down dependencies.", "Computed", ""],
  ["The why, with receipts", "Each risk is explained in plain words. Every sentence cites the event behind it, or it isn't shown.", "Cited", ""],
  ["Meetings that update the plan", "Standup transcripts, in English or Hinglish, become decisions, blockers and action items tied to real tasks.", "Transcribed", ""],
  ["Fixes that wait for you", "Reassign, move a date, split, notify or comment on a PR: proposed by the agent, approved by a person, audited.", "For review", "for-review"],
  ["Ask the workspace", "Ask what is blocking checkout. Answers come only from what you're allowed to see, with sources.", "Approved", "approved"],
];

const PRINCIPLES = [
  ["Computed, not generated", "Risk scores come from five SQL rules over your event log. A model can explain a score; it can never set one."],
  ["Evidence or silence", "Every generated sentence cites the events behind it. Anything uncited is dropped before anyone sees it."],
  ["Proposes, never acts", "The agent suggests one of five typed fixes. A lead approves, and the executor runs it exactly once, audited, with undo."],
  ["Isolated by the database", "Postgres row-level security on every table. Search filters by role before ranking, so nothing crosses teams."],
];

// Callouts on the real board screenshot (% of the image), placed at the edge of what they point to.
const CALLOUTS = [
  [20, 16.5, "Health is computed by rules from the event log, never by a model."],
  [50.8, 54, "At-risk work is marked by weight, not colour, and slips carry down dependencies."],
  [68.6, 50.8, "Every risk explains itself, citing the events that caused it."],
  [68.6, 63, "Evidence is the real event: the red CI run, the blocker said in standup."],
];

export function NavPill() {
  return html`<nav class="nav-pill" aria-label="Main">
    <a class="home" href="#/" aria-label="Daelys home"><${Mark} /></a>
    <button class="hide-sm" onClick=${() => scrollTo("proof")}>Product</button>
    <button class="hide-sm" onClick=${() => scrollTo("principles")}>Principles</button>
    <button class="hide-sm" onClick=${() => scrollTo("capabilities")}>Capabilities</button>
    <button onClick=${() => navigate("/login")}>Sign in</button>
    <button class="primary" onClick=${demo}>Open the demo <span class="arrow-chip">→</span></button>
  </nav>`;
}

// The centre of the hero: Daelys's own loop, laid out the way the collage lays out its stages.
function HeroPanel() {
  return html`<div class="hero-panel">
    <span class="stage s1">Brief<sup>01</sup></span>
    <span class="stage s2">Signals<sup>02</sup></span>
    <span class="stage s3">Review<sup>03</sup></span>
    <span class="stage s4">Action<sup>04</sup></span>
    <div class="hero-copy enter">
      <p class="kicker">Launch risk, computed, not guessed</p>
      <h1>See what's slipping<br />before the launch does.</h1>
      <p class="sub">Daelys turns a messy brief into a plan, watches the real work, explains every risk with
        evidence, and proposes fixes a person approves.</p>
      <div class="row"><button class="primary" onClick=${demo}>Open the demo <span class="arrow-chip">→</span></button>
        <button onClick=${() => scrollTo("proof")}>See the product</button></div>
    </div>
    <div class="fan" aria-hidden="true">
      <div class="fc doc"><b>Launch brief</b><i></i><i></i><i></i><i class="short"></i></div>
      <div class="fc photo"><${Crop} name="clouds" /><span>Checkout · at risk</span></div>
      <div class="fc score"><span class="num">94</span><span class="lbl">risk / 100</span></div>
      <div class="fc signal"><b>CI failed 3× near due date</b>pr #21 · due in 2 days</div>
      <span class="tag for-review t1">For review</span>
      <span class="tag approved t2">Approved</span>
      <div class="fc note"><b>Arjun · lead</b><small>2h ago</small>“Finance confirmed the sandbox keys for Thursday.”</div>
      <div class="fc file f1">.pr</div><div class="fc file f2">.txt</div>
    </div>
  </div>`;
}

export function LandingPage() {
  return html`
    <div class="landing">
      <header class="landing-hero on-collage">
        <div class="collage-top"><${NavPill} /></div>
        <${Collage}><${HeroPanel} /><//>
      </header>

      <div class="ticker" aria-label="Events Daelys reads">
        <div class="ticker-track">${[...EVENTS, ...EVENTS].map((e, i) => html`<span key=${i} aria-hidden=${i >= EVENTS.length}><i></i>${e}</span>`)}</div>
      </div>

      <main>
        <section class="proof wrap" id="proof">
          <div class="section-head">
            <span class="kicker">The product</span>
            <h2>One board. Every risk with its receipts.</h2>
            <p>This is the real demo workspace in week two of a subscription launch, not a mock-up.</p>
          </div>
          <figure class="shot">
            <img src="/img/board.png" alt="The Daelys project board: three tasks at risk, with the why panel citing the failing CI runs and the blocker" loading="lazy" />
            ${CALLOUTS.map(([x, y], i) => html`<span key=${i} class="pin" style=${{ left: `${x}%`, top: `${y}%` }}>0${i + 1}</span>`)}
          </figure>
          <ol class="legend">${CALLOUTS.map(([, , text], i) => html`<li key=${i}><span class="pin">0${i + 1}</span>${text}</li>`)}</ol>
        </section>

        <section class="blue-band" id="principles">
          <div class="wrap">
            <div class="band-copy">
              <span class="kicker">Principles</span>
              <h2>Built so engineering leaders can trust what it says.</h2>
              <p>Most AI tools ask you to believe them. Daelys shows its work, and changes nothing until a person says yes.</p>
            </div>
            <ol class="principles">${PRINCIPLES.map(([title, text], i) => html`
              <li key=${title}><span class="no">0${i + 1}</span><h3>${title}</h3><p>${text}</p></li>`)}</ol>
          </div>
        </section>

        <section class="wrap" id="capabilities">
          <div class="section-head">
            <span class="kicker">Capabilities</span>
            <h2>Everything a launch lead checks every morning</h2>
          </div>
          <ol class="ruled">${CAPABILITIES.map(([title, text, tag, kind], i) => html`
            <li key=${title}><span class="no">0${i + 1}</span><h3>${title}</h3><p>${text}</p>
              <span class=${`tag ${kind}`}>${tag}</span></li>`)}</ol>

          <div class="cta">
            <div class="copy">
              <h3>See a launch go off track, then put it back.</h3>
              <p>The demo workspace replays two weeks of a subscription launch. Follow the risk as it appears, read the
                evidence, and approve the fix. Daelys is in an invite-only pilot.</p>
              <div class="row"><button onClick=${demo}>Open the demo <span class="arrow-chip">→</span></button>
                <button class="outline" onClick=${() => navigate("/login")}>Sign in</button></div>
            </div>
          </div>
          <div class="facts-row">
            <div><div class="k">Security</div>Row-level tenant isolation, CSRF-protected sessions, every action audited</div>
            <div><div class="k">Integrations</div>GitHub App, meeting transcripts, calendar (.ics)</div>
            <div><div class="k">Stack</div>FastAPI, Postgres 16 + pgvector, Redis, React</div>
          </div>
          <footer class="l-foot">
            <nav><button onClick=${() => window.scrollTo({ top: 0, behavior: "smooth" })}>Home</button>
              <button onClick=${() => scrollTo("proof")}>Product</button><button onClick=${() => scrollTo("principles")}>Principles</button>
              <button onClick=${() => scrollTo("capabilities")}>Capabilities</button><button onClick=${() => navigate("/login")}>Sign in</button></nav>
            <span class="wordmark"><${Mark} />Daelys <span class="muted small">© 2026</span></span>
          </footer>
        </section>
      </main>
      <div class="foot-strip" aria-hidden="true"></div>
    </div>`;
}
