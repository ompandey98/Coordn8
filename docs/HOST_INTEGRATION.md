# Host integration — using the agent host's real mechanisms, by their current names

Coordn8's engine describes a **fleet**: readers, a cartographer, storyboarders, renderers,
adversaries, a claim auditor. That fleet is only real if the host actually runs the roles
in parallel with separate contexts, lets long work run without blocking the session, and
lets the agent look at what it rendered. This page maps each engine concept to the
mechanism each host really offers, and says how sure we are.

**How sure, in three words** (the same statuses as `data/host_capabilities.json`, which is
the machine-readable copy of this page):

- **confirmed** — read in that host's own public documentation on the date shown, at the
  URL shown.
- **assumed** — believed but not checked against that host's documentation. Coordn8 must
  work without it, so every assumed row names what Coordn8 does instead.
- **unsupported** — the way Coordn8 is delivered to that host gives the mechanism no way in
  (a pasted prompt has no hook configuration).

Claude Code rows were re-checked on **2026-10-23** against code.claude.com/docs. Codex,
Cursor and Kiro rows are **assumed** throughout: this build was only allowed to consult
Anthropic's public documentation, so nothing about those hosts is presented as confirmed.

To see what the current session is: `python3 scripts/host_detect.py` (environment and
project files only — it never uses the network and never writes).

---

## Claude Code (all confirmed, 2026-10-23)

