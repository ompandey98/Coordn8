# Page template and component library

A default skeleton for the single-file interactive page — **a starting point, not a cage**. Any
section may carry its own layout (`custom_html`) or a component that is not in this library yet;
the gates judge the outcome (contrast, focus, honest affordance, offline, no horizontal scroll),
never membership in this list. When a custom layout recurs, add it here as a component.

```
python3 scripts/render/build_page.py <content.json> --design-system <design_system.json | kit.json> \
    --out <slug>.html [--plan-out page_plan.json] [--claims-out claims.json] [--layout-js PATH]
python3 scripts/gates/check_page_a11y.py <slug>.html --shots <dir>
```

`build_page.py` inlines `shell.html`, `page.css`, `page.js`, the component files and the active
kit's `--c8-*` variables (from `scripts/design/kit_export.py`) into one file. It never writes a
CDN or network URL: absolute and protocol-relative URLs in content are removed and reported, and
the finished file is re-scanned — a page that would still load from elsewhere is not written.
A font is embedded (base64 woff2) only when the kit's `theme.vendorable` entry has an OFL licence
and a `file` that exists; otherwise the kit's system font stacks are used.

**Colour rule (every template, every component, every custom layout).** A colour is painted only
as one of: a kit role `var(--c8-<role>)`; that role at an alpha (`color-mix(in srgb, var(--c8-ink)
22%, transparent)` or a kit-derived `rgba` of a role); a `color-mix(in srgb, …)` of two roles
(`var(--c8-ink) 8%, var(--c8-paper)`); or `transparent` / `currentColor` / `inherit`. No literal
colour, and no literal fallback after a role (`var(--c8-ink, #111)`): the kit always defines every
role, so a fallback could only ever show an undeclared colour. Print uses the light roles, which
`build_page.py` pins under `@media print`. `scripts/gates/check_palette.py <design_system.json>
--html <page>` reads the stylesheet, resolves the role variables and the mixes, and flags anything
else as off-palette.

## Files

| File | What it is |
|---|---|
| `shell.html` | page skeleton: skip link, progress bar, header (tabs, theme button), contents, main, watch panel, footer, claims JSON, script |
| `page.css` | layout and every component's look; reads only `--c8-*` kit variables (light and dark), phone (≤ 420 px) reflow, print and reduced-motion rules |
| `page.js` | progressive enhancement: theme toggle, scroll tracking, tooltips, stepper, zoom, tabs, video speed, print unfolding, `window.C8.register` |
| `components/*.html` | one file per component; parts are separated by `<!-- c8:part NAME -->`; `{{x}}` is escaped, `{{{x}}}` is built markup |

## Content schema (`content.json`)

