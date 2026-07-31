# Coordn8 Architecture

This document explains how Coordn8 is built and, more importantly, *why* — the
design decisions that keep the packaged tool from ever being worse than pasting
the raw prompt.

## 1. Single source of truth: the brain

Everything flows from one file: [`engine/PLAYBOOK.md`](../engine/PLAYBOOK.md), the **brain**.
It is the complete engine: what to read, how to identify the audience, which
teaching principles to apply, how to orchestrate sub-agents, how to parallelize
safely, how to build and verify both deliverables.

Every adapter is *generated* from the brain and embeds it **byte-for-byte**
between two markers:

```
<!-- COORDN8:BRAIN:BEGIN -->
...the entire PLAYBOOK.md, unchanged...
<!-- COORDN8:BRAIN:END -->
```

`build/generate_adapters.py` writes the adapters; `--check` re-derives them and
fails if any on-disk adapter differs from what the current brain would produce,
**or** if the fenced brain in any adapter differs from `PLAYBOOK.md` by a single
byte. `tests/run_tests.py` asserts the same. This is the mechanical lock behind
the core guarantee.

### Why verbatim embedding matters

A packaged skill/rule could easily degrade the prompt — by summarizing it to fit
a description field, by paraphrasing, by truncating under context pressure. We
forbid all of that structurally: the adapter is a thin wrapper (a few lines of
host-specific glue) around the *entire* brain. In any tool, Coordn8's input to
the model is a superset of the raw prompt.

## 2. Advisory gates, not gatekeepers

The gate scripts in `scripts/gates/` are **advisory**. Their exit codes are
signals, never vetoes:

| Exit | Meaning |
|---|---|
| 0 | PASS — nothing to review |
| 1 | REVIEW — findings the agent should look at (NOT a hard failure) |
| 2 | SKIPPED — couldn't run here (missing tool/input); degrade honestly |
| 3 | ERROR — the gate itself broke |

Why advisory? A generic reusable checker is more rigid than code the model would
write fitted to *this* project. If we made gates hard blockers, a false-positive
could stop valid output from shipping — a failure mode the raw prompt doesn't
have. So the brain treats a gate finding as a lead to re-check against the code,
and retains the instruction to write a project-specific check when the generic
one doesn't fit. Gates *augment* judgment; they never replace it.

## 3. Capability detection and tiers

`scripts/env_detect.py` probes the environment and selects the richest tier:

- **A**: shell + files + `ffmpeg` + headless Chrome → webpage + video + all gates
- **B**: shell + files, no ffmpeg/Chrome → webpage + data/content/structure gates (video skipped, unlock path shown)
- **C**: file write only → single-file HTML only
- **D**: chat only → HTML in a code block

The invariant: **whatever tier you're in, the packaged output ≥ the raw prompt's
output in that same environment.** We degrade the *deliverable set*, never the
*rigor* — and we never fake a deliverable we couldn't produce.

## 4. Orchestration: a graph of loops over a fleet of agents

The brain runs as a graph, not a single pass (PLAYBOOK.md PHASE R):

- **Readers** fan out over subsystems, each writing facts to the memory palace.
- **Cartographer** joins reader output into `project_map.md` (code wins over docs).
- **Flow-builders** fan out, one per core unit, emitting decision-tree `flows.json`.
- **Storyboarder / visual designer** fan out, one per scene, turning each scripted
  beat into a concrete visual plan (`storyboard.json`) *before* any frame is
  rendered — what is drawn, what moves, and what each element explains. This is
  what stops a scene from collapsing into a static text box: a scene with no
  planned motion or no meaningful visual is rejected on paper, cheaply.
- **Motion director** joins the storyboard, enforcing movement continuity (a
  persistent object travels across beats) and one consistent visual system.
- **Renderers** fan out, one per scene, capturing frames independently.
- **Validators/adversaries** fan out to re-verify claims and hunt banned content —
  including three visual adversaries that run on both deliverables: a
  **visual-relevance adversary** (every visual must teach something and be the
  most-relevant depiction — no decoration-with-no-referent), a **composition/space
  critic** (the whole frame/width is used; density matches an explainer, not a
  slideshow), an **explainability critic** (would the target newcomer actually
  *get it* — is each idea shown, not just told?), and a **medium-fit critic**
  (page only — is the page a genuine compact reader, or the video re-skinned?
  bar: remove the video tab entirely and the page still fully teaches).

The memory palace (`_research/`, `_build/`) is the shared, persistent store every
agent reads and writes. Nothing is held only in a model's head; every fact lands
on disk with a reference, which is what makes the whole thing resumable.

## 5. Parallelism that won't crash the host

`scripts/render/parallel.py` is the bounded work-queue primitive:

- **Worker bound from real capacity.** CPU workers =
  `min(cores - 2, ceil(cores * 0.75))`, then capped by
  `free_RAM_MB / per_worker_MB` so a fleet of headless-Chrome frame workers can't
  exhaust memory. (Available RAM counts reclaimable pages — counting only truly
  free pages under-reports badly and needlessly throttles to one worker.)
