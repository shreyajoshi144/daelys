// Shared UI components: loading, image crops, empty states, confirmation dialogs and toasts.
// Dialogs and toasts are app-wide hosts driven by window events, so any page can call them without wiring.
import { html, useEffect, useRef, useState } from "./lib.js";

export function Loading({ label = "Loading", full = false }) {
  return html`<div class=${full ? "loading full" : "loading"} role="status">
    <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>${label}</div>`;
}

// Named regions of the image (fractions of its width and height: x0, x1, y0, y1).
const FRAGMENTS = {
  leaf: [0.205, 0.248, 0.02, 0.28], clouds: [0.165, 0.247, 0.32, 0.51], blossom: [0.76, 0.83, 0.06, 0.24],
  leafshadow: [0.787, 0.893, 0.51, 0.7], water: [0.873, 1, 0.91, 1],
};

// A crop of the image: the only imagery in the product. `cover` keeps tall shapes filled.
export function Crop({ name, cover = false }) {
  const [x0, x1, y0, y1] = FRAGMENTS[name];
  const w = x1 - x0, h = y1 - y0;
  const style = cover
    ? { backgroundSize: "cover", backgroundPosition: `${((x0 + x1) / 2) * 100}% ${((y0 + y1) / 2) * 100}%` }
    : { backgroundSize: `${100 / w}% auto`, backgroundPosition: `${(x0 / (1 - w)) * 100}% ${h < 1 ? (y0 / (1 - h)) * 100 : 50}%` };
  return html`<span class="crop" style=${style} aria-hidden="true"></span>`;
}

export function Empty({ title, children, crop = "blossom" }) {
  return html`<div class="empty-state enter"><${Crop} name=${crop} />
    <div><h3>${title}</h3><p>${children}</p></div></div>`;
}

// ---- Confirmation dialog: `if (await confirmDialog({...})) ...` ----
export function confirmDialog({ title, body, confirmLabel = "Confirm", cancelLabel = "Cancel" }) {
  return new Promise((resolve) => {
    window.dispatchEvent(new CustomEvent("daelys:confirm", { detail: { title, body, confirmLabel, cancelLabel, resolve } }));
  });
}

export function DialogHost() {
  const [dialog, setDialog] = useState(null);
  const confirmRef = useRef(null);
  useEffect(() => {
    const open = (e) => setDialog(e.detail);
    window.addEventListener("daelys:confirm", open);
    return () => window.removeEventListener("daelys:confirm", open);
  }, []);
  useEffect(() => {
    if (!dialog) return undefined;
    const previous = document.activeElement;
    confirmRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") close(false); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); previous?.focus?.(); };
  }, [dialog]);
  function close(answer) {
    dialog.resolve(answer);
    setDialog(null);
  }
  if (!dialog) return null;
  return html`<div class="modal-scrim" onClick=${(e) => e.target === e.currentTarget && close(false)}>
    <div class="modal surface" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
      <h3 id="dialog-title">${dialog.title}</h3><p>${dialog.body}</p>
      <div class="row"><button onClick=${() => close(false)}>${dialog.cancelLabel}</button>
        <button class="primary" ref=${confirmRef} onClick=${() => close(true)}>${dialog.confirmLabel}</button></div>
    </div></div>`;
}

// ---- Toasts: `toast("Saved")`, `toast("Failed", "error")` ----
export function toast(message, kind = "info") {
  window.dispatchEvent(new CustomEvent("daelys:toast", { detail: { message, kind } }));
}

export function ToastHost() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    let id = 0;
    const timers = new Set();
    const add = (e) => {
      const item = { ...e.detail, id: ++id };
      setItems((list) => [...list.slice(-3), item]);
      const timer = setTimeout(() => { setItems((list) => list.filter((t) => t.id !== item.id)); timers.delete(timer); }, 3800);
      timers.add(timer);
    };
    window.addEventListener("daelys:toast", add);
    return () => { window.removeEventListener("daelys:toast", add); timers.forEach(clearTimeout); };
  }, []);
  return html`<div class="toasts" role="status" aria-live="polite">
    ${items.map((t) => html`<div key=${t.id} class=${`toast surface ${t.kind}`}>${t.message}</div>`)}</div>`;
}

// ---- Collage backdrop: the image (web/img/collage.webp). Our content panel is placed exactly
// over the image's own centre panel, so the picture fragments and hairlines around it stay visible. ----
export function Collage({ children }) {
  return html`<div class="collage"><div class="collage-panel">${children}</div></div>`;
}