| Engine concept | Mechanism, by its current name | Source |
|---|---|---|
| Fleet roles (R.1, F.6) | the **`Agent`** tool — one call per role, **all independent calls in ONE message** so they run concurrently, each with its own context. Older text, including earlier versions of this page, called it the "Task tool"; the current tools table names it `Agent`. | [tools reference](https://code.claude.com/docs/en/tools-reference) |
| How wide a wave can be | **20** subagents running at once by default; the 21st spawn fails with `Concurrent subagent limit reached` (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`). Size each fan-out wave at ≤ 20. | [subagents](https://code.claude.com/docs/en/sub-agents.md) |
| Roles that spawn roles | **3** layers below the main conversation by default (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`; `1` turns nesting off). A role at the limit does its own work and returns one summary. | [env vars](https://code.claude.com/docs/en/env-vars.md) |
| Roles that must not collide on files | **`isolation: worktree`** in an agent's frontmatter — a temporary git worktree, removed when the agent changes nothing. The session itself uses **`EnterWorktree`** / **`ExitWorktree`**. | [subagents](https://code.claude.com/docs/en/sub-agents.md) |
| Continuing a role | **`SendMessage`** to that agent by ID or name — it keeps its reads and findings. A fresh `Agent` call starts cold. | [tools reference](https://code.claude.com/docs/en/tools-reference) |
| Phase / scene tracking | **`TaskCreate`**, **`TaskGet`**, **`TaskList`**, **`TaskUpdate`** — one task per phase and per in-flight scene, exactly one in progress. These are present by default only on some models; `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` opts in on the rest. When the session has none of them, keep the phase table in `_build/orchestration/` and print it at every phase boundary. | [tools reference § Task tool availability](https://code.claude.com/docs/en/tools-reference) |
| The legacy checklist | **`TodoWrite`** is disabled by default in favour of the four Task tools; only `CLAUDE_CODE_ENABLE_TASKS=0` brings it back. Use it only when it is the tool the session actually has — never as the default. | [tools reference](https://code.claude.com/docs/en/tools-reference) |
| Long renders, installs | **Bash with `run_in_background: true`** → a task ID; the output goes to a file the agent reads with **`Read`**. `TaskOutput` was removed in v2.1.277, and `BashOutput` is not in the current tools table — do not poll with either. **`TaskStop`** stops a task. | [interactive mode § How backgrounding works](https://code.claude.com/docs/en/interactive-mode.md) |
| Render progress as it happens | **`Monitor`** — runs a command in the background and feeds each output line back, so the render's progress/ETA lines (2.5b) arrive as events. | [tools reference § Monitor tool](https://code.claude.com/docs/en/tools-reference) |
| The Phase-0 plan (0.7) | **`EnterPlanMode`**, then **`ExitPlanMode`** presents the plan for approval — an explicit gate, and the user's chance to widen or narrow scope. | [tools reference](https://code.claude.com/docs/en/tools-reference) |
| Looking at a frame (Stage V) | **`Read`** on a PNG or JPEG (GIF and WebP too) returns the image itself. Large images are downscaled before the model sees them, so crop the region under review first and read the crop. | [tools reference § Read tool behavior](https://code.claude.com/docs/en/tools-reference) |
| Deterministic batch steps | **`claude -p --output-format json --json-schema '<schema>'`** — the schema-checked result is in `structured_output`; check the field is present before using it. Add `--bare` for a repeatable run that skips hooks, skills, plugins and MCP. | [headless](https://code.claude.com/docs/en/headless.md) |
| Searching the repo | **`Read`** for files. On macOS, Linux and WSL the **`Glob`** and **`Grep`** tools are left out of the default tool set and searches go through `find` / `grep` in Bash (embedded fast versions); they come back when named in `--tools` / `--allowedTools`. Batch independent reads and searches in ONE message either way. | [tools reference § Glob tool behavior](https://code.claude.com/docs/en/tools-reference) |
| Gates the agent cannot skip | **hooks**: `Stop` blocks with `{"decision":"block","reason":…}` or exit 2; `SubagentStop`, `SessionStart`, `PostToolUse` are the other events Coordn8 uses. | [hooks](https://code.claude.com/docs/en/hooks.md) |
| Skills | Coordn8 ships as a skill; other installed skills live at `~/.claude/skills/<name>/SKILL.md`, `.claude/skills/<name>/SKILL.md` and `<plugin>/skills/<name>/SKILL.md` (invoked as `/plugin-name:skill-name`). See *Skill federation* below. | [skills](https://code.claude.com/docs/en/skills.md) |
| Local tools | MCP servers: user and local scope in `~/.claude.json`, project scope in `.mcp.json`, plugin servers in the plugin's `.mcp.json`. | [MCP](https://code.claude.com/docs/en/mcp.md) |
| Design, 3-D and image tools the user signed in to | **claude.ai connectors** — added by the user at claude.ai/customize/connectors, visible in Claude Code only when it is signed in with a claude.ai subscription; on Team and Enterprise only admins add them. See *Connectors* below. | [MCP § claude.ai](https://code.claude.com/docs/en/mcp.md) |

### The concurrency rule

> If two things do not depend on each other, they go out in the **same message**.

`Agent` dispatches, `Read`s, searches, Bash calls — all of them. Serialising independent
work is the most common way a capable host is wasted.

### One fan-out wave (Phase 1 of the fleet)

One message containing five `Agent` calls (≤ 20 per wave):

```
Agent(subagent_type="general-purpose", description="read auth subsystem",
      prompt="Read <paths> END TO END. Emit _research/reads/auth.md with every fact
              carrying a file:line. Do not summarise, do not skip long files. Flag any
              doc that disagrees with the code.")
Agent(... "read ingest subsystem"  -> _research/reads/ingest.md)
Agent(... "read render subsystem"  -> _research/reads/render.md)
Agent(... "transcribe prior cut"   -> _research/reference_transcript.md)
Agent(... "classify requester lines DIRECTION/CONTENT/CORRECTION" -> _research/brief.md)
```

Then a **serial** join (the cartographer) merges them — serial because it genuinely needs
every result at once (R.2). Track the wave with `TaskCreate` / `TaskUpdate`; run the
render with `run_in_background: true` and watch it with `Monitor`.

### Blindness is a dispatch property

A verifier is a **separate `Agent` call whose prompt does not contain the builder's
claims** (F.5 rule 1). Hand a reviewer your checklist and it verifies the checklist while
missing everything else — which is what actually happened. The claim auditor is
dispatched **last**, and is the only role that receives the claim list. A frame reviewer
gets `Read` and nothing that writes.

---

## Other hosts

Nothing below is confirmed: these rows were not checked against the hosts' own
documentation. What Coordn8 relies on there is what this package itself installs — the
brain as a rule, a steering document, an `AGENTS.md` block, or a pasted prompt — plus a
shell when there is one.

| Host | How Coordn8 arrives | Fleet execution | What stays identical |
|---|---|---|---|
| Codex / AGENTS.md CLIs | block merged into the project's `AGENTS.md` | shell-level parallelism (`orchestrate.py` + the adaptive pool); roles as separate invocations or sequential passes | stage ladder, bus files, blindness |
| Cursor | rule at `.cursor/rules/coordn8.mdc`, invoked with `@coordn8` | the agent runs the ladder; pool for parallelism; roles as sequential role-scoped passes | stage ladder, bus files, blindness |
| Kiro | steering document at `.kiro/steering/coordn8.md` (manual inclusion) | as Cursor | stage ladder, bus files, blindness |
| Generic (pasted prompt, maybe no shell) | `COORDN8_PROMPT.md` pasted as the first message | ONE agent performs each role **in sequence**, writing the same bus files and re-reading only its predecessor's artefact | order and blindness |

### How agent roles degrade to sequential passes — with the same blindness

Where a host cannot spawn agents, the roles still run, one after another:

1. Each role is its own pass, in stage order, writing the same bus file under
   `_build/orchestration/` that the parallel version would write.
2. Each pass re-reads **only** its predecessor's artefact, not the conversation so far.
3. A verifier pass reads the **artefact and the sentence it is checking**, never the
   builder's claim list. A single agent can be blind to its own claims by reading only
   those two things — and it must.
4. The claim auditor runs last and is the only pass that reads the claims.
5. The handover says which roles ran in sequence. **Never claim a fleet ran when it did
   not.**

The **order and the blindness** are what must survive, not the parallelism.
`scripts/host_detect.py` prints this as `fleet` whenever spawning is not confirmed.

### Where a mechanism is missing

| Missing | Coordn8 does instead |
|---|---|
| task-list tool | phase table on disk + a status line at every phase boundary |
| background runs / line streaming | foreground run that writes progress to a log file; read it between steps |
| plan gate | the plan as a message; wait for approval |
| image reading | pixel gates only — and Stage V stays an **open item**, never a pass |
| hooks | run `verify_manifest.py` by hand before any "done" |
| headless structured output | the step runs in the main session (usable elsewhere only if the `claude` CLI is on PATH) |

---

## Confirmed / assumed, per host and mechanism

Generated from `data/host_capabilities.json` (one row per host per mechanism; sources and
check dates are in the JSON).

<!-- HOST-MATRIX:BEGIN -->
| Mechanism | claude-code | codex | cursor | kiro | generic |
|---|---|---|---|---|---|
| Skills (packaged instructions the agent loads by name) (`skills`) | confirmed | assumed | assumed | assumed | unsupported |
| Spawn a sub-agent with its own context (`subagents`) | confirmed | assumed | assumed | assumed | assumed |
| Several sub-agents at once (`parallel_subagents`) | confirmed | assumed | assumed | assumed | assumed |
| Sub-agents spawning sub-agents (`subagent_nesting`) | confirmed | assumed | assumed | assumed | assumed |
| Continue a spawned agent with its context (`agent_messaging`) | confirmed | assumed | assumed | assumed | assumed |
| Run an agent in an isolated git worktree (`worktree_isolation`) | confirmed | assumed | assumed | assumed | assumed |
| Lifecycle hooks (Stop, SubagentStop, SessionStart, PostToolUse) (`hooks`) | confirmed | assumed | assumed | assumed | unsupported |
| Long command in the background, read later (`background_runs`) | confirmed | assumed | assumed | assumed | assumed |
| Stream a background command's lines back as events (`monitor`) | confirmed | assumed | assumed | assumed | assumed |
| Live task list for build phases (`task_tools`) | confirmed | assumed | assumed | assumed | assumed |
| Legacy checklist tool (`todo_legacy`) | confirmed | assumed | assumed | assumed | assumed |
| Plan approval gate (`plan_mode`) | confirmed | assumed | assumed | assumed | assumed |
| Look at a rendered frame or screenshot (`image_reading`) | confirmed | assumed | assumed | assumed | assumed |
| Deterministic batch step with schema-checked output (`headless_structured`) | confirmed | assumed | assumed | assumed | assumed |
| Local MCP servers (`mcp`) | confirmed | assumed | assumed | assumed | unsupported |
| claude.ai connectors (design, 3-D, image tools the user signed in to) (`connectors`) | confirmed | unsupported | unsupported | unsupported | unsupported |
<!-- HOST-MATRIX:END -->

---

## Skill federation

At run start the agent may list the other skills installed on the machine and match them
to Coordn8's stages — explain, diagram, design, writing, creativity, video, review:

```bash
python3 scripts/federation.py match                 # every stage
python3 scripts/federation.py match --stage diagram
```

It reads only `~/.claude/skills/*/SKILL.md`, `~/.claude/plugins/**/skills/*/SKILL.md` and
the project's `.claude/skills/*/SKILL.md` (`CLAUDE_HOME` overrides `~/.claude`), parses
each skill's name, description and `when_to_use`, and scores it against the phrase table in
`data/host_capabilities.json`. Output: ranked candidates per stage with the phrases that
matched, and the built-in implementation for that stage.

A candidate is only a suggestion. It is **adopted** for a stage only after a probe shows
it was used and its output passed that stage's gates:

```bash
python3 scripts/federation.py probe --project . --stage diagram \
        --skill /some-plugin:some-skill --used yes --gate-json Coordn8/_build/gates/diagram.json
```

Probes are appended to `Coordn8/_build/orchestration/federation.jsonl`; an adopted
candidate ranks first next time, a rejected one ranks last. **Coordn8 stays complete when
nothing matches**: every stage has a built-in implementation, so no capability is lost on a
machine with no other skills.

## Connectors and design tools

```bash
python3 scripts/federation.py connectors --project . [--tool-names FILE] [--session-init FILE]
```

Reads (never writes) `~/.claude.json`, the project's `.mcp.json` and plugin `.mcp.json`
files, and emits a stage → tool suggestion list. claude.ai connectors are fetched by Claude
Code at run time and are **not** stored in any local file, so they are recognised from the
session's tool names or its MCP server list; the name rules for that are recorded as
**assumed**. No stage ever requires a connector or an MCP server, Coordn8 never installs or
authorises a connector (only the user can, at claude.ai), and sign-in-only tools are
offered last. Choose only tools that do not run a non-Anthropic AI model on your content.

---

## Coordn8's own commands are part of the toolkit

| Command | Use it for |
|---|---|
| `coordn8 doctor --probe` | before any render: measured engine, frames/s, frame size, projected time + disk |
| `coordn8 orchestrate <build>` | the fail-early stage ladder on an adaptive pool |
| `coordn8 quality <build>` | measured per-scene output quality, six dimensions, in parallel |
| `coordn8 perf <build>` | run health — too slow, or too hard on the host |
| `coordn8 disk <build>` / `clean` | what the build holds on disk, and reclaiming it |
| `capture_scene.py stream` | render with **zero** frames written to disk |
| `scene_runtime.js` | author scenes so the D1–D5 defect classes are impossible, not merely detected |
| `scripts/host_detect.py` | which host this is, and what it does instead of each missing mechanism |
| `scripts/federation.py` | installed skills and connectors that could serve a stage |

---

## Verifying this is still true

```bash
python3 tests/v2/test_host.py       # this page, the capability table, host detection, federation
python3 tests/audit_addendum.py     # the host mechanisms named in the brain
python3 tests/run_tests.py          # everything
```

Re-check every Claude Code name against code.claude.com/docs (the tools reference, sub-agents,
headless, hooks, MCP and env-vars pages) and update the `checked` dates in
`data/host_capabilities.json` when you do.