- **Load back-off.** Before doing real work, a worker briefly waits if the 1-min
  load average or free RAM is over the ceiling — then proceeds anyway after a
  bounded time, so a permanently-busy machine still makes progress. It throttles;
  it never deadlocks.
- **Write-as-you-go.** Each frame/scene/finding is written to disk the instant
  it's produced. Frames are pure functions of `t`, so a crashed worker loses one
  frame; re-running fills only the gaps.
- **Bounded retries**, then the item is recorded as failed rather than aborting
  the whole run.

## 6. The render pipeline

`scripts/render/capture_scene.py` drives deterministic scene rendering:

1. `capture` — headless Chrome opens `scene.html?t=<seconds>` and screenshots the
   frame for that instant, across the bounded pool, into a frames dir.
2. `encode` — ffmpeg turns a frames dir + per-scene audio into an intermediate
   mp4 with an **explicit** fps (never a default — mismatched fps is a classic
   silent corruption).
3. `concat` — serial ffmpeg concat of scene mp4s into the final silent video;
   audio is muxed per scene.

## 7. Sync verification

`scripts/gates/beat_sync.py` reads `beatmap.json` (sentence-level start/end
timestamps from TTS speech marks) and, on the **final** mp4, extracts a frame at
each sentence start + offset so the agent can confirm on-screen content matches
the words. It structurally flags any visual scheduled to enter *before* its
sentence, non-monotonic timestamps, and duration drift beyond tolerance.
`scripts/gates/media_probe.py` checks resolution, fps, audio presence, and a
clean full decode.

## 7a. Composition / layout verification

Timing and container sanity say nothing about whether content physically *fits*
the frame — which is how cluttered, overflowing, misaligned scenes used to ship.
`scripts/gates/layout_probe.py` closes that gap. Its deterministic core reads a
`layout.json` of every visible element's bounding box (sampled at several `t`
per scene, since entrances animate) and flags any box that overflows the frame
or the 5% safe area, is larger than the frame, collides with another text box,
or renders below the type-size floor (PLAYBOOK 2.4a). An optional Pillow-backed
pixel probe measures the content bounding box of a rendered frame to catch
edge-crowding and barren/scattered compositions. Like every gate it is advisory:
a clean probe is necessary, not sufficient — the agent still looks at the frames.
The video also carries **no meta chrome** (no username, hash, build date, or path
caption burned into a frame); traceability lives in the page footer and rebuild
guide instead.

## 7b. Visual-density verification (the one-shot fix for low-effort output)

The most common quality failure was never a wrong fact — it was a low-effort,
under-visualized deliverable: a video that's a static text box with two shapes,
or a page that's a slideshow of boxes-and-bullets. That slipped through even at
max tokens on a heavy model because "make it rich" was never a *structural,
checked* requirement. `scripts/gates/visual_density.py` makes it one. Crucially it
runs on the **plan, before rendering** — the video's `storyboard.json` (PLAYBOOK
2.1a) and the page's `page_plan.json` (1.1a) — so a thin design is caught and
redesigned on paper, not after five iterations of burned frames. It auto-detects
which plan it's reading (`scenes[]` vs `sections[]`) and enforces the visual
doctrine (0.4a) mechanically: every scene/section has a **non-text primary
visual** (show, don't tell); every element has an `explains` mapping and isn't
decorative filler (random stars/flowers/pigs are flagged); depictions aren't a
wall of generic boxes; the composition uses the **full frame/width** rather than a
centered quarter with dead margins; every video scene has **real motion** under
narration (and the page's equivalent — an interaction/progressive build); and the
overall meaningful-visual density clears an explainer-grade floor. Advisory, like
every gate: a deliberate single-hero focal beat is a valid override, and a PASS
never excuses a plan that still reads low-effort to the agent's own eyes.

The page carries two extra behaviors here, because the page is a **reader, not the
video re-skinned** (PLAYBOOK 1.1a): the video and the page are different media with
different ideologies — the video is a linear narrated film; the page is a compact,
random-access reader organized by information architecture, whose structure is
*derived per-project* (how many tabs, what splits, what depth) rather than mirrored
from the video's scene order. So (1) an optional "Watch" tab that merely embeds the
sibling mp4 in a player with speed controls is **exempt** from the carry-your-own-
visuals checks — but must stay an optional guest, not the spine; and (2) a section
that self-declares it's a transcribed video beat (`not_video_scene:false`) is
**flagged** — the medium-fit critic's bar is that removing the video entirely still
leaves a page that fully teaches on its own.

## 8. Distribution

- `install.sh` regenerates adapters from the current brain, then copies the right
  one into your tool's config location. It always installs the *current* brain,
  never a stale copy.
- `bin/coordn8.js` is the `npx coordn8` CLI: `init`, `print`, `detect`, `gates`,
  `check`, `version`.
- CI (`.github/workflows/ci.yml`) runs the verbatim-sync check and the test suite
  on every push.
