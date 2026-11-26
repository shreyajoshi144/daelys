# Design system

One stylesheet ([`web/styles.css`](../web/styles.css)), one image ([`web/img/collage.webp`](../web/img/collage.webp))
and one blue. There is no CSS framework and no build step. The reasons are in D-01 of
[Technical decisions](TECHNICAL_DECISIONS.md).

## 1. Principles

1. **One journey.** Landing, sign-in, onboarding and the workspace share the same paper, type and blue, so entering
   the product feels like going further into the same place.
2. **Weight before colour.** "At risk" is solid ink, not red. Severity reads in greyscale and for colour-blind users.
3. **One accent.** Cobalt marks what you can act on: primary buttons, links in context, the principles band, the
   call to action. Nothing else competes with it.
4. **Image as frame, never as content.** The collage frames the public screens and appears as crops in the
   workspace. It never carries information.
5. **Quiet motion.** Short entrance animations, all switched off under `prefers-reduced-motion`.

## 2. Tokens

### Colour (light)

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#f2efe8` | Canvas: the warm stone of the image |
| `--paper` | `#fbfaf6` | Cards, panels, inputs |
| `--stone`, `--stone-2` | `#e9e5dc`, `#dfdacf` | Sidebar, lanes, quiet fills |
| `--ink`, `--ink-2`, `--muted`, `--faint` | `#0f1419` → `#9aa0a9` | Text, from strongest to faintest |
| `--line`, `--line-strong` | ink at 10% and 20% | Hairlines and borders |
| `--blue`, `--blue-deep`, `--blue-soft` | `#1b5bb0`, `#123f7d`, `#d4e4f4` | The accent, its hover, its tint |
| `--review`, `--review-ink` | `#e6defa`, `#46357e` | "For review" (the image's lilac chip) |
| `--approved`, `--approved-ink` | `#d8eac6`, `#2d5418` | "Approved" (leaf green) |
| `--lilac`, `--lilac-ink` | `#e4e2f8`, `#3c3577` | Medium severity, the risk score tile |
| `--risk`, `--on-risk` | ink, paper | At risk: solid ink |
| `--watch` | `#8f86dc` | Watch: dots, card stripes, graph nodes |
| `--ok` | `#3f8f55` | On track |

### Light only

There is no dark mode. Every screen uses the same stone paper as the image, so the workspace feels like the landing
page and sign-in. `color-scheme: only light` keeps form controls and scrollbars light, and stops browsers from
auto-darkening the page.

### Shape, depth, motion

| Token | Value |
| --- | --- |
| `--r-sm`, `--r`, `--r-lg`, `--r-xl` | 7, 11, 18, 28 px |
| `--shadow` | a 1px lip and a soft 30px drop |
| `--shadow-lg` | a 70px drop for floating surfaces |
| `--ease` | `cubic-bezier(.2, .7, .2, 1)` |

## 3. Type

| Token | Stack | Use |
| --- | --- | --- |
| `--display` | SF Pro Display, Segoe UI Variable Display, Inter … | Headings: weight 600, letter-spacing −0.03 to −0.045 em |
| `--sans` | SF Pro Text, Segoe UI, Inter … | Body and UI |
| `--mono` | SF Mono, Menlo, Consolas … | Kickers, labels, scenario time, severity chips |
| `--italic` | Iowan Old Style, Palatino, Georgia | Only the "94" risk tile on the landing page |

All stacks are system fonts, so no font file is downloaded.

## 4. The image

The collage is 1733×907. Its centre panel spans x 24.9–73.2% and y 11–87.1%.

| Where | How |
| --- | --- |
| Landing hero, sign-in, onboarding | `Collage` (`web/js/ui.js`) places our panel exactly over the image's panel. Full-screen views compute the covered image size in CSS (`--wi: max(100vw, 191.07vh)`) to keep the alignment. Under 900px the image is hidden |
| Sidebar cover, project cards, empty states, hero photo card | `Crop` shows a named region: `water`, `clouds`, `blossom`, `leafshadow`, `leaf` |
| Landing footer | The bottom strip of the image |

## 5. Components

| Component | Class or export | Notes |
| --- | --- | --- |
| Buttons | `button`, `.primary`; `.outline` on cobalt | Primary is cobalt; one primary per view |
| Tags | `.tag`, `.for-review`, `.approved` | Pills from the image's chips |
| Risk badge | `.badge.at_risk`, `.badge.watch` | Ink for at risk, lilac for watch |
| Severity and tier | `.sev.critical/high/medium`, `.tier` | Mono uppercase chips; critical and high are ink |
| Health | `.health`, `.dot` | Ring for at risk, filled dot for watch and on track |
| Kicker | `.kicker` | Mono uppercase label above headings |
| Loader | `Loading` (`.dots`) | Three pulsing blue dots with a label |
| Empty state | `Empty` | A clipped image crop beside a title and a line of help |
| Dialog | `confirmDialog()` + `DialogHost` | Replaces `window.confirm`; focus moves in and returns; Escape cancels |
| Toasts | `toast()` + `ToastHost` | Up to four, polite live region |
| Workspace shell | `.sidebar`, `.side-cover`, `.ws`, `.side-link` | Stone sidebar with the workspace card over a crop; the active link is a paper pill with a blue icon |
| Board | `.lane`, `.items li.risk-*`, `.why` | Lanes on stone; at-risk cards get an ink stripe; the why panel is sticky paper |
| Public nav | `.nav-pill` | Floating paper pill with the cobalt primary action |
| Auth and onboarding card | `.paper-card` | Paper on the collage's panel |

## 6. Landing page

1. **Hero:** the collage with Daelys's loop as four stages (Brief, Signals, Review, Action) around the headline,
   and a fan of cards: the brief, a photo crop, the 94 risk tile, a CI signal, review tags and a lead's note.
2. **Ticker:** real event types Daelys reads and writes.
3. **Product:** the real board screenshot with four numbered callouts and a legend.
4. **Principles:** a cobalt band with four cards.
5. **Capabilities:** a ruled list of six, each with its status tag.
6. **Call to action:** a cobalt card with hairlines, then facts (security, integrations, stack) and the footer.

## 7. Accessibility

- Visible `:focus-visible` outline in cobalt on every control.
- Contrast (WCAG ratios, measured): ink on paper 17.7, `--ink-2` 9.7, `--muted` 5.0 on paper and 4.5 on the canvas;
  white on cobalt 6.6; cobalt on paper 6.3; the review, approved and lilac chips 6.9–8.4. The watch colour is 3.0
  on paper, enough for a non-text mark.
- **Known gaps:** `--muted` on the stone sidebar is 4.15 and `--faint` on paper is 2.5, both below AA for small
  text. `--faint` is used for secondary labels such as section headings in the sidebar; it should be darkened.
- Severity never depends on colour alone: at risk is a solid ink badge with words, and the board stripe is backed
  by the badge text.
- Dialogs move focus to the confirm button, close on Escape and return focus afterwards; toasts use
  `role="status"` with `aria-live="polite"`.
- Decorative crops are `aria-hidden`; the product screenshot has descriptive alt text.
- Motion stops under `prefers-reduced-motion`.
- Under 700px secondary nav items hide; under 900px the hero stacks and the image is hidden.
