// React and htm are plain globals from /vendor (no build step). `html` is JSX-like tagged templates.
export const React = window.React;
export const html = window.htm.bind(React.createElement);
export const { useState, useEffect, useCallback, useMemo, useRef } = React;

export const LANES = ["Product", "Engineering", "Marketing"];

export function navigate(path) {
  window.location.hash = path;
}

export function useRoute() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash.replace(/^#/, "").split("/").filter(Boolean);
}

// Runs an async loader on mount and whenever deps change; exposes data, error and a reload function.
export function useLoad(loader, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const load = useCallback(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    loader()
      .then((data) => live && setState({ data, error: null, loading: false }))
      .catch((error) => live && setState({ data: null, error, loading: false }));
    return () => { live = false; };
  }, deps);
  useEffect(load, [load]);
  return { ...state, reload: load };
}

// Server-sent events: refetch whenever this tenant's work items or risk change (debounced).
// One EventSource per tab, shared by every subscriber: browsers allow only 6 HTTP/1.1 connections per host,
// and a stream per component would starve ordinary requests once a few tabs are open.
const subscribers = new Set();
let source = null;

function subscribe(listener) {
  subscribers.add(listener);
  if (!source) {
    source = new EventSource("/stream");
    source.addEventListener("update", () => subscribers.forEach((fn) => fn()));
  }
  return () => {
    subscribers.delete(listener);
    if (subscribers.size === 0 && source) {
      source.close();
      source = null;
    }
  };
}

export function useLiveUpdates(onUpdate) {
  useEffect(() => {
    let timer = null;
    const unsubscribe = subscribe(() => {
      clearTimeout(timer);
      timer = setTimeout(onUpdate, 300);
    });
    return () => { clearTimeout(timer); unsubscribe(); };
  }, [onUpdate]);
}

export const RISK_LABELS = { at_risk: "At risk", watch: "Watch", ok: "On track" };

export function RiskBadge({ level, score }) {
  if (!level || level === "ok") return null;
  return html`<span class=${`badge ${level}`} title=${`risk ${score}`}>${RISK_LABELS[level]}</span>`;
}

export function HealthCard({ title, health, detail }) {
  const cls = { "At risk": "at_risk", Watch: "watch", "On track": "ok" }[health] || "ok";
  return html`<div class=${`health ${cls}`}><span class="muted">${title}</span><strong>${health}</strong>${detail && html`<span class="muted">${detail}</span>`}</div>`;
}

// Only http(s) links to external evidence; anything else (including javascript:) stays an internal link.
export function safeExternal(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

export function ErrorNote({ error }) {
  if (!error) return null;
  return html`<p class="error" role="alert">${error.message}</p>`;
}
