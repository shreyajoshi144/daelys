import { html } from "./lib.js";

// Stroke icons (24px grid). Colour follows the surrounding text.
const PATHS = {
  home: "M3.5 10.5 12 3.5l8.5 7V20a1 1 0 0 1-1 1H15v-6H9v6H4.5a1 1 0 0 1-1-1z",
  inbox: "M3.5 13.5h4.8l1.6 2.8h4.2l1.6-2.8h4.8M5.6 4.5h12.8l2.1 9V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19v-5.5z",
  mic: "M12 3.5a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0v-5a3 3 0 0 0-3-3zM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3",
  search: "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM20 20l-4.6-4.6",
  shield: "M12 3.2 19.5 6v5.6c0 4.4-3.1 8.1-7.5 9.2-4.4-1.1-7.5-4.8-7.5-9.2V6zM9 12l2.1 2.1L15.2 10",
  menu: "M4 6.5h16M4 12h16M4 17.5h16",
  logout: "M14.5 4.5h4a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-4M10 16l-4-4 4-4M6.2 12H15",
  arrow: "M5 12h14M13 6l6 6-6 6",
  plus: "M12 5v14M5 12h14",
  doc: "M6.5 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5zM13.5 3.5v4h4M8.5 12.5h7M8.5 16h5",
  pulse: "M3.5 12h4l2.2-5.5 4.4 11L16.3 12h4.2",
  check: "M5 12.5l4.2 4.2L19 7",
  quote: "M5 6.5h14v9H10l-4 3.5v-3.5H5z",
  calendar: "M5 5.5h14a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6.5a1 1 0 0 1 1-1zM4 10h16M8.5 3.5v4M15.5 3.5v4",
  clock: "M12 3.8a8.2 8.2 0 1 0 0 16.4 8.2 8.2 0 0 0 0-16.4zM12 7.5V12l3 2",
  lock: "M7 10.5V8a5 5 0 0 1 10 0v2.5M5.5 10.5h13a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1z",
};

export function Icon({ name, small }) {
  return html`<svg class=${small ? "icon sm" : "icon"} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${PATHS[name]} /></svg>`;
}

// The Daelys mark: a "D" on an ink tile, with the keel line in the accent green.
export function Mark() {
  return html`<svg class="mark" viewBox="0 0 24 24" aria-hidden="true">
    <rect class="m-bg" x="0.5" y="0.5" width="23" height="23" rx="6.5" />
    <path class="m-d" d="M7 5.5h4.6a5.25 5.25 0 0 1 0 10.5H7z" />
    <rect class="m-k" x="7" y="17.6" width="10" height="1.9" rx=".95" /></svg>`;
}

export function Wordmark({ href = "#/" }) {
  return html`<a class="wordmark" href=${href}><${Mark} />Daelys</a>`;
}

export function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
}
