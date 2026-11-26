import { html, useState } from "../lib.js";
import { Collage } from "../ui.js";

// Shown once per person per browser, between sign-in and the workspace. Three short steps, always skippable.
const KEY = (userId) => `daelys.onboarded.${userId}`;

export function needsOnboarding(me) {
  try {
    return window.localStorage.getItem(KEY(me.user_id)) !== "1";
  } catch {
    return false; // storage blocked: never trap anyone in onboarding
  }
}

function markDone(me) {
  try {
    window.localStorage.setItem(KEY(me.user_id), "1");
  } catch {
    // Without storage the tour may show again next time; harmless.
  }
}

const CAN_APPROVE = { owner: "approve any proposal", lead: "approve proposals for projects you lead" };

function steps(me) {
  const first = me.display_name.split(" ")[0];
  return [
    {
      title: `Welcome to ${me.tenant_name}, ${first}`,
      body: html`<p>Daelys watches the real work of your launches and tells you what is slipping, and why, before
        it becomes a surprise.</p><p>You're signed in as <b>${me.role}</b>${me.is_demo ? " in the demo workspace" : ""}.</p>`,
    },
    {
      title: "Risk comes from what happened",
      body: html`<ul><li>Pull requests, CI runs, standups and calendars become events.</li>
        <li>Five rules you can read score every task. Slips carry down dependencies.</li>
        <li>Click any task to see why, with every claim linked to its evidence.</li></ul>`,
    },
    {
      title: "Nothing changes without a person",
      body: html`<p>The agent can propose a fix: reassign, move a date, split, notify or comment on a PR.</p>
        <p>${CAN_APPROVE[me.role] ? `As ${me.role}, you can ${CAN_APPROVE[me.role]}, and every decision is audited.`
          : "Owners and project leads approve; every decision is audited and you can follow it in Proposals."}</p>`,
    },
  ];
}

export function Onboarding({ me, onDone }) {
  const [n, setN] = useState(0);
  const all = steps(me);
  const finish = () => { markDone(me); onDone(); };
  const step = all[n];
  return html`<div class="onboard on-collage"><${Collage}>
    <section class="paper-card onboard-card" key=${n} aria-labelledby="onboard-title">
      <div class="step-n">STEP 0${n + 1} / 0${all.length}</div>
      <h1 id="onboard-title">${step.title}</h1>
      ${step.body}
      <div class="progress" aria-hidden="true">${all.map((_, i) => html`<i key=${i} class=${i <= n ? "on" : ""}></i>`)}</div>
      <div class="row">
        <button class="link" onClick=${finish}>Skip</button>
        <div class="row">
          ${n > 0 && html`<button onClick=${() => setN(n - 1)}>Back</button>`}
          ${n < all.length - 1
            ? html`<button class="primary" onClick=${() => setN(n + 1)}>Next</button>`
            : html`<button class="primary" onClick=${finish}>Enter the workspace <span class="arrow-chip">→</span></button>`}
        </div>
      </div>
    </section>
  <//></div>`;
}
