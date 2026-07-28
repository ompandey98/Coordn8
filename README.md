<div align="center">

# ⚙️ Coordn8

### Point it at any codebase. Get back an interactive **page** and a narrated, frame-synced **video** — audience-tailored, every claim traced to the code.

[![CI](https://img.shields.io/badge/CI-verbatim--sync%20%2B%20gates-2ea44f)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20·%20Cursor%20·%20Codex%20·%20Kiro-8957e5)](#install-into-your-tool)
[![Tiers](https://img.shields.io/badge/degrades-honestly-orange)](#capability-tiers--platform-agnostic)

*Install once. Run in any repo, in any AI coding tool.*

</div>

---

A large codebase is territory without a map — walls of unfamiliar files, and a README that quietly disagrees with the code inside them. Coordn8 gives your AI coding agent the gear to move across that terrain at speed, scout what's really there, and come back with the coordinates: it reads the whole repository, finds the *real* decision logic, and produces two things you can hand to a human —

- an **interactive page** — one self-contained HTML file, true decision-**tree** diagrams (not slideware), every branch labeled in plain words;
- a short **narrated video** — where every visual lands exactly on the word it belongs to.

Every claim in both carries a `file:line` anchor back to the ground-truth code. You point it at a repo; it comes back with questions, a plan, and — once you approve — the deliverables, auto-named after the project.

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
One self-contained HTML file, zero external dependencies, opens by double-click offline. Animated architecture map, true decision-**tree** flows (every fork labeled in plain words), per-unit deep dives with a "walk an example" stepper, exhaustive outcomes tables, and a source-of-truth color legend (deterministic code vs. model judgment vs. data field vs. vision). Every node carries its `file:line`.

**2 — The video** (`<project>.mp4`)
≤4–5 min, narrated, 1080p, presentation-grade. A documentary-style teaching arc: hook → what it is → why it wins → the machinery → one worked example → payoff. Every visual enters *exactly* when its sentence is spoken, verified frame-by-frame on the final file.

---

## What you get

| | |
|---|---|
| **Works in your tool, unchanged** | Ships as a Claude Code skill, a Cursor rule, a Codex `AGENTS.md`, a Kiro steering doc, or a paste-anywhere prompt — one install command, no config. |
| **Reads the code, not the marketing** | Where a doc and the code disagree, it trusts the code and notes the discrepancy. Every claim is anchored to a `file:line`. |
| **True decision trees** | Branching logic is drawn as an actual tree with every fork and outcome — a linear "chain of boxes" is treated as a build failure, not an acceptable diagram. |
| **Knows its audience** | Identifies who the explanation is for — new hire, SME, leadership, auditor — and shifts depth, tone, section order, and video pace to fit. |
| **Frame-accurate narration** | Visuals are synced to the spoken sentence and checked on the *final* rendered file, not on intermediates. |
| **Remembers your decisions** | Your course-corrections ("slow the intro", "cut that section") are written to a persistent decision log and honored across long sessions and full rebuilds — never silently reverted. |
| **Won't melt your laptop** | The render pipeline sizes its own parallelism from real CPU + free RAM, backs off under load, and writes every frame to disk as it goes — a crash loses one frame, not the batch. |
| **Honest about limits** | On a machine without video tooling it produces the webpage and says *exactly* what it skipped and how to unlock it. It never fakes a deliverable. |
| **Verifies before it hands over** | Six deterministic checks (tree-shape, fact-tracing, content bans, DOM render, narration sync, media integrity) run on every build. |
| **Reproducible** | Stamps the exact code snapshot into both deliverables and writes a rebuild guide, so anyone can tell which version was explained and regenerate it. |

---

## Audience-tailored, by design

Coordn8 identifies **who** the explanation is for before it designs anything, and changes depth, tone, section order, and video pace accordingly:

| Audience | Entry point | Depth | Register |
|---|---|---|---|
| New engineer | what & why → architecture | full, scaffolded | warm, encouraging |
| Senior / SME | straight to the engine | exhaustive | precise, dense |
| Leadership | problem → impact | shallow machinery | outcome-first |
| External / OSS | mental model first | self-contained | welcoming |
| Auditor | the full decision surface | every branch & terminal | neutral, traceable |

It applies real teaching discipline — curiosity gap first, one idea per beat, concrete before abstract, progressive disclosure, explicit bridges, close the loop — the same principles the best short-form teaching channels use.

---

## Capability tiers — platform-agnostic

Coordn8 runs wherever it's invoked and degrades **honestly** to the richest tier the environment supports. Run `npx github:ompandey98/coordn8 detect` to see your tier.

| Tier | Environment | Webpage | Video | Gates |
|---|---|---|---|---|
| **A** | shell + files + `ffmpeg` + headless Chrome | ✅ | ✅ | all |
| **B** | shell + files, no `ffmpeg`/Chrome | ✅ | ⏭️ skipped (unlock path shown) | data/content/structure |
| **C** | file write only (artifact env) | ✅ | ⏭️ | in-model |
| **D** | chat only | code-block | ⏭️ | in-model |

It **never** claims a deliverable it didn't produce. A skipped video is stated as skipped, with the exact install command to unlock it.

---

## The verification gates

Nothing ships past a red gate. These run as real scripts (`scripts/gates/`) and are advisory — findings are leads the agent re-checks against the code.

| Gate | Checks |
|---|---|
| `tree_shape` | flows are real decision trees, not linear chains; forks have ≥2 labeled exits; terminals cover all outcomes |
| `facts_trace` | every claim/number resolves to a real `file:line` |
| `content_ban` | no customer/case IDs, emails, person names, or jargon handles in the final payload |
| `dom_smoke` | headless render: all tabs present, marker counts match, zero external dependencies |
| `beat_sync` | video: no visual enters ahead of its sentence; duration within tolerance; per-sentence frames extracted for inspection |
| `media_probe` | video: resolution, fps, audio present, clean decode |

Run them all against a build folder: `npx github:ompandey98/coordn8 gates path/to/Coordn8`

---

## Under the hood

```
coordn8/
├── PLAYBOOK.md              # THE BRAIN — canonical, embedded verbatim everywhere
├── VERSION
├── install.sh               # one-command installer (detects/targets your tool)
├── bin/coordn8.js           # npx coordn8 — init / print / detect / gates / check
├── adapters/                # GENERATED from the brain (byte-identical); do not hand-edit
│   ├── claude-code/         #   SKILL.md + /coordn8 command
│   ├── cursor/              #   coordn8.mdc
│   ├── codex/               #   AGENTS.md
│   ├── kiro/                #   steering doc
│   └── generic/             #   paste-anywhere prompt
├── build/generate_adapters.py   # single-source generator + --check (drift = CI fail)
├── scripts/
│   ├── env_detect.py        # capability detection + bounded parallel budget
│   ├── run_gates.py         # consolidated advisory gate report
│   ├── gates/               # the 6 gate scripts above
│   └── render/              # bounded parallel pool + deterministic frame capture/encode/concat
├── tests/run_tests.py       # verbatim byte-equality + gate unit tests (no pytest needed)
├── examples/                # generated pages → GitHub Pages gallery
└── docs/                    # architecture, teaching principles, adding a tool adapter
```

**Parallelism that won't crash your machine.** The render pipeline computes its worker count from real CPU + free-RAM capacity, runs a bounded queue with load back-off, and writes every frame to disk as it goes — so a crashed worker loses one frame, not a batch. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Contributing

Coordn8's whole engine lives in one canonical file, and every tool adapter is *generated* from it and embeds it byte-for-byte — a CI check fails the build if any adapter drifts by a single character. If you fork it to extend the engine or add a new tool, regenerate and verify:

```bash
python3 build/generate_adapters.py        # regenerate the tool adapters from source
python3 build/generate_adapters.py --check # verify they're byte-in-sync (CI gate)
python3 tests/run_tests.py                 # run the full suite
```

Adapters under `adapters/` are generated — never hand-edit them. Adding a new tool is a small, single-place change; see [`docs/ADDING_A_TOOL.md`](docs/ADDING_A_TOOL.md).

---

## Author & license

Created by **pandeom**. Released under the [MIT License](LICENSE) — free to use, fork, and build on, with attribution.

<div align="center">
<sub>Coordn8 — because the fastest way to understand a system is to watch someone explain it well.</sub>
</div>
