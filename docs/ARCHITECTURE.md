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
  *get it* — is each idea shown, not just told?), a **medium-fit critic**
  (page only — is the page a genuine compact reader, or the video re-skinned?
  bar: remove the video tab entirely and the page still fully teaches), a
  **shareability / de-personalization critic** (both — could a stranger share
  this unedited? no requester-personal framing, and no narration that reads raw
  IDs/URLs aloud instead of speaking their meaning), and a continuously-running,
  learning **anti-slop critic** (both — does this read as a crafted teaching
  piece or as AI slop? it runs throughout the build and feeds every confirmed
  flaw forward via `slop_lessons.md`).

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

## 6. The render pipeline — a multi-engine blend on one timeline

The video is not rendered by a single engine. Each scene is drawn by the **best
free tool for its content**, and every engine emits the *same* frame model
(`frame_%06d.png` at the same fps/resolution) so the identical encode+concat path
joins them with no special-casing. This is how Coordn8 gets a 3Blue1Brown-grade
math look *and* crafted software/flow motion in one film, using only free,
no-API, locally-installable tools.

`scripts/render/capture_scene.py` drives it, with two front-end engines feeding
one shared timeline:

1. `capture` — headless Chrome opens `scene.html?t=<seconds>` and screenshots the
   frame for that instant, across the bounded pool, into a frames dir. Best for
   software / architecture / flow / graph scenes: HTML/CSS/SVG/Canvas plus the
   vendored JS libraries — **GSAP** (professional eased timelines), **D3**
   (force-directed dependency graphs, tree layouts, data transitions), and
   **Rough.js** (hand-drawn warmth over corporate boxes). Each frame is a pure
   function of `t`, so a killed capture loses only the in-flight frame.
