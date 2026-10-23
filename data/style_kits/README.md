# data/style_kits/ — style kits (starting points, never fences)

A **style kit** is one coherent visual language for a build. The page and the video of one build
read the **same** active kit, so they look like one product. Each `*.json` here is a kit
(`"schema": 1`). They are a **menu of starting points**: pick one, blend two, bend one with
`extends`, or invent a new one. Nothing ever fails because a kit, a transition, an entrance or a
background treatment is not listed here — kits are judged only by **outcome rules** (below).

## Shipped kits

| id | look | base | for |
|---|---|---|---|
| `field-notebook` | ivory paper, graphite ink, humanist serif, settling motion | light | methods, research, the history of an idea |
| `ops-console` | slate-green dark, mono display, wipe-ins, cut on the beat | dark | infrastructure, pipelines, incidents |
| `drafting-table` | drafting film, condensed caps, lines drawn on, zoom to detail | light | architecture, system design, mechanisms |
| `quiet-ward` | cool white, deep teal, large steady type, slow dissolves | light | health, care, safety-critical procedures |
| `crayon-box` | cream dot paper, rounded type, pop entrances, big objects | light | children, classrooms, first introductions |
| `ledger-ink` | ruled paper, bookish display serif, tabular numerals, count-ups | light | finance, payments, pricing, risk |
| `night-gallery` | plum-black, high-contrast didone, focus-pulls, spotlight | dark | strategy, game theory, ideas |
| `poster-grid` | white and black, one red block, huge grotesque, hard cuts | light | launches, arguments, comparisons |
| `deep-field` | indigo night, geometric sans, drift and zoom across scale | dark | physics, astronomy, scale |
| `chalk-talk` | green slate, chalk display, write-on and erase | dark | maths, proofs, derivations |

## Schema (every field a renderer reads)

| section | what it holds |
|---|---|
| `id`, `name`, `base` | identity; `base` = the skin the video renders in (`light` or `dark`) |
| `mood`, `use_when`, `avoid_when` | prose a human (and the picker's reasons) can read |
| `signals.for` / `signals.against` | words the picker matches against the brief (scoring hints only) |
| `palette_family` | the `data/palettes.json` family this kit pairs best with (a fit bonus, never a requirement) |
| `palette` | the kit's own built-in skin, same shape as a palette family: `roles` (light) and `dark`, with the twelve roles `paper ink muted accent link positive negative warning deterministic model-judgement data-field vision`. When the Palette Director has chosen a palette for the build, that palette **replaces** this one |
| `theme` | `family` font stacks (`display`, `body`, `mono`, optional `math`) — system/offline stacks ending in a generic family; `scale` (1080p px, every size ≥ 28); `page_scale` (CSS px); `weights`; `line_height`; `tracking_em`; `numerals`; `vendorable` (optional OFL faces, never shipped, never a CDN) |
| `stage` | `background` treatment + texture + vignette, `grid` (columns, gutter, margin, baseline), `safe_area` (≥ 5 %), `radius`, `stroke`, `depth` |
| `props` | `card`, `chip`, `node`, `connector`, `callout` — colours are **role references** (`{"role": "ink", "alpha": 0.05}`), never raw hex |
| `charts` | `series` = ONE ink role at stepped opacities; `highlight` (one accent); `gridline`, `axis`, `label`; `increase_decrease` says how up/down is shown **without hue** |
| `motion` | `ease` (CSS `cubic-bezier`), `enter_ms`, `exit_ms`, `stagger_ms`, `lead_ms`, `hold_min_ms`, `entrance` / `exit` described as a move (opacity / translate / scale / blur / clip) |
| `transitions` | ordered vocabulary: each `{id, use, ms, …}` with `use` = `within-section`, `section` or `emphasis`; a move is described by parameters (`blend`, `via_role`, `move`, `scale_from/to`, `mask`, `morph`, `on_beat`), so an invented transition is renderable without new code |
| `continuity` | `prefer` (usually `morph`: the same element moves to its new place) and the `first_second_anchor` — what is on screen at frame 0 |
| `caption_style` | `font_px` ≥ 28, `max_lines` ≤ 2, `overflow: truncate` (never shrink), `scrim`, `marks.good/bad/key` each with a role **and** a non-colour `styles` cue, `max_marks` ≤ 2 |
| `do`, `dont` | rules the scene and page authors follow for this look |

## Outcome rules (the only way a kit can fail)

`scripts/design/pick_kit.py --validate FILE` checks, and nothing else:

- text roles (`ink`, `muted`, `link`) ≥ 4.5:1 on paper, other roles ≥ 3:1, in **both** skins;
  the four legend roles pairwise CIEDE2000 ≥ 10;
- every colour used by stage / props / charts / captions is a declared role;
- every type size ≥ 28 px at 1080p; font stacks offline and ending in a generic family;
- caption ≥ 28 px, ≤ 2 lines, truncates; caption text ≥ 4.5:1 and marks ≥ 3:1 against the scrim
  laid over paper **and** over solid ink; the three marks differ by more than colour;
- `ease` is a valid `cubic-bezier` (x in 0..1) and not linear; enter 150–1200 ms, exit 100–1000 ms,
  stagger 0–400 ms; transitions are a cut (0 ms) or 150–1500 ms, with one marked `section`;
- chart series: one ink, ≥ 2 steps in 0.25..1 falling ≥ 0.12 per step; safe area ≥ 5 %;
  connectors ≥ 2 px.

## Using kits

```bash
python3 scripts/design/pick_kit.py brief.json --build Coordn8/_build            # rank, pick, write
python3 scripts/design/pick_kit.py brief.json --build DIR --blend ledger-ink,night-gallery --take motion,transitions
python3 scripts/design/pick_kit.py brief.json --build DIR --kit my_kit.json --reason "why"   # invented
python3 scripts/design/kit_export.py css     Coordn8/_build/design_system.json  # page:  :root{--c8-*}
python3 scripts/design/kit_export.py runtime Coordn8/_build/design_system.json --js   # video: window.__C8_KIT__
```

A hand-defined kit may start from a shipped one with `"extends": "<id>"` and override any section.
An invented kit is recorded as `invented:<name>` with its reason; a blend as `blend:a+b` with both
parents and the sections each one gave.
