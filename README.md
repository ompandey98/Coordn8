<div align="center">

# ⚙️ Coordn8

### Point it at any codebase. Get back a narrated, word-synced **video** — and an interactive **page** when you ask for it — framed for the people who will watch it, with every claim traced to a line of code.

[![CI](https://img.shields.io/badge/CI-passing-2ea44f)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Works with](https://img.shields.io/badge/works%20with-Claude%20Code%20·%20Cursor%20·%20Codex%20·%20Kiro-8957e5)](#works-in-every-tool)
[![Local only](https://img.shields.io/badge/runs-local%20·%20no%20keys%20·%20no%20accounts-orange)](#what-stays-on-your-machine)

*Install once. Run it in any repository, from any AI coding tool.*

</div>

---

Coordn8 reads a whole repository, finds the logic that actually decides things, and turns it into something you can show to a person:

- a short **narrated video** in which every visual lands on the word it belongs to, with captions you can switch on and off that carry the exact spoken words;
- an **interactive page**: one self-contained HTML file with real decision-tree diagrams, a glossary that explains terms where they appear, and a claims ledger that ends the page.

**By default it builds the video.** Say "the page too", "both", or "page only" to change that. You approve a plan before anything is built, and the files it produces are named after your project.

---

## Quickstart

### Claude Code — as a plugin (recommended)

```text
/plugin marketplace add ompandey98/coordn8
/plugin install coordn8@coordn8
```

From a shell, the same thing is `claude plugin marketplace add ompandey98/coordn8` followed by `claude plugin install coordn8@coordn8`. The plugin brings:

- the Coordn8 skill (`/coordn8:coordn8`);
- fifteen role agents;
- the quality hooks;
- the `coordn8` command on the Bash path;
- an optional output style (`/output-style coordn8:coordn8`).

### Any tool — one command

```bash
npx -y github:ompandey98/coordn8 init all
```

This one command installs everything, always at the newest version, and never stops to wait for you. It works through these steps in order:

1. **The package**, into each tool you have.
2. **Dependencies:** ffmpeg, a headless browser, Python imaging, the layout and animation libraries, and the icon library.
3. **Local voices.**
4. **The open-source MCP servers** that need no sign-in.
5. **A dated copy of the public reference docs.**
6. **Connectors that need your sign-in**, offered last. The offer waits 20 seconds and then skips. Declining loses only that one connector.

A step that fails is skipped with its reason and the one-line fix, and the rest carry on. Run it again at any time to update. The installer prints `installed vX → vY`.

Use **one route per machine**. The plugin and the npx install both carry the same skill and agents, so having both gives you two copies of each agent. On the npx route the hooks are opt-in, because they edit your `~/.claude/settings.json`. Set `COORDN8_HOOKS=1` to have them merged in; uninstall takes them out again.

Then open a **fresh session at your repository root** and ask for what you want: *"explain how a request is routed, for new engineers"*, or *"make the video and the page for the billing pipeline"*.

---

## What it does, stage by stage

```
 read ─▶ index ─▶ audience ─▶ diverge ─▶ design system ─▶ diagram plans ─▶ storyboard / page plan
                                                                                   │
  release ◀─ verification manifest ◀─ frame review ◀─ gates ◀─ render / build ◀────┘
                                          ▲                │
                                          └── reflect + retry with the failure record
```

1. **Read the code first.** A retrieval index (`coordn8 index`, `coordn8 find`) maps every symbol, document section and import. Short per-folder maps say what each directory holds. Readers then read the files end to end. Every fact carries `path:line` — and if no line can be found, the fact is marked *inferred*.
2. **Frame it for the audience.** The audience is inferred from your brief and context: engineer, manager, executive, designer, newcomer, external developer, someone non-technical. It is never defaulted to "explain like I'm five". Business audiences get the impact and the decision first. Experts keep their field's own terms.
3. **Diverge before committing.** For the look, the narrative spine, each hero figure and each pivotal scene, an idea-generator agent proposes at least three genuinely different options. It works from named techniques plus real seeded random prompts. A *separate* critic agent then rejects the defaults and the merely surprising. Every option and every reason is kept in `divergence.json`.
4. **Commit to one design system.** The **Palette Director** picks colours for this subject and audience, and any colour you name always wins. It shows the candidates it rejected and why, checks contrast in light and dark, and avoids repeating the palette of your last builds. A **style kit** sets the type, spacing, motion and transitions. The video and the page share it.
5. **Choose diagrams on purpose.** A selector first identifies the *behaviour* being shown — fan-in, staged process, branching, trust boundaries, a lifecycle — and then the layout. Every figure has a written plan that lists the options it rejected. A deterministic layout engine places the boxes and routes the connectors: the same graph always gives the same SVG.
6. **Narrate with the best voice on your machine.** Open-source neural voices run locally. A casting step picks the best fit for the narrative and audience and says why it rejected the others. Abbreviations are spoken letter by letter, while the captions keep the clean text. Word timings come from the voice engine itself, and a scene that cites a missing word refuses to render.
7. **Build with menus, not cages.** The scene catalog, caption styles and page components are starting points. You and the agent can bend them, mix them or invent something new. A gate only ever judges the result, never whether something is in a catalog.
8. **Verify, then let a blind reviewer look.** The gates check contrast, layout, connectors, word sync, captions, plain language, one term per concept, unbacked numbers, claims, chart honesty, density, disk and run health. After every render, contact sheets go automatically to a **blind frame-reviewer** agent. It sees only the frames and the exact sentences, never the builder's claims. A frame that contradicts its sentence blocks the build.
9. **Learn from failures.** Every failed gate or rejected output is recorded with its cause, and the retry carries that record. A cause that repeats becomes a lesson loaded first next time.
10. **Say only what was proven.** The final "Verified:" line is written by the verification manifest. It is never typed by hand, and it ends with what was *not* verified.

---

## What you get

| Deliverable | What is in it |
|---|---|
| `Coordn8/<project>.mp4` | the narrated video, with a caption track you can switch on and off |
| `Coordn8/<project>.srt` / `.vtt` | captions: the exact spoken words, split into readable lines |
| `Coordn8/<project>.claims.md` | every claim the video makes → `path:line` → read or inferred |
| `Coordn8/<project>.html` (when asked) | the interactive page: contents with scroll tracking; What/Why/How with How folded; decision trees; an example stepper; outcome tables; glossary tooltips that work with keyboard and touch; light and dark themes; print and phone layouts; the claims ledger |
| `Coordn8/_build/` | the plans (design system, diagram plans, storyboard, divergence), the gate reports, the frame-review verdicts |
| `Coordn8/_research/` | the project's memory: decisions, lessons, failure log, requirements ledger, index |

Optional extras:

- **Music and sound effects**, synthesized locally. They are off unless the storyboard switches them on. When on, they duck under the voice and are mixed to −16 LUFS, with separate stems.
- **A style borrowed from a reference video or image** (`coordn8 distill`). Coordn8 matches the style and never copies the content.
- **Your brand colours and fonts** (`coordn8 brand`). Coordn8 checks their contrast, shows what it would change, and asks before applying.
- **Explain modes:** one figure with its claims, or a chat-only answer that writes no files (`coordn8 explain`).

---

## What stays on your machine

Everything runs locally: no API keys, no paid tools, no accounts. Coordn8 never sends your text, audio or code to a hosted AI service. That includes speech: the voices are open-source models that run on your computer.

### What Coordn8 owns, and what you own

| Zone | Where | What install / update / uninstall may do |
|---|---|---|
| **Package** — Coordn8's own files | the plugin folder, `~/.claude/skills/coordn8/`, `~/.claude/commands/coordn8.md`, `~/.claude/agents/coordn8-*.md`, the adapter files | replace them freely |
| **Your data, everywhere** | `~/.coordn8/`: memory, preferences, profiles, your local catalog additions, lessons, choice history, voices, references, backups | create missing folders only — never edit or delete |
| **Your data, per project** | each project's `Coordn8/` folder, including `_research/` | never touched |
| **Your files** | your `AGENTS.md`, Cursor and Kiro rules, MCP configuration, settings | back the file up, then change only a clearly marked Coordn8 block, nothing else |

- **Uninstall** removes the package files and the blocks it added.
- **`uninstall --purge --yes`** also deletes `~/.coordn8/`. It lists everything first, and does nothing without `--yes`.

The details are in [docs/DATA_SAFETY.md](docs/DATA_SAFETY.md).

### Memory that respects the current conversation

Coordn8 keeps two levels of memory:

- **you**, in `~/.coordn8/memory/`: preferences, standing decisions, lessons learned across projects;
- **each project**, in `Coordn8/_research/`.

Entries are typed, dated and quoted from their source. A newer instruction *supersedes* an older one, and both stay visible. What you say in the current chat always outranks memory. When the two disagree, Coordn8 says so out loud instead of choosing silently.

---

## Works in every tool

| Tool | How it is installed | Agents and hooks |
|---|---|---|
| **Claude Code** | the plugin, or `init claude` (the skill + `/coordn8` command, with the full engine embedded) | real parallel role agents; hooks for session recall, the post-render scorecard, the agent log, and a stop gate that refuses "done" while the verification manifest has open items |
| **Cursor** | `init cursor` → `.cursor/rules/coordn8.mdc` | the roles run one after another, with the same blindness |
| **Codex / AGENTS.md tools** | `init codex` → a marked block in your `AGENTS.md` | the roles run one after another |
| **Kiro** | `init kiro` → `.kiro/steering/coordn8.md` | the roles run one after another |
| **Anything else** | `npx -y github:ompandey98/coordn8 print`, then paste the output as your first message | — |

Every adapter embeds the same engine byte for byte. `coordn8 check` proves it. Which host mechanisms are confirmed and which are assumed is recorded in [docs/HOST_INTEGRATION.md](docs/HOST_INTEGRATION.md).

---

## Commands

```text
coordn8 init|update [target]   install or update (targets: claude cursor codex kiro generic all)
coordn8 uninstall [--purge --yes]
coordn8 status · doctor [--probe] · detect · setup · check · version
coordn8 orchestrate <build>    fail-early verification ladder on an adaptive parallel pool
coordn8 gates <build>          every gate as one advisory report
coordn8 quality|perf|disk|clean <build>
coordn8 index <repo> · find <query>
coordn8 memory · recall · curate
coordn8 palette · diagram · distill · brand · explain
coordn8 voices · cast <brief>
coordn8 review prepare|collect <build>
coordn8 reflect record|wrap|retry-prompt|status
coordn8 refs · refs check · mcp · federation
coordn8 bench · evals
```

---

## Renders that are fast and gentle on your machine

- **Frames are streamed** straight into the encoder. A render is budgeted from a measured frame before it starts, and a watchdog stops it before the disk fills. Intermediates are cleaned up on success, on failure and on Ctrl-C.
- **Parallelism adapts to the machine.** It ramps up on spare capacity and backs off as soon as the machine is loaded. One browser stays open for the whole render.
- **`coordn8 doctor --probe`** measures this machine and projects the time and disk a job will need before anything is rendered.
- **`coordn8 quality`** scores every scene: alignment, overlap, voice-sync drift in milliseconds, density, legibility and motion.

---

## Proof

- `python3 tests/run_tests.py` runs the full suite.
- `python3 tests/audit_v2.py` maps every requirement to the checks that prove it.
- `python3 tests/v2/test_coverage_matrix.py` proves every capability is present.
- `python3 bench/run.py` scores six fixed projects and writes `bench/report.md`.

A gate passing proves the thing it measures, and nothing more. Whether a video is *good* is judged by the frame review and by you.

---

## Contributing

`engine/PLAYBOOK.md` is the single source of truth. After you change it, run `python3 build/generate_adapters.py`, then `python3 build/generate_adapters.py --check` and `python3 tests/run_tests.py`. New checks go *beside* the existing gates, and no change may alter what an existing gate passes or fails. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ADDING_A_TOOL.md](docs/ADDING_A_TOOL.md). Third-party components and their licences are listed in [docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md).

## Author & license

Built by **pandeom**. MIT — see [LICENSE](LICENSE).
