// Same-origin calls to the FastAPI backend. The session token lives in an httpOnly cookie the page cannot
// read; every write sends the CSRF header the API requires for cookie-authenticated requests.
export class ApiError extends Error {
  constructor(status, message, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const MESSAGES = {
  not_configured: "This needs server configuration first: ",
};

async function request(method, path, { json, body, contentType } = {}) {
  const headers = {};
  if (method !== "GET") headers["X-Daelys-CSRF"] = "1";
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (contentType) {
    headers["Content-Type"] = contentType;
  }
  const resp = await fetch(path, { method, headers, body, credentials: "same-origin" });
  // A 401 mid-session sends the user to sign in; the session check itself and the sign-in call just report it.
  if (resp.status === 401 && path !== "/auth/session" && path !== "/auth/me") {
    window.dispatchEvent(new Event("daelys:signed-out"));
    throw new ApiError(401, "Please sign in.");
  }
  if (resp.status === 204) return null;
  const data = await resp.json().catch(() => null);
  if (!resp.ok) {
    const code = data && data.error;
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
    const prefix = MESSAGES[code] || "";
    throw new ApiError(resp.status, prefix + (detail || `Request failed (${resp.status})`), code);
  }
  return data;
}

export const api = {
  login: (tenant, email, password) => request("POST", "/auth/session", { json: { tenant, email, password } }),
  logout: () => request("DELETE", "/auth/session"),
  me: () => request("GET", "/auth/me"),
  projects: () => request("GET", "/projects"),
  project: (id) => request("GET", `/projects/${id}`),
  createProject: (name) => request("POST", "/projects", { json: { name } }),
  workItems: (projectId) => request("GET", `/work-items?project_id=${projectId}&limit=200`),
  briefs: (projectId) => request("GET", `/projects/${projectId}/briefs`),
  brief: (projectId, id) => request("GET", `/projects/${projectId}/briefs/${id}`),
  pasteBrief: (projectId, title, content) =>
    request("POST", `/projects/${projectId}/briefs`, { json: { title, content } }),
  uploadBrief: (projectId, file) =>
    request("POST", `/projects/${projectId}/briefs/upload?title=${encodeURIComponent(file.name)}`, {
      body: file,
      contentType: briefContentType(file),
    }),
  generatePlan: (projectId, artifactId) =>
    request("POST", `/projects/${projectId}/plan`, { json: { artifact_id: artifactId } }),
  drafts: (projectId) => request("GET", `/projects/${projectId}/plan/drafts`),
  draft: (projectId, id) => request("GET", `/projects/${projectId}/plan/drafts/${id}`),
  acceptPlan: (projectId, draftId) =>
    request("POST", `/projects/${projectId}/plan/accept`, { json: { draft_id: draftId } }),
  health: () => request("GET", "/risk/health"),
  itemSignals: (itemId) => request("GET", `/work-items/${itemId}/signals`),
  explain: (signalId) => request("POST", `/signals/${signalId}/explain`),
  graph: (projectId) => request("GET", `/projects/${projectId}/graph`),
  event: (id) => request("GET", `/events/${id}`),
  generateProposals: (itemId) => request("POST", `/work-items/${itemId}/proposals/generate`),
  proposals: (status) => request("GET", `/proposals${status ? `?status=${status}` : ""}`),
  approve: (id, args) => request("POST", `/proposals/${id}/approve`, { json: args ? { args } : {} }),
  reject: (id, reason) => request("POST", `/proposals/${id}/reject`, { json: { reason } }),
  undo: (id) => request("POST", `/proposals/${id}/undo`),
  audit: (params) => request("GET", `/audit?${new URLSearchParams(params)}`),
  meetings: () => request("GET", "/meetings"),
  meeting: (id) => request("GET", `/meetings/${id}`),
  uploadTranscript: (body) => request("POST", "/meetings/transcript", { json: body }),
  uploadAudio: (file, params) =>
    request("POST", `/meetings/audio?${new URLSearchParams(params)}`, { body: file, contentType: file.type || "audio/mpeg" }),
  extractMeeting: (id) => request("POST", `/meetings/${id}/extract`),
  mapSpeaker: (label, userId) => request("PUT", "/speakers", { json: { label, user_id: userId } }),
  ask: (question) => request("POST", "/ask", { json: { question } }),
  summaries: (projectId) => request("GET", `/summaries?project_id=${projectId}`),
  createSummary: (projectId, lane, weekStart) =>
    request("POST", "/summaries", { json: { project_id: projectId, lane, week_start: weekStart } }),
  duplicates: () => request("GET", "/duplicates"),
  decideDuplicate: (id, decision) => request("POST", `/duplicates/${id}/${decision}`),
  comments: (itemId) => request("GET", `/work-items/${itemId}/comments`),
  addComment: (itemId, body) => request("POST", `/work-items/${itemId}/comments`, { json: { body } }),
  members: () => request("GET", "/members"),
  resetScenario: () => request("POST", "/admin/reset-scenario"),
  replay: (scenario, timelines, speed) =>
    request("POST", "/time-machine/replays", { json: { scenario, timelines, speed } }),
};

function briefContentType(file) {
  if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) return "application/pdf";
  if (file.name.toLowerCase().endsWith(".txt")) return "text/plain";
  return "text/markdown";
}