2. `manim` — renders a **Manim** (3Blue1Brown's engine) scene to its own mp4, then
   ffmpeg *re-extracts* those frames into the SAME `frame_%06d.png` model at the
   pipeline fps/resolution. Best for math, precise geometry, and continuous
   transforms. `manim_render()` degrades honestly: if Manim (or ffmpeg) isn't
   installed it returns a SKIP (exit 2) telling the agent to render that scene
   with the HTML/Canvas engine instead — it never fakes frames.
3. `encode` — ffmpeg turns a frames dir + per-scene audio into an intermediate
   mp4 with an **explicit** fps (never a default — mismatched fps is a classic
   silent corruption).
4. `concat` — serial ffmpeg concat of scene mp4s into the final silent video;
   audio is muxed per scene. Because Chrome-captured and Manim-derived scenes share
   the exact frame model, concat joins them seamlessly.

**Enhancement vs. base.** ffmpeg + headless Chrome + a TTS engine are the *base*
video pipeline — without them no video can be made, so they gate the tier. Manim
and the vendored JS libs are *enhancements*: they raise the ceiling dramatically,
but a failed Manim/libs install is reported as "enhancement unavailable" and the
scene falls back to plain HTML/Canvas — it **never** blocks the base video.
`scripts/render/fetch_visual_libs.py` vendors the pinned, free JS libraries
(GSAP 3.13.0, ScrollTrigger, D3 7.9.0, Rough.js 4.6.6, anime.js 3.2.2) into
`_build/vendor/` once, recording each file's sha256 in a manifest for provenance;
the agent then inlines the ones a scene uses so the offline/self-contained
contract holds. `scripts/setup_env.py` installs the whole toolchain — base **and**
enhancements — from one command, sudo-free where possible, re-probing after each
install so it never claims a success it didn't observe.

## 6a. Subtitles / captions (accurate by construction)

Captions are cheap and correct here because they are a *re-serialization of the
beat map*, not a re-transcription. The narration text and its exact per-sentence
timings already live in `_build/beatmap.json` (built from the TTS speech marks).
`scripts/render/subtitles.py` turns that same data into caption files, so the
words and times are the spoken ones by construction:

- `srt` — a standards-compliant `.srt` sidecar (de-overlapped, sequentially
  renumbered).
- `vtt` — a WebVTT `.vtt` for the page's `<video>` player.
- `mux` — muxes the `.srt` into the mp4 as a **soft, selectable** subtitle track
  (`mov_text`), video/audio stream-copied (no re-encode, no quality loss), with
  the track **off by default**. The viewer toggles captions on/off in any standard
  player (VLC, QuickTime, browsers) — exactly the VLC-style embedded-subtitle
  behavior, plus the sidecar `.srt` for players that prefer it.

`scripts/gates/subtitle_check.py` is the anti-drift backstop: it parses the emitted
`.srt`/`.vtt` and proves every cue's text equals the corresponding narration
sentence *verbatim* (whitespace-normalized only), that the cue set covers the
narration with none dropped or added, and that timings are monotonic, non-negative,
`start < end`, and within the video's duration when an mp4 is available. Since the
captions are derived from the beatmap this passes trivially on a clean build — its
job is to catch a hand-edit or a future regression that lets caption text diverge
from the voice.

## 7. Sync verification

`scripts/gates/beat_sync.py` reads `beatmap.json` (sentence-level start/end
timestamps from TTS speech marks) and, on the **final** mp4, extracts a frame at
each sentence start + offset so the agent can confirm on-screen content matches
the words. It structurally flags any visual scheduled to enter *before* its
sentence, non-monotonic timestamps, and duration drift beyond tolerance.
`scripts/gates/media_probe.py` checks resolution, fps, audio presence, and a
clean full decode. `scripts/gates/subtitle_check.py` (§6a) proves the caption
track carries the exact narrated words at sane times.

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

## 7c. Shareability, de-personalization, and the learning loop

The deliverables are authored for a broad, anonymous audience and must be freely
shareable — the kind of thing that gets uploaded to an internal video/streaming
platform and *enjoyed*, not clocked as AI slop. Two failure modes used to break
that, and both are now structurally addressed (PLAYBOOK 0.3a / 2.2a):

- **The requester is the director, not the subject.** What the person says in the
  chat sets *direction* (scope, focus, depth) but must never become *content*. A
  line like "since you already know the basics, we'll skip them" or "don't worry
  if this gave you a headache" leaks the requester's knowledge state or mood into
  a video a hundred other people should be able to share unedited. The fix is three
  operations: re-voice relevant framing into a neutral statement about the system
  ("this covers X; the prerequisites are Y"), drop irrelevant personal detail
  entirely, and preserve every *relevant* fact (de-personalizing never means losing
  context). `content_ban.py` gained a `requester_personal_framing` pattern class,
  and a **shareability/de-personalization critic** (§4) reads both deliverables
  cold. Generic teacherly "you" ("you'll see this in `foo.py`") is explicitly not
  flagged — only the requester's private context is.

- **Voice speaks meaning; the frame carries the specific.** Narration was often a
  transcription of the screen — reading a raw AWS account number, URL, hash, or
  file path aloud. The frame shows the literal (labeled); the voice says what it
  *is* ("the AWS account the pipeline deploys into"). `content_ban.py` gained a
  `read_aloud_literal` pattern, applied to the narration script (not just the HTML),
  and the same critic flags spoken literals.

- **The audience tailors design; it is never a role the deliverable greets.**
  Coordn8 identifies who the explanation is for and shifts depth, entry point, pace,
  and register (PLAYBOOK 0.3) — but the audience is an *input to composition*, never
  a person the artifact speaks to. A video generated "for a new engineer" that opens
  "welcome to the team" is nonsensical: a hundred strangers open the same file, none
  of them joining anyone's team. `content_ban.py` gained an
  `audience_roleplay_greeting` pattern that flags every such salutation; the fix is
  to state what the *system* is, greeting no one.

- **Factual correctness is non-negotiable — no invented acronym expansions.** A
  confident guess at what an abbreviation "stands for" is a factual defect, not a
  stylistic one. Every full-form must trace to a real source (docs/code); an unknown
  abbreviation stays as-is (PLAYBOOK 2.6). `content_ban.py` gained an
  `acronym_expansion` pattern that surfaces the *act* of expanding an abbreviation so
  each one is verified before it ships — a regex can't know an expansion is wrong,
  but it guarantees a fabricated one can't slip through unreviewed.

- **No stale build/source metadata on frames or in narration.** Version stamps,
  `TODO`/`@author` comment markers, and license/copyright headers teach nothing and
  read as a machine dump if they land on a frame or in the spoken script (PLAYBOOK
  2.1a "no meta chrome"). `content_ban.py` gained a `stale_meta` pattern; the
  ground-truth snapshot hash lives only in the HTML footer, never a video frame.

## 7d. The final verification manifest (the anti-fraud backstop)

Every gate above validates one dimension, but nothing stopped a build from *saying*
"Verified: sync, animation, explainability" when only some of that was actually
checked — the exact "you claim it's done, but is it?" failure. `scripts/gates/verify_manifest.py`
closes it. The build writes `_build/verification.json` — one entry per checklist
item, each with a `status` (pass/fail/untested/skipped) and the `evidence` behind it
— and the gate enforces three things a model under pressure tends to violate: (1) no
`pass` may lack evidence; (2) every end-user-facing "Verified: …" word must be backed
by a genuine pass, and any that isn't is refused; (3) any required gate that is
missing, failed, or untested is surfaced as still-open with its recorded
risk/assumption. The gate emits the **only** claim string safe to show a human —
the intersection of what was claimed and what actually passed (`authorized_claim`) —
so the "Verified" line the user sees is never free-typed, only what the build earned.
Advisory in exit code, but treated as blocking in spirit: it is the last line of
defense before a false "Verified" reaches a person.

- **The learning loop (`_research/slop_lessons.md`).** Every confirmed flaw —
  from the anti-slop critic, any adversary, or the user — is appended to a
  per-project lessons file that is loaded *first* on every later pass and every
  future rebuild, so the same class of flaw is designed out up front rather than
  re-caught late. The **anti-slop critic** runs continuously throughout the build
  (not only at the final gate), both consuming this file and growing it. This is
  what makes Coordn8 get better at a given project over time instead of repeating
  the same mistakes.

## 7e. The handover summary (what was added, tested, and couldn't be done)

A build produces a lot of internal state — memory-palace files, gate reports,
per-scene frames — that the end user doesn't need to wade through. But they *do*
need three things stated plainly at the end, and Coordn8 always ends a run with
them (PLAYBOOK final handover):

- **Added / produced** — the concrete deliverables and their paths (`<slug>.html`,
  `<slug>.mp4`, `<slug>.srt`), and which optional capabilities were exercised
  (e.g. "Manim math scene", "embedded captions").
- **Verified** — what was actually checked and *how*: which gates passed on the
  **real emitted files** (not intermediates), tagged so a unit/lint/partial check
  is never dressed up as end-to-end. The `verify_manifest` gate is the source of
  truth for the "Verified:" line — no claim word appears unless it's backed by a
  genuine pass.
- **Couldn't be done here, and exactly why** — any capability that was
  unavailable on this machine, named specifically with its one-line unlock (e.g.
  "Manim not installed → the geometry scene used the HTML engine; `pip install
  manim` to enable the 3b1b look"; "no ffmpeg → video skipped, page shipped").

The discipline: showing the internal processing is *optional* (no mandate to
surface it, no harm if it shows), but the closing summary of added/verified/
couldn't-do is **required** and honest. A missing capability is never hidden and
is **never** written into the deliverables themselves — the page and video are for
the audience; the capability status is an operator line for the requester. This is
the same anti-fraud stance the `verify_manifest` gate enforces mechanically,
carried through to the human-facing handover.

## 8. Distribution

- `install.sh` regenerates adapters from the current brain, then copies the right
  one into your tool's config location. It always installs the *current* brain,
  never a stale copy.
- `bin/coordn8.js` is the `npx coordn8` CLI: `init`, `print`, `detect`, `gates`,
  `check`, `version`.
- CI (`.github/workflows/ci.yml`) runs the verbatim-sync check and the test suite
  on every push.
