# Daelys documentation

## Project management

| Read this | To learn |
| --- | --- |
| [Project charter](project-management/PROJECT_CHARTER.md) | Purpose, objectives, scope, constraints, roles (RACI), governance |
| [PRD](project-management/PRD.md) | Users, goals, 37 functional and 8 non-functional requirements with status, success metrics |
| [Roadmap](project-management/ROADMAP.md) | Now, next and later |
| [Milestones](project-management/MILESTONES.md) | M1–M7 delivered with exit criteria and results; M8–M10 upcoming |
| [Product backlog](project-management/PRODUCT_BACKLOG.md) | Epics, user stories, definition of done, open backlog, technical debt |
| [Risk register](project-management/RISK_REGISTER.md) | Risks, mitigations, issues, heat map |
| [QA report](project-management/QA_REPORT.md) | Test strategy, requirements coverage, results, defect log, what isn't verified |
| [Closure report](project-management/CLOSURE_REPORT.md) | Outcomes, scope changes, cost, lessons learned, handover, sign-off |

## Product and engineering

| Read this | To learn |
| --- | --- |
| [Case study](CASE_STUDY.md) | One page: the problem, who has it, what was cut and why, how quality is measured |
| [Product overview](PRODUCT_OVERVIEW.md) | What Daelys does, for whom, every feature and screen, approval tiers, and what it deliberately doesn't do |
| [Architecture](ARCHITECTURE.md) | System context, the event spine, risk engine, proposals and executor, meetings, retrieval, data and security model, with diagrams |
| [Evaluation](EVALUATION.md) | DaelysBench: seven suites, their metrics and datasets, and the rules for honest reporting |
| [Technical decisions](TECHNICAL_DECISIONS.md) | Every decision with its reason, by ID (S1-01 … D-01) |
| [API reference](API_REFERENCE.md) | Every route with its access level, rate limit and purpose |
| [Configuration](CONFIGURATION.md) | Every environment variable and per-workspace policy |
| [Deployment](DEPLOYMENT.md) | Where deployment stands and the steps to Render and Neon |
| [Onboarding a team](ONBOARDING.md) | GitHub App permissions, first backfill, speaker mapping, approval tiers, isolation checks |
| [Runbook](RUNBOOK.md) | Health checks, admin tasks, troubleshooting, data rules |
| [Design system](DESIGN_SYSTEM.md) | Tokens, type, the image, components, accessibility |
| [Changelog](CHANGELOG.md) | What changed |
| [Project context](PROJECT_CONTEXT.md) | Stack, file layout and data flows in brief |

## Reading paths

| You are | Read |
| --- | --- |
| Reviewing project management | [Case study](CASE_STUDY.md) → [charter](project-management/PROJECT_CHARTER.md) → [PRD](project-management/PRD.md) → [milestones](project-management/MILESTONES.md) → [closure report](project-management/CLOSURE_REPORT.md) |
| Reviewing the product | [Case study](CASE_STUDY.md) → [product overview](PRODUCT_OVERVIEW.md) |
| Reviewing the engineering | [Architecture](ARCHITECTURE.md) → [technical decisions](TECHNICAL_DECISIONS.md) → [QA report](project-management/QA_REPORT.md) |
| Checking quality claims | [Evaluation](EVALUATION.md) → [QA report](project-management/QA_REPORT.md) |
| Deploying or operating | [Deployment](DEPLOYMENT.md) → [configuration](CONFIGURATION.md) → [runbook](RUNBOOK.md) |

## How it was built

- [Sprint specs](process/sprints/): the written spec for each sprint, with acceptance criteria.
- [Verification gates](process/verification/): break-it checks run before each sign-off.
- [Sprint reports](sprints/): what was built, real test and bench output, gaps and decisions.
- [Build playbook](process/BUILD_PLAYBOOK.md) and the [project rules for Claude Code](../CLAUDE.md).

Component notes live next to the code: [`evaluation/README.md`](../evaluation/README.md) (DaelysBench plan suite)
and the versioned prompts in [`agents/prompts/`](../agents/prompts/).