| Key | Type | Meaning |
|---|---|---|
| `title` | string, required | page title (h1) |
| `conclusion` | rich inline | the answer, shown first under the title (conclusion first) |
| `lede` | rich block | optional framing line(s) under the conclusion |
| `kicker` | string | small header line (defaults to the title) |
| `lang` | string | `<html lang>`, default `en` |
| `snapshot`, `build_date` | string | stamped in the footer only |
| `source_link_base` | relative path | when set, every `path:line` chip links to `<base><path>#L<line>` |
| `glossary` | list | `{term, definition, aliases?, scope?: "page"|"section"}` |
| `glossary_scope` | `page` (default) or `section` | where "first use" is counted |
| `sections` | list, required | see below, rendered in order (outside-in is the author's job) |
| `claims` | list | `{id?, claim, ref?, status: "read"|"inferred"}`; rendered as the last section |
| `claims_title`, `claims_summary`, `claims_caption` | string | labels for that section |
| `video` | object | `{src, captions?, title?, speeds?}` — relative sibling files only |
| `toc_label` | string | contents heading, default "On this page" |

**Section:** `id`, `title` (required), `summary` (one line, required by convention — a warning if
missing), `what`, `why`, `how` (rich blocks; `how` is folded in a native `<details>`), `how_label`,
`components` (list), `custom_html` (string), `custom_kind` / `custom_explains` (for the page plan),
`primary_visual`, `interaction` (override the page-plan entry), `order` (any arrangement of
`what`, `why`, `components`, `custom`, `how`; default in that order).

**Rich text.** Inline: `` `code` ``, `**strong**`, `[[path:line]]` (a code chip),
`[label](#anchor or relative/path)`. Block: paragraphs separated by a blank line, lines starting
`- ` become a list, a list of strings is several blocks, `{"html": "..."}` is sanitised markup.

## Components

| `type` | Data it takes | Renders |
|---|---|---|
| contents (automatic) | section titles | sidebar `<nav>`; scroll tracking via IntersectionObserver plus scroll; a clicked link holds the active state 800 ms; the last section is active at the page bottom; the active link has `aria-current="true"` |
| progress bar (automatic) | — | fixed reading-progress bar (decorative, `aria-hidden`) |
| summary (per section) | `summary` | one line under the heading |
| What / Why / How (per section) | `what`, `why`, `how` | What and Why open; How in `<details>` |
| `flow` (`tree`, `diagram`) | `title`, `caption`, and one of `graph` `{nodes:[{id,label,ref?,source?,kind?}], edges:[{from,to,label}]}` (laid out by `scripts/render/layout.js` when present — or `--layout-js`), `svg` (inline string) or `svg_file` (relative to the content file); `legend` (default on), `legend_labels` | figure with a Full-size toggle, the four source-of-truth legend entries (each with a line style, not colour alone), and — for graphs — a "read this flow as a list" alternative. With a graph and no layout output the indented list is the figure. SVG nodes may use `class="c8-node"`, `data-node-id`, `data-kind="terminal"`, `data-source="deterministic|model-judgement|data-field|vision"`, edges `c8-edge`, labels `c8-edge-label` |
| `stepper` (`example`) | `title`, `steps: [{title, body, highlight: [node ids]}]` | one step at a time with Previous / Next buttons, arrow, Home and End keys, a live "Step n of N"; highlights the named flow nodes in the same section |
| `outcomes` (`table`) | `caption`, `columns`, `rows` (lists or objects; a cell may be `{text, ref, href}`), `row_header` (default true), `after` | table with scoped headers; on a phone each cell carries its column label instead of scrolling |
| `claims` | `rows` like top-level `claims`, `caption` | claim → `path:line` chip → read / inferred (text + mark, not colour alone), a tally, and the rows in `#c8-claims` JSON (hook for the claims gate) |
| `callout` (`note`, `warning`) | `kind`, `title`, `body` | aside with a text label and an accessible name |
| `code` | `code`, `ref`, `caption` | a code block with its chip |
| `chip` | `ref`, `href?` | a `path:line` chip: a real link when it has a target (and print shows the target), plain code otherwise — never a link-looking control that does nothing |
| `custom` | `html`, `name` | sanitised markup (no script, no event handlers, no external URL, no `@import`); `<style>`, inline SVG, `details`, checkboxes and radios are allowed |
| `predict` (`predict-next`) | `prompt`, `reveal`, `hint?`, `label_open?`, `label_close?` | C6, optional. A "predict what happens next" pause: the question first, the answer behind a real button (`aria-expanded`, `aria-controls`). Without script the answer is simply shown and the button never appears |
| `assumption_toggle` (`assumption`) | `assumption`, `holds`, `breaks`, `highlight?` (flow node ids), `switch_label?`, `holds_label?`, `breaks_label?` | C6, optional. "Change an assumption and see what breaks": a `role="switch"` button (`aria-checked`, Enter/Space) flips between what happens while the assumption holds and what breaks when it does not, marks the named flow nodes in the same section, and announces the change in a polite live region. Without script both states are shown, labelled |
| `self_check` (`self-check`) | `questions: [{q, options: [..], answer: index or option text, explain}]`, `title?` | C6, optional. Three questions by default (another count is kept, with a warning). Each option is a button (`aria-pressed`); choosing one says right or not and opens the native `<details>` "Answer and why". Every question must carry its explanation or the build refuses. Without script the answers sit in the `<details>` |
| glossary tooltip (automatic) | `glossary` | first use of each term, word-boundary and case-insensitive (the original case is kept; `cache` never matches `cached`), skipping code, pre, headings, links, buttons, summaries, nav, chips, existing tooltips and `data-no-gloss`. Markup: `<button class="c8-term" aria-describedby=ID>term</button><span role="tooltip" id=ID>definition</span>` — accessible name is the term, description the definition; opens on hover, focus and tap; Escape and a tap elsewhere close it; flips above near the bottom edge and is clamped inside the viewport. Without script it still opens on hover and focus (CSS) |
| video tab (`video`) | `src`, `captions`, `title`, `speeds` (within 0.75–2) | Read / Watch tabs (arrow keys), a native player, speed buttons. A `.vtt` found next to the page at build time is embedded as a `data:` URL, because Chromium does not load a relative caption track from a `file://` page |
| theme toggle (automatic) | — | follows the OS setting until pressed; then pins `data-theme` (kept in localStorage) |
| footer (automatic) | `snapshot`, `build_date` | the only place the snapshot hash appears |

**Adding a component.** Put `templates/page/components/<type>.html` in place (slots `{{key}}` are
filled, escaped, from the component's own keys) and, if it needs behaviour, mark its root
`data-c8-widget="<type>"` and register it from page script: `window.C8.register("<type>", el => …)`.
Anything that looks clickable must be a real control with a handler — the gate clicks every
control and fails one that changes nothing.

**The claims ledger always ends the page (C3).** Top-level `claims` and every `claims` component
inside a section are listed together in one final ledger section (the same claim given in both places
is listed once). A page with no claims still ends with the ledger, which says nothing was traced, and
the build warns. `scripts/gates/check_claims.py verify <page>.html claims.json` proves the final
ledger, its tally and the `#c8-claims` JSON equal the manifest; `check_claims.py emit` writes the
`<slug>.claims.md` sidecar every video ships with. The C6 components' CSS and script come from their
own component files and are inlined only on a page that uses them.

## What the build also writes

- `--plan-out`: a page plan in the shape `scripts/gates/visual_density.py` reads, derived from the
  components (each component's `explains` field feeds it).
- `--claims-out`: exactly the rows of the final ledger, as `{"claims": [{id, text, ref, status}]}`.
- stdout: one JSON line — kit, sections, claim counts, every glossary mark (term, matched text,
  section), how each flow was drawn, removed unsafe markup, warnings.
