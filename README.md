<div align="center">

# ⚙️ Coordn8

### Point it at any codebase. Get back an interactive **page** and a narrated, frame-synced **video** — audience-tailored, every claim traced to the code.

[![CI](https://img.shields.io/badge/CI-passing-2ea44f)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20·%20Cursor%20·%20Codex%20·%20Kiro-8957e5)](#install-into-your-tool)
[![Tiers](https://img.shields.io/badge/degrades-honestly-orange)](#capability-tiers--platform-agnostic)

*Install once. Run in any repo, in any AI coding tool.*

</div>

---

**Coordn8 turns any codebase into an interactive page and a short narrated video that explain how it actually works.** Install it once and run it in any AI coding tool, pointed at any repo.

It reads the whole repository, finds the *real* decision logic, and hands you two things you can put in front of a human:

- an **interactive page** — one self-contained HTML file, true decision-**tree** diagrams (not slideware), every branch labeled in plain words;
- a short **narrated video** — where every visual lands exactly on the word it belongs to, with toggle-able captions carrying the exact spoken words.

Every claim in both carries a `file:line` anchor back to the ground-truth code — nothing invented. You approve a plan first; the deliverables come out auto-named after the project.

---

## Quickstart

**One command. No clone, no setup, no npm account.** It downloads Coordn8 straight from GitHub and installs it into every tool you have:

```bash
npx github:ompandey98/coordn8 init all
```

That's the whole install. It drops the right adapter into each tool's config **on your machine** (`~/.claude`, `./.cursor`, `./AGENTS.md`, …) — nothing is hardcoded to any author's path. Requires Node.js (ships with npm), which you almost certainly have if you use these tools.

Want just one tool? Swap `all` for `claude`, `cursor`, `codex`, `kiro`, or `generic`. To remove it later:

```bash
npx github:ompandey98/coordn8 init claude    # install one tool
npx github:ompandey98/coordn8 uninstall      # remove it again
```

<details>
<summary><b>No Node?</b> Clone-and-install fallback</summary>

```bash
git clone https://github.com/ompandey98/coordn8 && cd coordn8
./install.sh all          # or: claude | cursor | codex | kiro | generic | uninstall
```
</details>

Then **open a fresh session at the root of the repo you want explained** and invoke it:

| Tool | How you invoke it |
|---|---|
| **Claude Code** | type `/coordn8` (or let the `coordn8` skill trigger) |
| **Cursor** | `@coordn8` |
| **Codex / any AGENTS.md CLI** | it's picked up automatically from `AGENTS.md` |
| **Kiro** | include the `coordn8` steering doc |
| **Anything else** | `npx github:ompandey98/coordn8 print` → paste the output as your first message |

Coordn8 reads everything, detects your environment, then comes back with numbered questions + a build plan. Approve it, and it builds + verifies the deliverables into a `Coordn8/` folder in the current path — both files auto-named after the project (`<project>.html`, `<project>.mp4`). You name nothing.

---

## What it produces

Both land in a `Coordn8/` folder in whatever path you run it, **auto-named after the project** — for a repo called `awesome-api` you get `Coordn8/awesome-api.html` and `Coordn8/awesome-api.mp4`.

**1 — The interactive page** (`<project>.html`)
One self-contained HTML file, zero external dependencies, opens by double-click offline. **A compact, read-through reference — not the video re-skinned.** It's a random-access reader, organized by an information architecture derived from *this* project (its own tabs, splits, and depth), closer to an interactive Distill/3Blue1Brown article than a slide deck: animated architecture map, true decision-**tree** flows (every fork labeled in plain words), per-unit deep dives with a "walk an example" stepper, exhaustive outcomes tables, and a source-of-truth color legend (deterministic code vs. model judgment vs. data field vs. vision). Every node carries its `file:line`. If a video was produced, it's available as an *optional* embedded player tab (with speed controls) — a guest feature, never the page's spine.

**2 — The video** (`<project>.mp4`)
≤4–5 min, narrated, 1080p, presentation-grade. A documentary-style teaching arc: hook → what it is → why it wins → the machinery → one worked example → payoff. Every visual is timed to enter when its sentence is spoken — checked sentence-by-sentence against the *final* rendered file (a per-sentence frame is pulled and inspected), so no visual runs ahead of its narration. Each scene is rendered by the **best free engine for its content** — Manim (3Blue1Brown's engine) for math and precise geometry; HTML/CSS/Canvas with GSAP, D3, and Rough.js for software, flows, and graphs — all merged into one uniform timeline. Ships with **toggle-able captions**: a soft subtitle track embedded in the mp4 (off by default; switch on in VLC/QuickTime/any player) plus a sidecar `.srt`, both carrying the *exact* narrated words.

---

## What you get

| | |
|---|---|
| **Works in your tool, unchanged** | Ships as a Claude Code skill, a Cursor rule, a Codex `AGENTS.md`, a Kiro steering doc, or a paste-anywhere prompt — one install command, no config. |
| **Reads the code, not the marketing** | Where a doc and the code disagree, it trusts the code and notes the discrepancy. Every claim is anchored to a `file:line`. |
| **True decision trees** | Branching logic is drawn as an actual tree with every fork and outcome — a linear "chain of boxes" is treated as a build failure, not an acceptable diagram. |
| **Knows its audience** | Identifies who the explanation is for — engineers, product, leadership, or an external reader — and shifts depth, tone, section order, and video pace to fit. |
| **Shareable by anyone, exposes no one** | The person who requested it is the *director*, not the *subject*: your chat sets scope, but nothing about you leaks in. It re-voices "the part you didn't get" into "this covers X," drops irrelevant asides, and never reads raw IDs/URLs aloud — so a hundred people with the same need could each share the exact file, unedited. |
| **Reads like a human made it, and learns** | An anti-slop critic runs throughout the build, not just at the end, keeping the output feeling like a crafted teaching piece rather than AI slop — and every flaw it finds is written to a per-project lessons file that's loaded first on the next rebuild, so mistakes are designed out up front. |
| **Sentence-synced narration** | Visuals are timed to the spoken sentence and checked on the *final* rendered file, not on intermediates — a frame is pulled per sentence and inspected so nothing enters ahead of its narration. |
| **Best free engine per scene** | Math and precise geometry are rendered by **Manim** (the engine 3Blue1Brown uses); software, flows, and graphs use HTML/CSS/Canvas with **GSAP** (eased motion), **D3** (force/tree graphs), and **Rough.js** (hand-drawn warmth) — all free, no API keys, no paid services. Every engine writes the same frame model, so they blend seamlessly on one timeline. Manim and the JS libs are *enhancements*: if one can't be installed, that scene falls back to plain HTML/Canvas and the base video still ships. |
| **Toggle-able captions** | The video carries a soft, embedded subtitle track (off by default; enable in VLC/QuickTime/any player), plus a sidecar `.srt` and a WebVTT track for the page player. Captions are the *exact* narrated words — re-serialized from the same beatmap the voice uses, not re-transcribed — and a gate proves they never drift from the narration. |
| **Remembers your decisions** | Your course-corrections ("slow the intro", "cut that section") are written to a persistent decision log and honored across long sessions and full rebuilds — never silently reverted. |
| **Won't melt your laptop** | The render pipeline sizes its own parallelism from real CPU + free RAM, backs off under load, and writes every frame to disk as it goes — a crash loses one frame, not the batch. |
| **Honest about limits** | On a machine without video tooling it produces the page and says *exactly* what it skipped and how to unlock it. It never fakes a deliverable. |
| **Verifies before it hands over** | Deterministic gate scripts (tree-shape, fact-tracing, content bans, DOM render, visual density, layout/composition, narration sync, **caption fidelity**, media integrity) run against each build, and a final verification manifest refuses to authorize any "Verified: …" claim word that isn't backed by a genuine pass. The scripts are advisory leads the agent re-checks — a gate whose input is absent skips honestly rather than faking a pass. |
| **Tells you what it did, tested, and couldn't do** | Every run ends with a short plain-language handover: what was **added/produced**, what was **verified** (and how — which gates passed on the real files), and what **couldn't be done here** and exactly why (e.g. "Manim wasn't installed, so the math scene used the HTML engine"). No missing capability is ever hidden or written into the deliverables — it's an honest status line for you, not your audience. |
| **Reproducible** | Stamps the exact code snapshot into both deliverables and writes a rebuild guide, so anyone can tell which version was explained and regenerate it. |

---

## Audience-tailored, by design

Coordn8 identifies **who** the explanation is for before it designs anything, and changes depth, tone, section order, and video pace accordingly:

| Audience | Entry point | Depth | Register |
|---|---|---|---|
| Non-technical / leadership | problem → impact | shallow on machinery, deep on why | outcome-first, minimal jargon |
| Product / stakeholder | what it does & why it matters | moderate | clear, concrete |
| Engineer (new to it) | what & why → architecture | full, scaffolded | warm, encouraging |
| Engineer (senior / SME) | straight to the engine | exhaustive | precise, dense |
| External / open source | mental model first | self-contained | welcoming |

It applies real teaching discipline — curiosity gap first, one idea per beat, concrete before abstract, progressive disclosure, explicit bridges, close the loop — the same principles the best short-form teaching channels use.

---

## Capability tiers — it installs what it needs first

Coordn8's first move on any machine is to **unblock itself**: one command auto-installs everything the full pipeline can use — the **base** tools a video needs (`ffmpeg`, a headless Chromium sudo-free via Playwright, and a TTS engine) *and* the **enhancement** layer (Manim for math scenes; the vendored GSAP/D3/Rough/anime JS libs; Pillow for the pixel probe) — *before* it decides what it can produce. All of it is free and needs no API key or account. Dropping to a lower tier is a last resort after a genuine, verified install failure, never a first move. On essentially any machine with a network connection — including locked-down CI and cloud shells with no root — that reaches the full page + video tier. A failed *enhancement* install never blocks the video: that scene type falls back to the HTML/Canvas engine and the base video still ships.

```bash
npx github:ompandey98/coordn8 setup     # install EVERYTHING missing — base tools + Manim + JS libs (sudo-free where possible)
npx github:ompandey98/coordn8 detect    # see the resulting tier
```

| Tier | Environment (after self-setup) | Page | Video | Gates |
|---|---|---|---|---|
| **A** | shell + files + `ffmpeg` + headless Chrome + TTS | ✅ | ✅ | all |
| **B** | shell + files, but a video tool genuinely couldn't be installed (offline, no package manager) | ✅ | ⏭️ skipped, with the *specific* reason + one-line unlock | data/content/structure |
| **C** | file write only (artifact env, no shell to install) | ✅ | ⏭️ | in-model |
| **D** | chat only | code-block | ⏭️ | in-model |

It **never** fakes a deliverable, and it **never** writes "not supported here" into the page or video — the deliverables are for your audience, not a report on the build machine. If a video is skipped, that's a one-line status in the handover with the exact command to unlock it.

---

## The verification gates

Each of these is a bar the deliverable must genuinely clear before it's called done. They run as real scripts (`scripts/gates/`) and are **advisory** — a script finding is a lead the agent re-checks against the code, and a script is never a hard veto (a false-positive gate must not be able to block a correct deliverable). A gate whose input is absent **skips honestly**; it never reports a pass it didn't earn. The `verify_manifest` gate is the backstop: no "Verified" claim reaches you unless it's backed by a real pass.

| Gate | Checks |
|---|---|
| `tree_shape` | flows are real decision trees, not linear chains; forks have ≥2 labeled exits; terminals cover all outcomes |
| `facts_trace` | every claim/number cites a `file:line` that resolves in the snapshot (reference resolvability — the agent still confirms each citation actually *supports* the claim) |
| `content_ban` | no customer/case IDs, emails, person names, jargon handles, capability-excuse text, **requester-personal framing** ("since you already know…"), **read-aloud raw literals** (URLs/IDs a narrator shouldn't spell out), **audience role-play/greetings** ("welcome to the team"), **fabricated acronym expansions**, or **stale build/source meta** (version stamps, TODO/@author, license headers) — swept over the HTML *and* the narration script |
| `dom_smoke` | headless render: all tabs present, marker counts match, zero external dependencies |
| `visual_density` | both: runs on the *plan before rendering* (video storyboard / page plan) — every scene/section has a non-text primary visual, real motion (video) or interaction (page), every visual explains something (no decorative filler), no wall of generic boxes, full-frame use — so low-effort output is caught on paper, not after burning frames |
| `layout_probe` | video: geometry from the rendered DOM (`getBoundingClientRect`) — nothing overflows the frame or 5% safe area, no text-box collisions, type above the size floor. With `--shot` (wired automatically when captured frames exist, and where Pillow is installed) it also probes **real captured pixels**, not a self-described layout |
| `beat_sync` | video: no visual enters ahead of its sentence; duration within tolerance; per-sentence frames extracted for inspection |
| `subtitle_check` | video: the caption track (`.srt`/`.vtt`) carries the **exact** narrated words in order — proves captions never drifted from the voice — and cue timings are monotonic, non-negative, and within the video's duration |
| `media_probe` | video: resolution, fps, audio present, clean decode |
| `verify_manifest` | both, LAST: reads `_build/verification.json` and refuses (1) any `pass` with no evidence, (2) any end-user "Verified: …" word not backed by a genuine pass, (3) any required gate silently missing/failed/untested — emitting the only claim string that's safe to show |

Run them all against a build folder: `npx github:ompandey98/coordn8 gates path/to/Coordn8`

---

## Contributing

Contributions welcome. To extend the engine or add support for a new tool:

```bash
python3 build/generate_adapters.py         # rebuild the tool adapters from source
python3 build/generate_adapters.py --check # sanity check (also runs in CI)
python3 tests/run_tests.py                 # run the full suite
```

Files under `adapters/` are generated, so edit the source rather than them; adding a tool is a small, single-place change. See [`docs/ADDING_A_TOOL.md`](docs/ADDING_A_TOOL.md), and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

---

## Author & license

Created by **pandeom**. Released under the [MIT License](LICENSE) — free to use, fork, and build on, with attribution.

<div align="center">
<sub>Coordn8 — because the fastest way to understand a system is to watch someone explain it well.</sub>
</div>
