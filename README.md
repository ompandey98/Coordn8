<div align="center">

# ⚙️ Coordn8

### Point it at any codebase. Get back a narrated, frame-synced **video** — and an interactive **page** when you ask for it — audience-tailored, every claim traced to the code.

[![CI](https://img.shields.io/badge/CI-passing-2ea44f)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20·%20Cursor%20·%20Codex%20·%20Kiro-8957e5)](#install-into-your-tool)
[![Tiers](https://img.shields.io/badge/degrades-honestly-orange)](#capability-tiers--platform-agnostic)

*Install once. Run in any repo, in any AI coding tool.*

</div>

---

**Coordn8 turns any codebase into a short narrated video — and an interactive page when you want one — that explain how it actually works.** Install it once and run it in any AI coding tool, pointed at any repo.

It reads the whole repository, finds the *real* decision logic, and hands you something you can put in front of a human:

- a short **narrated video** — where every visual lands exactly on the word it belongs to, with toggle-able captions carrying the exact spoken words;
- an **interactive page** — one self-contained HTML file, true decision-**tree** diagrams (not slideware), every branch labeled in plain words.

**By default it builds the video only.** Ask for "the page too" or "both" to also get the interactive page, or "page only" for just the HTML. Every claim in whatever it builds carries a `file:line` anchor back to the ground-truth code — nothing invented. You approve a plan first; the deliverables come out auto-named after the project.

---

## Quickstart

**One command. No clone, no setup, no npm account.** It downloads Coordn8 straight from GitHub and installs it into every tool you have:

```bash
npx -y github:ompandey98/coordn8 init all
```

The `-y` answers npx's own **"Ok to proceed?"** download confirmation automatically, so the command runs start-to-finish without stopping to ask you anything. (Leave it off and npx will ask you to confirm the download the first time — both work; `-y` is just hands-free.)

That's the whole install. It drops the right adapter into each tool's config **on your machine** (`~/.claude`, `./.cursor`, `./AGENTS.md`, …) — nothing is hardcoded to any author's path — and it's safe to re-run at any time: every run installs the current brain and cleans up after itself. Requires Node.js (ships with npm), which you almost certainly have if you use these tools.

The install is **fully non-interactive** — it never stops to ask a question, so it works unattended in CI, cloud shells, and piped one-liners. Package-manager and pip prompts are auto-answered; a headless run just installs everything.

> **⚠️ After installing, open a NEW Claude Code session.** Claude Code reads its skill/command list once, at session start. If you install (or re-install) while a session is already open, `/coordn8` won't appear **in that window** until you start a fresh session (quit and reopen, or open a new one). This is the #1 reason `/coordn8` "isn't showing up" — the files are already installed; the open window just hasn't re-read them yet. (And it's `/coordn8`, not `/coordinate`.)

Want just one tool? Swap `all` for `claude`, `cursor`, `codex`, `kiro`, or `generic`. To refresh or remove it later:

```bash
npx -y github:ompandey98/coordn8 init claude    # install one tool
npx -y github:ompandey98/coordn8 update         # pull the current version (clears the npx cache) + reinstall
npx -y github:ompandey98/coordn8 uninstall      # remove it again
```

> **If a run seems to be serving something stale, use `update`.** `npx github:…` caches the cloned repo, so a plain re-run can keep serving a cached copy. Both `update` and `init` clear that cache first, so the next fetch pulls straight from GitHub and overwrites what's installed.

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
| **Anything else** | `npx -y github:ompandey98/coordn8 print` → paste the output as your first message |

Coordn8 reads everything, detects your environment, then comes back with numbered questions + a build plan — including which deliverable(s) it will produce (**default: the video only**; say "page too", "both", or "page only" to change it). Approve it, and it builds + verifies the in-scope deliverable(s) into a `Coordn8/` folder in the current path — auto-named after the project (`<project>.mp4`, and `<project>.html` when the page is in scope). You name nothing.

---

## What it produces

Whatever it builds lands in a `Coordn8/` folder in the path you run it, **auto-named after the project** — for a repo called `awesome-api` the default run gives you `Coordn8/awesome-api.mp4` (add the page and you also get `Coordn8/awesome-api.html`).

**By default: the video only.** Coordn8 builds the video and nothing else unless you ask for more — say "page too" or "both" for the interactive page as well, or "page only" for just the HTML. It never produces an unrequested deliverable, and it never skips a requested one silently. Both deliverables are described below; you get the one(s) your request scopes to.

**1 — The video** (`<project>.mp4`) — *the default deliverable*
≤4–5 min, narrated, 1080p, presentation-grade. A documentary-style teaching arc: hook → what it is → why it wins → the machinery → one worked example → payoff. Every visual is timed to enter when its sentence is spoken — checked sentence-by-sentence against the *final* rendered file (a per-sentence frame is pulled and inspected), so no visual runs ahead of its narration. Each scene is rendered by the **best free engine for its content** — Manim (3Blue1Brown's engine) for math and precise geometry; HTML/CSS/Canvas with GSAP, D3, and Rough.js for software, flows, and graphs — all merged into one uniform timeline. Ships with **toggle-able captions**: a soft subtitle track embedded in the mp4 (off by default; switch on in VLC/QuickTime/any player) plus a sidecar `.srt`, both carrying the *exact* narrated words.

**2 — The interactive page** (`<project>.html`) — *built when you ask for it*
One self-contained HTML file, zero external dependencies, opens by double-click offline. **A compact, read-through reference — not the video re-skinned.** It's a random-access reader, organized by an information architecture derived from *this* project (its own tabs, splits, and depth), closer to an interactive Distill/3Blue1Brown article than a slide deck: animated architecture map, true decision-**tree** flows (every fork labeled in plain words), per-unit deep dives with a "walk an example" stepper, exhaustive outcomes tables, and a source-of-truth color legend (deterministic code vs. model judgment vs. data field vs. vision). Every node carries its `file:line`. If a video was also produced, it's available as an *optional* embedded player tab (with speed controls) — a guest feature, never the page's spine.

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
| **Toggle-able captions** | The video carries a soft, embedded subtitle track (off by default; enable in VLC/QuickTime/any player), plus a sidecar `.srt` and a WebVTT track for the page player. Captions are the *exact* narrated words — re-serialized from the same beatmap the voice uses, not re-transcribed — and a gate proves they never drift from the narration. A long sentence is split into **readable cues** (≤2 lines, ≤42 chars/line, capped on-screen time) rather than dumped as one screen-filling paragraph — the words stay verbatim, just broken into glance-able chunks. |
| **Remembers your decisions** | Your course-corrections ("slow the intro", "cut that section") are written to a persistent decision log and honored across long sessions and full rebuilds — never silently reverted. |
| **Fast without melting your laptop** | The render pipeline is **adaptive**: it sizes parallelism from real CPU + free RAM and then keeps re-tuning it live — ramping *up* to use idle cores when the machine has headroom, and shrinking fast the instant load or memory tightens (the same additive-increase / multiplicative-decrease discipline TCP uses). So a machine that frees up mid-run gets used, instead of being stuck behind one cautious worker count. It also writes every frame to disk as it goes — a crash loses one frame, not the batch. |
| **Spends effort where it counts** | Judgment-heavy work (understanding code, fact-checking, the anti-slop and clarity critics) gets full reasoning; deterministic plumbing (subtitle serialization, muxing, frame capture, byte-diffing adapters) runs on a fast, cheap, high-concurrency path. Faster and lighter on tokens, with **zero** shortcuts on anything that decides correctness — every fidelity gate runs at full strength regardless. |
| **Honest about limits** | On a machine without video tooling it says *exactly* what it couldn't render and the one-line command to unlock it, and offers the page as a fallback rather than silently producing an unrequested deliverable. It never fakes a deliverable and never swaps in one you didn't ask for. |
| **Verifies before it hands over** | Deterministic gate scripts run against each build — tree-shape, fact-tracing, content bans, DOM render, visual density, layout/composition, narration sync, caption fidelity, media integrity — plus a **semantic layer** that checks *meaning* rather than geometry: whether a frame shows what the voice is saying, whether an arrow points the way the sentence says, whether a panel shows the right example, whether a section quietly lost its visual, and whether any beat leaves half the frame empty. A final verification manifest then refuses to authorize any "Verified: …" word that isn't backed by a genuine pass. The scripts are advisory leads the agent re-checks — a gate whose input is absent skips honestly rather than faking a pass. |
| **Says what a check actually proves** | A gate passing means "not provably broken in one mechanical way" — it never means the deliverable is good, and Coordn8 says so in the same breath instead of presenting a green board as a quality claim. Anything about meaning or craft ("does this explain well", "does this look good") can only be called done after a **frame-level review**, and every status ends with what was *not* verified. |
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
npx -y github:ompandey98/coordn8 setup     # install EVERYTHING missing — base tools + Manim + JS libs (sudo-free where possible)
npx -y github:ompandey98/coordn8 detect    # see the resulting tier
```

The tier is the **capability ceiling** — what the machine can physically render. It's intersected with your requested scope (video by default), and it can only ever *subtract* from that scope, never add to it: the columns below show what each tier is *able* to produce, and Coordn8 builds whichever of those you actually asked for.

| Tier | Environment (after self-setup) | Can render page | Can render video | Gates |
|---|---|---|---|---|
| **A** | shell + files + `ffmpeg` + headless Chrome + TTS | ✅ | ✅ | all |
| **B** | shell + files, but a video tool genuinely couldn't be installed (offline, no package manager) | ✅ | ⏭️ can't here — *specific* reason + one-line unlock | data/content/structure |
| **C** | file write only (artifact env, no shell to install) | ✅ | ⏭️ | in-model |
| **D** | chat only | code-block | ⏭️ | in-model |

If your scope needs something the tier can't render — e.g. you asked for the video but the machine couldn't install the video tools — Coordn8 says so plainly with the exact unlock command, and **offers** the page as a fallback rather than silently producing a deliverable you didn't ask for. It **never** fakes a deliverable, **never** swaps in an unrequested one, and **never** writes "not supported here" into the page or video — the deliverables are for your audience, not a report on the build machine. A skip is a one-line status in the handover with the command to unlock it.

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
| `content_coverage` | both, the completeness counterpart to `visual_density`: reads the requirement-coverage ledger (`_research/requirements.md`) and flags any in-scope must/should ask that isn't `covered`, or any coverage claim with no `file:line`/section anchor — so substance that was silently dropped (e.g. because it was hard to animate) is caught, not just sparse visuals |
| `layout_probe` | video: geometry from the rendered DOM (`getBoundingClientRect`) — nothing overflows the frame or 5% safe area, no text-box collisions, type above the size floor. With `--shot` (wired automatically when captured frames exist, and where Pillow is installed) it also probes **real captured pixels**, not a self-described layout |
| `beat_sync` | video: no visual enters ahead of its sentence; duration within tolerance; per-sentence frames extracted for inspection |
| `subtitle_check` | video: the caption track (`.srt`/`.vtt`) carries the **exact** narrated words in order — proves captions never drifted from the voice — cue timings are monotonic, non-negative, and within the video's duration, **and** every cue is within the readable limits (≤2 lines, ≤42 chars/line, ≤7 s on screen, ≤20 chars/sec) so a caption can never come back as a screen-filling paragraph |
| `media_probe` | video: resolution, fps, audio present, clean decode |
| `check_semantics` | video, **S1–S8**: reads the instrumentation trace and catches what geometry can't — a non-finite alpha (an element that renders **nothing**, silently), a raw boundary index past its array, text escaping the container it's drawn *inside*, text sitting on a connector, an asset whose `subjectId` differs from the beat's (right shape, **wrong content**), a drawn direction or arrowhead contradicting the declared `from`→`to`, a **declared section that renders nothing**, and a section losing ink against the last approved cut. S1/S5/S6 are treated as blocking |
| `check_indices` | video, **I1/I2**: every beat reference resolves for that scene's real line count — no stale helper index, no raw boundary index past the array, no `lineCount` guard that silently falls through to hardcoded timings. Also reports how much of each scene is **name-bound** (the safe form) |
| `check_density` | video, **frame use**: ink coverage per horizontal band per spoken line, so a beat that leaves ~540px of the frame empty under live narration is a finding, not an opinion. Runs on the trace (no renderer needed) or on real frames, and always prints a **weakest-beat ranking** — the lowest-ink beat is usually what a reviewer names next |
| `check_content` | both: every entry in `approved_strings.json` appears **byte-exact** (approved wording is frozen; a theme phrase is never a licence to rewrite it), zero repeated 5-grams across narration lines, no sentence reciting a list already carried by the frame, and an identifier/PII sweep across **frames and sidecar files** (captions, intermediate JSON, logs shipped alongside) |
| `stage_v` | video, **the blind reverse check**: every spoken line's frame reviewed against its exact sentence by a reviewer with **no knowledge of the builder's claims**, on an artifact proved **newer than every source edit**, with the **previous cut's frame** supplied. A `contradicts` verdict blocks; an unanswered field counts as *not verified*; no render at all makes this an **open item, never a pass** |
| `verify_manifest` | both, LAST: reads `_build/verification.json` and refuses (1) any `pass` with no evidence, (2) any end-user "Verified: …" word not backed by a genuine pass, (3) any required gate silently missing/failed/untested, (4) any `pass` whose evidence strength is `untested`, (5) **any semantic claim** ("explainability", "shareability", "anti-slop", "looks good") that has no **frame-level** verification behind it — emitting the only claim string that's safe to show |

Run them all against a build folder: `npx -y github:ompandey98/coordn8 gates path/to/Coordn8`

---

## The semantic layer — checking MEANING, not just geometry

> **Mechanical checks measure GEOMETRY and PRESENCE. Everything a human complains about is MEANING.**

That distinction is the most important idea in this package. Geometry can tell you two boxes don't overlap, a panel isn't too small, an index is in range. It cannot tell you whether **this frame shows what the voice is saying** — whether a section lost the visual it should have, whether an arrow points the right way, whether this is the right evidence for the thing being narrated. A frame can be geometrically perfect and semantically wrong, so a suite of geometry gates can be entirely green on a video a reviewer rejects on sight.

Coordn8 closes that gap on three levels. The order matters: the first level makes defects *impossible* rather than merely *detectable*.

**1. The scene runtime (`scripts/render/scene_runtime.js`) — whole defect classes removed at the source.** Video scenes are authored against it, and it refuses to let the classic failures happen:

| The runtime guarantees | Which makes this impossible |
|---|---|
| **Named beats that THROW** — `beat('the-worked-example')` resolves against the script; an out-of-range index raises (never **clamps**) | A beat reference going stale when the narration line count changes. Editing narration is the most common operation in the workflow, and a clamped index mistimes scenes *silently* — one root cause that surfaces as many unrelated-looking symptoms. Now it's one loud resolve error naming the valid beats |
| **Non-finite alpha is fatal at the draw call** | A `NaN` opacity drawing **nothing** — no error, no warning, no log — while the narration names the missing thing |
| **Bounded text by default** inside containers (`unbounded: true` is an explicit opt-in) | A caption overflowing its own card. A box-overlap check compares two *separate* boxes, so a child escaping its *own parent* is invisible to it |
| **`from`/`to` required** on every connector | An arrow animating opposite to its own arrowhead, contradicting the sentence describing the flow. **Geometry has no concept of direction** |
| **`subjectId` required** on every asset and beat | A panel showing the evidence for a *different* example — real, legible, well-cropped, and about the wrong thing |
| **`section('name', …)`** declarations + an **instrumentation trace** (per frame: boxes, connector polylines, asset draws) | Everything below. Without instrumentation the semantic gates cannot be written at all |

**2. Semantic gates that read the trace — no browser required.** `check_semantics.py` runs S1–S8: non-finite alpha · raw boundary index · child-escape · text-on-connector · asset↔subject mismatch · flow direction · declared-but-empty section · ink regression against the last approved cut. `check_indices.py` catches stale beat references and `lineCount` fallback guards, and reports how much of a scene is name-bound. `check_density.py` turns *"this looks low-effort"* into a **number** — ink per horizontal band per spoken line — so half a frame at 0.0% ink under live narration is a finding instead of an opinion; it also ranks the weakest beats, which is usually what a reviewer names next. `check_content.py` holds `approved_strings.json` **byte-exact** (an approved wording is frozen until you change it — a theme phrase is never a licence to rewrite it), catches repeated 5-grams, catches the voice reciting a list already on the frame, and sweeps identifiers in **sidecar files too**, not just frames.

**3. Stage V — the blind visual reverse check (`stage_v.py`), a mandatory stage.** For every spoken line, the frame at 85–90% through that line goes to a reviewer together with **the exact sentence and no knowledge of the builder's claims**. A `contradicts` verdict **blocks the build**; everything else is a ranked defect list. Three rules make it work: verifiers are **blind** (a reviewer handed a checklist finds only checklist items); the artifact must be proved **newer than every source edit** before it may be reviewed (reviewing a stale render surfaces already-fixed "defects" and discredits the real ones); and the reviewer gets the **previous cut's matching frame** and is asked *"what used to be here?"* — the only reliable way to find a section that silently lost its visual.

**Claim discipline ties it together.** A status line like *"ten gates pass on all N scenes"* can be completely true and still worthless — and a true-but-worthless quality claim destroys trust faster than any single defect. So: report what a gate *measures*, never what it *implies*; say in the same breath that a gate passing is **not** evidence of quality; and only frame-verified items may be called done for anything semantic — `verify_manifest.py` **refuses** to authorize words like "explainability" or "looks good" without a frame-level verification behind them. Every status ends with what was **not** verified.

The full doctrine — ten defect classes with their root causes, seven pre-build rules, the verification fleet, and a first-go checklist — is **Phase F** of [engine/PLAYBOOK.md](engine/PLAYBOOK.md), so any fresh agent session inherits all of it. A worked reference scene is in [examples/example_scene.js](examples/example_scene.js).

---

## Contributing

Contributions welcome. To extend the engine or add support for a new tool:

```bash
python3 build/generate_adapters.py         # rebuild the tool adapters from source
python3 build/generate_adapters.py --check # sanity check (also runs in CI)
python3 tests/run_tests.py                 # run the full suite
node scripts/render/scene_runtime.js selftest   # prove the scene-runtime guarantees
```

Files under `adapters/` are generated, so edit the source rather than them; adding a tool is a small, single-place change.

| Read this | For |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | the full design |
| [`docs/ADDING_A_TOOL.md`](docs/ADDING_A_TOOL.md) | wiring up a new host tool |
| [`docs/TEACHING_PRINCIPLES.md`](docs/TEACHING_PRINCIPLES.md) | the pedagogy both deliverables are held to |
| [`docs/FIRST_GO_QUALITY_ADDENDUM.md`](docs/FIRST_GO_QUALITY_ADDENDUM.md) | the field report behind the semantic layer — the ten defect classes, why mechanical checks were blind to each, and the ranked fixes. Written from a real production build; it's the reasoning behind Phase F of the playbook |
| [`examples/example_scene.js`](examples/example_scene.js) | a worked video scene authored the safe way, gate-clean, with every pattern annotated |

---

## Author & license

Created by **pandeom**. Released under the [MIT License](LICENSE) — free to use, fork, and build on, with attribution.

<div align="center">
<sub>Coordn8 — because the fastest way to understand a system is to watch someone explain it well.</sub>
</div>
