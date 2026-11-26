import { api } from "../api.js";
import { ErrorNote, html, useState } from "../lib.js";

function Sentence({ s }) {
  const parts = s.text.split(/\[(evt|doc):([0-9a-f-]+)\]/);
  const out = [];
  for (let i = 0; i < parts.length; i += 3) {
    out.push(parts[i]);
    if (parts[i + 1] === "evt") out.push(html`<a key=${i} class="cite" href=${`#/events/${parts[i + 2]}`}>[event ${parts[i + 2]}]</a>`);
    if (parts[i + 1] === "doc") out.push(html`<span key=${i} class="cite">[doc]</span>`);
  }
  return html`<p>${out}</p>`;
}

export function AskPage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { setResult(await api.ask(question.trim())); } catch (err) { setError(err); } finally { setBusy(false); }
  }
  return html`
    <h1>Ask Daelys</h1>
    <p class="muted">Answers only from documents you are allowed to see and from the event log, with sources. If the evidence isn't there, it says so.</p>
    <form class="inline" onSubmit=${submit}>
      <input value=${question} onInput=${(e) => setQuestion(e.target.value)} placeholder="What is blocking checkout?" minlength="3" required />
      <button type="submit" disabled=${busy}>${busy ? "Thinking…" : "Ask"}</button>
    </form>
    <${ErrorNote} error=${error} />
    ${result && html`<section class="answer">
      ${result.enough_evidence ? result.answer.map((s, n) => html`<${Sentence} key=${n} s=${s} />`) : html`<p>${result.text}</p>`}
      ${result.sources.length > 0 && html`<h2>Sources</h2><ul class="plain">${result.sources.map((src) => html`
        <li key=${src.id}>${src.kind === "doc" ? html`<b>${src.title}</b> <span class="muted small">${src.excerpt}</span>` : html`<a href=${`#/events/${src.id}`}>event ${src.id}</a> <span class="muted small">${src.type} · ${src.subject_key || ""}</span>`}</li>`)}</ul>`}
      <p class="muted small">retrieval: ${result.mode}</p>
    </section>`}`;
}
