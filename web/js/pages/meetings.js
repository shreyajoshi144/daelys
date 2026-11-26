import { api } from "../api.js";
import { ErrorNote, html, navigate, useLiveUpdates, useLoad, useState } from "../lib.js";
import { Empty, Loading } from "../ui.js";

const LABELS = { "decision.made": "Decision", "action.proposed": "Action item", "blocker.mentioned": "Blocker" };

function UploadForm({ onDone, clockNow }) {
  const [mode, setMode] = useState("transcript");
  const [form, setForm] = useState({ title: "", occurred_at: localInputValue(clockNow ? new Date(clockNow) : new Date()), language: "auto", transcript: "" });
  const [file, setFile] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const occurred = new Date(form.occurred_at).toISOString();
    try {
      const res = mode === "transcript"
        ? await api.uploadTranscript({ title: form.title, occurred_at: occurred, language: form.language, transcript: form.transcript })
        : await api.uploadAudio(file, { title: form.title, occurred_at: occurred, language: form.language });
      navigate(`/meetings/${res.meeting_id}`);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return html`
    <form class="brief-form" onSubmit=${submit}>
      <div class="filters">
        ${["transcript", "audio"].map((m) => html`<button type="button" key=${m} class=${mode === m ? "chip on" : "chip"} onClick=${() => setMode(m)}>${m === "audio" ? "Recording (transcribed by Notiv)" : "Labelled transcript"}</button>`)}
      </div>
      <input value=${form.title} onInput=${set("title")} placeholder="Meeting title" required />
      <div class="row">
        <label class="inline-label">When <input type="datetime-local" value=${form.occurred_at} onInput=${set("occurred_at")} required /></label>
        <label class="inline-label">Language <select value=${form.language} onChange=${set("language")}>
          <option value="auto">auto</option><option value="en">English</option><option value="hi">Hindi / Hinglish</option></select></label>
      </div>
      ${mode === "transcript"
        ? html`<textarea rows="10" value=${form.transcript} onInput=${set("transcript")} placeholder="[00:12] Arjun: Checkout is blocked on the sandbox keys" required></textarea>`
        : html`<input type="file" accept="audio/*" onChange=${(e) => setFile(e.target.files[0])} required />`}
      <button type="submit" disabled=${busy}>${busy ? (mode === "audio" ? "Transcribing… (a few minutes)" : "Saving…") : "Add meeting"}</button>
      <${ErrorNote} error=${error} />
    </form>`;
}

export function MeetingsPage({ canEdit, clockNow }) {
  const { data, error, reload } = useLoad(() => api.meetings(), []);
  useLiveUpdates(reload);
  return html`
    <h1>Meetings</h1>
    <p class="muted">Decisions, action items and blockers are pulled from the transcript, each citing its lines. Action items that match no existing task become proposals; nothing is created on its own.</p>
    <${ErrorNote} error=${error} />
    ${data && data.length === 0 && html`<${Empty} title="No meetings yet" crop="clouds">Paste a standup transcript below. Lines like <code>[00:12] Arjun: Checkout is blocked</code> keep the speaker.<//>`}
    <ul class="plain">${(data || []).map((m) => html`<li key=${m.id}><a href=${`#/meetings/${m.id}`}>${m.title}</a>
      <span class="muted"> · ${new Date(m.occurred_at).toLocaleString()} · ${m.source} · ${m.status}</span></li>`)}</ul>
    ${canEdit !== undefined && html`<section><h2>Add a meeting</h2><${UploadForm} onDone=${reload} clockNow=${clockNow} /></section>`}`;
}

export function MeetingPage({ meetingId, canEdit }) {
  const { data, error, reload } = useLoad(() => api.meeting(meetingId), [meetingId]);
  const members = useLoad(() => api.members(), []);
  const [hover, setHover] = useState([]);
  const [note, setNote] = useState(null);
  useLiveUpdates(reload);
  if (error) return html`<${ErrorNote} error=${error} />`;
  if (!data) return html`<${Loading} label="Opening the transcript" />`;
  const extracted = data.events.filter((e) => LABELS[e.type]);
  async function map(label, userId) {
    try { await api.mapSpeaker(label, userId); reload(); } catch (err) { setNote(err); }
  }
  async function extract() {
    try { await api.extractMeeting(meetingId); setNote({ message: "Extraction queued." }); } catch (err) { setNote(err); }
  }
  return html`
    <p><a href="#/meetings">← Meetings</a></p>
    <h1>${data.title} <span class="muted">(${data.status})</span></h1>
    ${note && html`<p class=${note.status ? "error" : "muted"}>${note.message}</p>`}
    <div class="review">
      <div>
        <h2>Pulled from the transcript</h2>
        ${extracted.length === 0 && html`<p class="muted">Nothing extracted yet.${canEdit ? html` <button class="link" onClick=${extract}>Run extraction</button>` : ""}</p>`}
        <ul class="plain">${extracted.map((e) => html`
          <li key=${e.id} class="clickable" onMouseEnter=${() => setHover(e.payload.lines || [])} onMouseLeave=${() => setHover([])}>
            <b>${LABELS[e.type]}</b> ${e.payload.text}
            <div class="muted small">${e.payload.owner_speaker ? `owner: ${e.payload.owner_speaker} · ` : ""}${e.payload.due_date ? `due ${e.payload.due_date} · ` : ""}${e.payload.matched ? `linked to ${e.subject_key}` : e.type === "action.proposed" ? "no existing task: proposed as a new item" : ""} · lines ${(e.payload.lines || []).join(", ")}</div>
          </li>`)}</ul>
        <h2>Speakers</h2>
        <ul class="plain">${Object.entries(data.speakers).map(([label, userId]) => html`
          <li key=${label}>${label} →
            ${canEdit ? html`<select value=${userId || ""} onChange=${(e) => e.target.value && map(label, e.target.value)}>
              <option value="">unmapped</option>${(members.data || []).map((m) => html`<option key=${m.id} value=${m.id}>${m.display_name}</option>`)}</select>`
              : html`<span>${(members.data || []).find((m) => m.id === userId)?.display_name || "unmapped"}</span>`}
          </li>`)}</ul>
      </div>
      <aside class="brief"><h2>Transcript</h2>
        <ol class="transcript">${data.lines.map((l) => html`<li key=${l.n} class=${hover.includes(l.n) ? "on" : ""}><span class="muted small">L${l.n}</span> <b>${l.speaker || "Unknown"}</b>: ${l.text}</li>`)}</ol>
      </aside>
    </div>`;
}

// <input type="datetime-local"> wants local wall time without an offset; toISOString() would give UTC.
function localInputValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
