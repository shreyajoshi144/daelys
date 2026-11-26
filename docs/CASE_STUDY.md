# Daelys: product case study

**One line:** an AI project manager for launch teams that is not allowed to make things up or act on its own.

## The problem

Every morning a launch lead asks the same three questions: *what is slipping, why, and what do we do about it?* The
answers are scattered:
- PR status and failing CI runs are in GitHub
- blockers are said out loud in standups, often in Hinglish
- due dates live in a calendar and in a brief nobody re-reads

So the lead spends the first hour stitching this together by hand, and risk is usually noticed when a date has
already moved.

AI tools promise to do this for them, but they fail in two ways that make them unusable for a launch:

| Failure | Why it matters |
| --- | --- |
| **Confident fiction:** the model reports a risk the data doesn't support, or misses one that it does | The lead has to re-check everything, so the tool saves no time |
| **Unreviewed action:** a bot reassigns work or messages people on its own | One wrong action in launch week costs more trust than the tool ever earns |

## Who has it

| Person | What they need | What Daelys gives them |
| --- | --- | --- |
| **Launch lead or engineering manager** (primary) | Know what's slipping before standup, and why | A board where risk is computed from real work, and every risk shows the events that caused it |
| **Team lead** | Fix problems without chasing people | Proposed fixes (reassign, move a date, split, notify, comment on a PR) to approve in one click, with undo |
| **Engineer** | Not be micromanaged by a bot | Nothing about their work changes unless a person approves it, and every change is in an audit log |

The demo scenario, **Tiffin Box**, is a small team launching meal subscriptions with Razorpay checkout. Over two
replayed weeks the checkout PR fails CI near its due date, and a blocker is raised in standup. Daelys flags the
checkout as at risk, carries the slip down to the tasks that depend on it, and proposes a fix.

## The product bet

Use the model for the work it is good at (reading messy text, explaining, drafting) and **take away its power over
the two things that break trust**:
1. **Risk is computed, not generated.** Five readable rules score every task from the event log. The model may
   explain a score but can never set one.
2. **The agent proposes, a person approves.** The agent can only suggest one of five typed actions. Only a separate
   executor acts, and only on an approved proposal, exactly once, with an audit row and an undo.

Everything else follows from that bet: every generated sentence must cite the events it came from or it is
dropped, and when there is no evidence Daelys says so instead of guessing.

## What I cut, and why

Scope was set in a written blueprint before any code. These were cut on purpose:

| Cut | Why |
| --- | --- |
| Gantt charts | They answer "when is everything scheduled", not "what is slipping and why". Keeping them accurate is the manual work Daelys is meant to remove |
| Sprint velocity | It measures the team, not the launch. It also invites using the tool to judge individual people |
| Custom fields, WIP limits | Configuration surface for a generic tracker. Daelys sits on top of GitHub, so it doesn't need to be one |
| Built-in chat, presence avatars | The team already talks in Slack and standups. Daelys reads those conversations; it doesn't host them |
| Mobile app, billing, signup funnel | Not needed to prove the core loop with a pilot team, so the product is invite-only |
| Google Calendar OAuth | A read-only `.ics` upload gives the due dates without asking a team for calendar access. It's an upload, not a URL, so the server never fetches an address a user typed (no server-side request forgery) |
| Free-text agent actions | Exactly five typed actions, each with a preview, a permission tier and an undo. A free-text action can't be checked before it runs |
| An LLM risk score | A rule can be read, tested and argued with. A model's score can't |

Two things were added only when there was a clear reason. A sixth proposal type, `create_item`, lets a task heard
in a meeting become a work item, but only after a person approves it. Demo reset is the only feature that deletes
events, and it works only in demo workspaces.

## How quality was measured

I built an evaluation harness, **DaelysBench**, before the features it measures. It writes its own results into
the README, so no number is typed by hand. A suite that needs a model key reports `n/a`, not an estimate.

| What | Measure | Result |
| --- | --- | --- |
| **Risk detection** (3 replayed scenarios) | Precision and recall of at-risk flags; how early they come | Precision 1.00, recall 1.00; flagged on the day the labels expect, on average **2.64 days** before the task's due date |
| **Evidence** | Share of flagged tasks whose signals cite an event of the labelled cause (such as the failing CI run) | 0.92 |
| **Safety** (11 adversarial cases) | Prompt injection, unapproved and expired actions, a demoted approver, cross-tenant access, double execution | **11/11 pass**; gates CI. A test proves the suite fails when the guard is removed |
| **Duplicate detection** | Lexical candidate precision and recall | 0.57 / 1.00 (a model then confirms candidates) |
| **Plans, meeting extraction (English vs Hinglish), faithfulness, cost** | Item and dependency precision and recall, action-item F1, cited share, p95 latency, cost per summary | **Not measured yet:** needs a model API key |
| **Engineering** | Tests against real Postgres and Redis | 159 tests (158 pass; 1 needs macOS speech tools), 83% coverage |

**Honest caveats.**
- The labelled datasets are drafts I wrote while building. A perfect score on them shows the system agrees with
  its author, not that it's right. Independent labels come next.
- The risk numbers come from 3 replayed scenarios, not a live team.

## Where it stands

- **Built and tested:** the full loop runs end to end on the demo workspace. That covers brief, plan, signals,
  risk, explanation, proposal, approval, action and audit.
- **Not yet done:** deployment (the production image is verified locally) and live model measurements.

Next steps, in order:
1. Run the model suites with a real key and publish plan quality, Hinglish vs English extraction, faithfulness and
   cost.
2. Get the datasets labelled by someone else.
3. Run a two-week pilot with one real team, measuring how many days earlier they hear about a slip.

## What I'd tell the next PM

- **Write the cut list first.** Most of the hard decisions here were about what not to build.
- **Decide what the model is not allowed to do,** and enforce it in code, not in the prompt.
- **Build the measurement before the feature,** and report "not measured" instead of a number you can't defend.

Deeper reading: [product overview](PRODUCT_OVERVIEW.md), [architecture](ARCHITECTURE.md),
[evaluation](EVALUATION.md), [QA report](project-management/QA_REPORT.md), and the full
[project-management set](project-management/README.md): charter, PRD, roadmap, milestones, backlog, risk register
and closure report.
