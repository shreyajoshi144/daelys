import { api } from "./api.js";
import { html, navigate, useEffect, useRoute, useState } from "./lib.js";
import { Shell } from "./shell.js";
import { LandingPage } from "./pages/landing.js";
import { LoginPage } from "./pages/login.js";
import { PlanReviewPage } from "./pages/plan.js";
import { ProjectPage } from "./pages/project.js";
import { ProjectsPage } from "./pages/projects.js";
import { AskPage } from "./pages/ask.js";
import { AuditPage } from "./pages/audit.js";
import { MeetingPage, MeetingsPage } from "./pages/meetings.js";
import { ProposalsPage } from "./pages/proposals.js";
import { EventPage } from "./pages/why.js";
import { Onboarding, needsOnboarding } from "./pages/onboarding.js";
import { DialogHost, Loading, ToastHost } from "./ui.js";

function workspacePage(route, me) {
  const canEdit = me.role === "owner" || me.role === "lead";
  if (route[0] === "projects" && route[1] && route[2] === "plan" && route[3]) {
    return html`<${PlanReviewPage} projectId=${route[1]} draftId=${route[3]} canEdit=${canEdit} />`;
  }
  if (route[0] === "meetings" && route[1]) return html`<${MeetingPage} meetingId=${route[1]} canEdit=${canEdit} />`;
  if (route[0] === "meetings") return html`<${MeetingsPage} canEdit=${me.role !== "viewer" ? canEdit : undefined} clockNow=${me.clock_now} />`;
  if (route[0] === "ask") return html`<${AskPage} />`;
  if (route[0] === "proposals") return html`<${ProposalsPage} canEdit=${canEdit} />`;
  if (route[0] === "audit" && canEdit) return html`<${AuditPage} />`;
  if (route[0] === "events" && route[1]) return html`<${EventPage} eventId=${route[1]} />`;
  if (route[0] === "projects" && route[1]) return html`<${ProjectPage} projectId=${route[1]} canEdit=${canEdit} clockNow=${me.clock_now} />`;
  return html`<${ProjectsPage} canEdit=${canEdit} isOwner=${me.role === "owner"} isDemo=${me.is_demo} me=${me} />`;
}

let returnTo = null; // in-app path to open after sign-in (memory only; never taken from the URL)

function App() {
  const route = useRoute();
  const [me, setMe] = useState(undefined); // undefined = checking the session, null = signed out
  const [onboardedNow, setOnboardedNow] = useState(null); // user id that just finished the tour this visit

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
    const signedOut = () => setMe(null); // a request got 401: the session expired
    window.addEventListener("daelys:signed-out", signedOut);
    return () => window.removeEventListener("daelys:signed-out", signedOut);
  }, []);

  const isPublic = route.length === 0 || route[0] === "login";
  useEffect(() => {
    // Read the live hash: a navigate() in the same tick (sign-in, sign-out) hasn't reached `route` yet.
    const live = window.location.hash.replace(/^#/, "").split("/").filter(Boolean);
    const livePublic = live.length === 0 || live[0] === "login";
    if (me === null && !livePublic) {
      returnTo = `/${live.join("/")}`; // a deep link survives the trip through sign-in
      navigate("/login");
    }
    if (me && livePublic) {
      navigate(returnTo || "/projects");
      returnTo = null;
    }
  }, [me, isPublic]);

  useEffect(() => {
    window.scrollTo(0, 0); // braces matter: newer browsers return a Promise, which React would treat as a cleanup
  }, [route[0]]);

  if (me === undefined) return html`<${Loading} full label="Opening Daelys" />`;
  if (route[0] === "login" && !me) {
    return html`<${LoginPage} workspace=${route[1]} onSignedIn=${setMe} />`;
  }
  if (route.length === 0 && !me) return html`<${LandingPage} />`;
  if (!me) return null;
  if (onboardedNow !== me.user_id && needsOnboarding(me)) {
    return html`<${Onboarding} me=${me} onDone=${() => setOnboardedNow(me.user_id)} />`;
  }

  const signOut = () => api.logout().finally(() => { setMe(null); navigate("/"); });
  return html`<${Shell} me=${me} route=${route} onSignOut=${signOut}>
    <div class="enter" key=${route.join("/")}>${workspacePage(route, me)}</div><//>`;
}

// Dialogs and toasts live outside the routed tree, so every screen (landing to workspace) shares them.
function Root() {
  return html`<${App} /><${DialogHost} /><${ToastHost} />`;
}

window.ReactDOM.createRoot(document.getElementById("root")).render(html`<${Root} />`);
