<!-- Shipped inside Coordn8 as the PROVENANCE for PLAYBOOK Phase F. -->
> **Where this came from.** This document was written by a developer who used Coordn8 on a real
> production build and hit the failure it describes: a ~6-minute narrated video that took **4
> delivered versions and 8+ full render cycles**, where a run with *every automated gate green*
> was rejected with **ten distinct real defects**. It is kept here verbatim as the record behind
> **Phase F** of [`engine/PLAYBOOK.md`](../engine/PLAYBOOK.md).
>
> **Implementation status in this package (v1.5.0).** All ten ranked package changes from Part 8
> are implemented: named beats that THROW + instrumentation mode + `section()` + bounded-text
> default + `subjectId` + connector `from`/`to` (`scripts/render/scene_runtime.js`); gates S1–S8
> (`scripts/gates/check_semantics.py`), I1/I2 (`check_indices.py`), DEN (`check_density.py`),
> approved-strings/repetition/identifier-sidecar sweep (`check_content.py`); Stage V with the
> blindness + freshness + previous-cut rules (`stage_v.py`); and the automatic claim manifest
> with semantic claims refused unless frame-verified (`verify_manifest.py`). A worked reference
> scene is at [`examples/example_scene.js`](../examples/example_scene.js).

# COORDN8 — REQUIREMENTS ADDENDUM v1
## "First-go quality" requirements for ANY Coordn8 video

**Scope: every video Coordn8 produces, for any repository, any domain, any audience.** Nothing in
this file is specific to one project. Where a real failure is cited it is cited as *evidence that
the requirement is necessary*, described in neutral terms (a "badge row", a "diagram node", a
"case-evidence panel") — never as domain content you need to understand.

**Written for:** (a) the Coordn8 package developer, and (b) any new agent session that opens the
package to build a narrated explainer video. Read this **with** `SKILL.md`. `SKILL.md` says *what
to build*. This file says **what reliably goes wrong, why the existing checks are blind to it, and
the gates + fleet that make the FIRST cut good.**

**Provenance (why this exists).** Derived from a full production build: ~6-minute narrated video,
11 segments, mixed canvas-rendered scenes and screen-capture clips, real product screenshots,
generative TTS, burned captions. It took **4 delivered versions and 8+ full render cycles**. On the
run where **every automated gate reported PASS**, the reviewer's verdict was that most of what was
claimed as done was not done, and listed **10 distinct real defects**. That gap — *all gates green,
deliverable rejected* — is the general problem this addendum fixes.

---

## PART 0 — THE CORE DIAGNOSIS (applies to every video)

**Every check Coordn8 runs today measures GEOMETRY and PRESENCE. Every complaint a human makes is
about MEANING.**

Geometry checks answer: do two boxes overlap? is a panel too small? is the frame empty? is an index
in range? is the audio in sync?

They cannot answer: **does this frame show what the voice is saying?** did this section lose the
visual it used to have? does this arrow point the right way? is this the right evidence for the
thing being discussed? is this text spilling out of its own container? would a stranger call this
well-made?

So a frame can be geometrically perfect and semantically wrong, and the suite calls it PASS.
Fix: keep the geometry gates (Part 4), add the semantic gates (Part 3), make a **blind vision
reverse check** a hard pipeline stage (Part 5), and run it all as a **parallel fleet where finders
and verifiers are different agents and the verifier never sees the builder's claims** (Part 6).

---

## PART 1 — THE TEN UNIVERSAL DEFECT CLASSES

These are properties of the *rendering and authoring model*, not of any subject matter. Every one
shipped at least once with all gates green. Treat as the standing regression suite.

### D1 — Silent non-finite alpha: an element renders NOTHING and nothing reports it
**Generic mechanism.** A scene computes an element's opacity from a timing expression. If any input
to that expression is `undefined` or `NaN`, the result is `NaN`. Canvas/DOM then draws **nothing**:
no error, no warning, no log line. The narration still names the missing thing.

**Observed instance.** A row of badge/label chips and a companion badge were both computed from an
out-of-range index into the beat-boundary array (see D2). Both were **completely absent** for their
entire beat while the voice described them. The reviewer flagged the section as empty and asked
whether anyone had looked at it.

**Why every gate passed.** The dead-frame gate only fires when the *whole frame* is empty; the frame
still had a title and other elements, so it read as "content present".

**Requirement.** `S1` (Part 3): instrument every draw call; **fail the build on any non-finite
alpha.** ~20 lines of code; would have saved a full review cycle on its own.

### D2 — Stale beat indices: the highest-frequency defect in any narration-locked video
**Generic mechanism.** Scenes address narration beats **by index** (`beat(3)`, `lineIn(t, 4)`,
`B[i]`). Frame-lock helpers typically **clamp** an out-of-range index to the last line instead of
raising. Therefore **every edit to the narration line count silently mistimes some beat** — and
editing the narration is the most common operation in the entire workflow.

**Observed instances — one root cause, six unrelated-looking symptoms:**
| Symptom | Stale reference |
|---|---|
| **~8 seconds of dead screen** while narration played | a whole stage gated on a beat that no longer existed → early-returned |
| An entire scene running on wrong timings | a `lineCount >= N` guard on a scene cut below `N` → fell through to a hardcoded-timing fallback branch |
| A stat tile never on screen while its own sentence was spoken | tile gated one beat later than its sentence |
| Two element groups printing **on top of each other** | a fade-out index clamped onto the same beat as the fade-in |
| A badge row never drawing at all | raw out-of-range array index → NaN (= D1) |
| A flow animation drawn **a beat late** | gated on the old index after a line was deleted |

**Requirement.** `I1/I2` (Part 4): static scan of every beat-index reference **and every raw
boundary-array index** against each scene's real line count, with comments stripped first (prose
otherwise creates false hits).

> **Package-level fix, strictly better than the gate:** make the helpers **throw** on out-of-range
> indices in verify mode, and let scenes bind beats **by name** (`beat('the-worked-example')`)
> resolved against the script. Deleting a line then becomes a loud resolve error instead of six
> silent mistimings. **This is the single highest-value change in this document.**

### D3 — Child-escape: text overflowing its own container
**Generic mechanism.** An unbounded text primitive is called inside a bounded container. The string
is wider than the container, so it spills out both sides. The container is nowhere near the frame
edge, so edge/safe-area probes pass.

**Observed instance.** A ~530 px sub-caption drawn inside a ~440 px card, overhanging ~45 px each
side. Reviewer called it out as visibly broken.

**Why the overlap gate passed.** It compares **two separate boxes**. A child escaping its **own
parent** is a containment failure that the gate does not model.

**Requirement.** `S3` (Part 3), plus a package rule: **inside any container, bounded/width-fitted
text is the default**; unbounded text requires an explicit opt-in flag.

### D4 — Right shape, wrong content: the asset doesn't belong to what's being narrated
**Generic mechanism.** A beat discusses instance/example/case X. The panel shows a screenshot of
instance Y. The screenshot is real, legible, well-cropped, correctly composed, PII-clean — and
about the wrong thing. Nothing in the pipeline binds an asset to the subject of its beat.

**Observed instance.** A beat narrated one specific comparison while displaying the analysis output
for a *different* comparison. The correct asset was already present in the same asset folder,
unused.

**Requirement.** `S5` (Part 3): every asset crop declares a `subject_id`; every beat declares the
`subject_id` it discusses; a mismatch **fails**. Cheap to implement, and it prevents the most
credibility-destroying defect class there is — a confident visual that contradicts the voice.

### D5 — Direction: flows and arrows pointing the wrong way
**Generic mechanism.** A path's point array is built in one order and then reversed (or built
backwards) while the arrowhead is drawn at a fixed end. The animation then contradicts its own
arrow, and contradicts the sentence describing the direction.

**Observed instance.** A return/feedback path animated *away* from its destination node while the
arrowhead pointed *into* it.

**Why every gate passed.** **No geometry gate has any concept of direction.** An arrow is just
drawn lines.

**Requirement.** `S6` (Part 3): every connector declares `from` and `to` node ids; the gate checks
the rendered path's first/last points against those nodes' real positions **and** that the arrowhead
is at the `to` end.

### D6 — Text sitting on drawn lines
**Generic mechanism.** Labels, numbers or stat tiles are positioned in a band that a diagram
connector passes through. The text lands on the line.

**Why the overlap gate passed.** It compares card/text **boxes**; a **connector is not a box**.

**Requirement.** `S4` (Part 3): record connector polylines during instrumentation; fail when a
visible text box intersects a segment within a clearance margin.

### D7 — Dead space: the "low effort" look, never measured
**Generic mechanism.** Content occupies the upper portion of the frame; the lower half is empty for
the whole beat. Or a title arrives on one beat while its supporting content is gated to the next,
leaving a nearly blank frame under live narration.

**Observed instance.** Measured after the complaint: **540 px (half the frame height) at 0.0 % ink**
across several scenes; one beat at **1.8 % mean ink**. The reviewer's wording was that it looked
machine-generated and effortless.

**Requirement.** `DEN` (Part 4): ink coverage per horizontal band per spoken line; fail on ≥3
consecutive dead bands. Also print the mean-ink table — it **ranks** scenes, and the weakest scene
is almost always the one a reviewer names next.

### D8 — Correction-as-content: turning feedback into narration
**Generic mechanism.** A reviewer corrects the builder's *misunderstanding of the system*. The
builder writes that correction into the script and onto the frame as an assertion.

**Observed instance.** A reviewer corrected a factual error about how two components relate. That
correction was then spoken and displayed as a sentence in the video. The reviewer's response was
that they were supplying information, not content.

**Why no gate can catch it.** It is a category error, not a measurable property.

**Requirement (R4, Part 2).** Classify every requester line before use: **DIRECTION** (what to
build) / **CONTENT** (what the audience hears) / **CORRECTION** (a fact the builder had wrong).
Corrections update your model of the system and are **never narrated**. The same rule also excludes:
the requester's local file paths, their knowledge state, their mood, their deadline, their tooling.

### D9 — Approved strings drifting
**Generic mechanism.** The requester approves a specific string (title, tagline, claim) verbatim. A
later pass substitutes a different phrase — often a *theme* or *spine* phrase from elsewhere in the
brief — and the approved wording is silently lost.

**Observed instance.** An approved title was replaced twice across versions; the requester had to
search back through the conversation to recover their own approved wording.

**Requirement (R5).** Maintain **`approved_strings.json`**: each approved string byte-exact, with
the quote that approved it. A gate diffs the deliverable against it. **A theme phrase is never a
licence to rewrite an approved string.**

### D10 — Repetition, and voice-reads-the-screen
**Generic mechanism.** (a) The same fact is restated across multiple beats, so the video feels long
and circular. (b) The narration recites a list of names/values that is already visible on the frame.

**Observed instances.** A reviewer reported the same information "repeating ten times" without ever
connecting in the flow; separately, asked that an enumerated list of source names be dropped from
the voice because the visual already carried it.

**Requirement.** N-gram repetition gate across narration lines (a 5-gram shared by two different
lines = fail; one build went **16 → 0**), plus the rule: **if a set of names/values is on the frame,
the voice speaks the category, not the list.**

---

## PART 2 — THE SEVEN PRE-BUILD RULES (they prevent re-work, in any domain)

**R1 — Read the reference material end to end before writing one line of script.** If any prior
deck, doc or earlier cut exists, its phrasing sets the register and its facts are ground truth.
Authoring narration from a general impression of the codebase, without the references, produced a
wholesale rejection.

**R2 — Freeze a sourced fact ledger before scripting.** Every number, name, abbreviation expansion
and example detail in one file with a source pointer. **No source → not renderable.** (Figures were
burned into captions and later had to be retracted; a stale sidecar caption file still carried the
retracted numbers, undetected, until an audit found it.)

**R3 — Lock CONTENT before building VISUALS.** Get the narration approved *as text*, then build.
Every render cycle that changed the script forced a full downstream re-render, and each re-render
introduced fresh D2 index bugs. Script churn is the dominant cost driver.

**R4 — Classify every requester line DIRECTION / CONTENT / CORRECTION** (see D8). Only CONTENT
reaches the audience. Log the classification.

**R5 — Keep `approved_strings.json`** (see D9). Approved = frozen until the owner changes it.

**R6 — One idea per frame; a change of subject gets a new frame.** Both failure directions are
real: cramming a mechanism *and* its outcomes into one frame drew a complaint, and so did splitting
one continuous idea across two frames when it should have revealed in place. Test: **one idea → one
frame; the parts of one idea → revealed in place, one at a time.**

**R7 — Prefer the shorter, denser cut.** Across this build every accepted change made the video
*shorter* while *adding* substance (three successive cuts trimmed total runtime). Removing
repetition is nearly always the correct first edit.

---

## PART 3 — SEMANTIC GATES (the new ones; they catch what geometry cannot)

All require an **instrumentation mode** in the scene runtime that records, per frame: every drawn
box (`kind, x, y, w, h, alpha`), every connector polyline (`from, to, points`), every asset draw
(`path, crop, subject_id, scale`). Without instrumentation none of these are implementable.

Run on every scene, every spoken line, ≥5 samples/second.

| ID | Gate | Fails when | Catches |
|---|---|---|---|
| **S1** | non-finite alpha | any recorded element has `NaN`/undefined alpha | **D1** — silently invisible element |
| **S2** | raw boundary index | a raw `B[i]`-style index can exceed the built array | **D1/D2** at the source |
| **S3** | child-escape | a text box crosses the bounds of the container it is drawn inside | **D3** — overflow |
| **S4** | text-vs-connector | a visible text box intersects a drawn connector segment | **D6** |
| **S5** | asset↔subject | a beat's `subject_id` ≠ the `subject_id` of an asset it draws | **D4** — wrong evidence |
| **S6** | flow-direction | path endpoints don't match declared `from`→`to`, or arrowhead is at the wrong end | **D5** |
| **S7** | section-presence | a **named section** renders with no content of its own (per-section, not per-frame) | **D1/D7** — a section that lost its visual |
| **S8** | previous-cut diff | a section's mean ink drops >40 % vs the last approved cut with no instruction to change it | an unrequested **regression** |

> **S7 + S8 are the pair that would have caught "this whole section is empty" on the FIRST run.**
> S7 requires sections to be *declared* in the scene (`section('name', …)`) — a small package
> change with outsized payoff.

---

## PART 4 — INVARIANT GATES (necessary, never sufficient)

| ID | Gate | Fails when |
|---|---|---|
| C1 | box overlap | two visible content boxes partially overlap (full containment is legal) |
| C2 | asset present | a declared asset never actually draws |
| C3 | cursor region | a captured cursor sits in dead space |
| C4 | repetition | an n-gram repeats across two narration lines |
| C5 | source-trace | a scene lacks its provenance comment |
| C6 | dead-frame | a sustained void (>0.6 s) **while a line is being spoken** |
| C7 | legibility | a screenshot panel renders below ~0.42× of source (text becomes unreadable) |
| C8 | cross-context | one subsystem's assets appear in another subsystem's scene |
| **I1/I2** | **beat-index** | any beat index (helper call or raw array) out of range for that scene |
| **DEN** | **density** | ≥3 consecutive dead horizontal bands during a spoken beat |
| SUB | subtitle fidelity | cue text ≠ narration verbatim; >2 lines / >42 chars-per-line / >7 s / >20 cps |
| ID | identifier sweep | real identifiers/PII on any frame **or in any sidecar file** |

**Two general findings worth encoding:**

- **On legibility (C7):** on-screen scale is governed by the crop's **WIDTH**, not its height.
  Cropping a text block shorter does **not** enlarge its glyphs. If a panel's text cannot reach the
  legibility floor at any width that fits the frame, **stop cropping** — keep the panel for
  *structure* and re-draw the load-bearing line as **real rendered text** at full size. That is the
  only fix that works, and it took a measured proof to establish.
- **On identifier sweeps:** sweep **sidecar artifacts too** (caption files, intermediate JSON,
  build logs shipped alongside). Retracted figures survived in a stale caption file long after they
  were removed from the video; any re-mux would have shipped them.

---

## PART 5 — THE VISUAL REVERSE CHECK (mandatory stage, every video)

Gates prove "not provably broken". Only this stage answers *"does this frame show what is being
said, and would a stranger call it well-made?"*

**Stage V — for every spoken line of every changed scene:**
1. Render the frame at **85–90 % through the line** (entrances settled, beat at full strength).
2. Send **image + its exact spoken sentence** to a vision model acting as a fresh reviewer with
   **no knowledge of the builder's claims.**
3. Fixed answer schema, per frame:
   - Does the frame show what the sentence says? `match` / `partial` / **`contradicts`**
   - Any text clipped, overflowing its container, or crossing a line?
   - Any panel unreadable at 1:1? (measure glyph height; do not estimate)
   - Any large dead region? Ragged alignment?
   - Does any arrow/flow direction contradict the sentence?
   - Would a discerning stranger call this **well-made** or **low-effort** — and specifically why?
   - Anything resembling a real identifier or private data?
4. **`contradicts` on any frame blocks the build.** The rest is a ranked defect list.

**Three rules that make Stage V actually work — each learned from it failing:**
- **Verifiers must be blind to the builder's claims.** Verifiers briefed with a claim list verified
  the claims and missed everything not on it. A reviewer pointed at a checklist finds checklist
  items.
- **Assert artifact freshness first.** Two verifier reports reviewed an artifact older than the
  fixes and reported already-fixed defects as severe — which made their *genuine* findings easy to
  dismiss. Compare mtime/hash against every source edit before reviewing.
- **Ask "what used to be here?"** Supply the previous cut's matching frame. That single question is
  what surfaces a section that silently lost its visual (D1/S7/S8); no verifier without the prior
  cut can find it.

---

## PART 6 — THE PARALLEL FLEET (agents, subagents, skills)

Run as a graph over a **shared on-disk memory** so nothing lives only in context and any worker is
restartable. Bound concurrency to `min(16, cores-2)`. Persist every artifact the moment it exists.

**Phase 1 — GROUND TRUTH** *(parallel fan-out → one join)*
| Role | Job | Output |
|---|---|---|
| `reader×N` | one per subsystem / reference doc, read end to end | fact files, every fact with a source |
| `transcriber` | exact wording + timings from any prior cut or deck | reference transcript |
| `fact-ledger` *(join)* | merge; **source code wins over docs**; flag every conflict | the sourced fact ledger (R2) |
| `input-classifier` | label every requester line DIRECTION / CONTENT / CORRECTION | the brief (R4) |

**Phase 2 — SCRIPT LOCK** *(serial; gates everything downstream)*
`scriptwriter` → `repetition-critic` (must reach 0) → `register-critic` (kills filler, bare-letter
enumeration, manufactured suspense, voice-reads-the-screen) → `fact-auditor` (every figure traces to
the ledger or is cut) → **HUMAN GATE: script approved as text before any visual work** (R3).

**Phase 3 — DESIGN** *(parallel per scene → one join)*
`storyboarder×N` — per scene: one idea, a non-text primary visual, ≥1 moving element, `explains` +
source per element, declared `subject_id`, declared connector `from`/`to`, declared `section` names.
`asset-binder` — crops with `subject_id` and a privacy-safe bound proved on a grid overlay.
`motion-director` *(join)* — continuity, one design system, R6 chunking.
`plan-gate` — run density/relevance **on the storyboard, before rendering a single frame.**

**Phase 4 — RENDER** *(parallel, deterministic)*
Frames are a **pure function of `t`** so any frame or scene re-renders in isolation and a crash
loses one item. Run static + instrumented gates **on the scene source before capture** — seconds,
versus minutes per render.

**Phase 5 — VERIFY** *(parallel; verifiers are BLIND)*
`frame-reviewer×N` (Stage V, one per scene) · `direction-auditor` (S6) · `subject-auditor` (S5) ·
`regression-auditor` (S8) · `identifier-auditor` (frames **and** sidecars) · `slop-critic`
(**runs continuously through all phases**, not only at the end) · `claim-auditor` (reads the
builder's claims **last**, marks each `supported` / `unsupported`).

**Phase 6 — REPORT**
Emit a manifest: one row per gate and per Stage-V frame, each with `status`, `evidence`, and
evidence strength (`verified-live` / `code-traced` / `untested`). **The only "Verified:" line a
human may be shown is the intersection of what was claimed and what passed with evidence.**

---

## PART 7 — CLAIM DISCIPLINE

The most damaging output of the build was not a defect. It was a status line of the form:

> *"Ten gates pass on all N scenes — [list of ten gate names]."*

Every word was **true**. The video had **ten real defects**. The claim was true and worthless, and
presenting it as a quality statement destroyed trust faster than any individual defect.

1. **Report what a gate measures, never what it implies.** "No two boxes overlap" — not "the layout
   is good".
2. **A gate passing is not evidence of quality.** State that in the same breath, every time.
3. **Only frame-verified items may be called done** for anything semantic: subject-correctness,
   direction, section presence, register, "does it look good".
4. **A subagent saying "confirmed" is not verification.** Re-check load-bearing claims directly.
5. **Never review a stale artifact.** Assert freshness first.
6. **End every status with what was NOT verified.**

---

## PART 8 — PACKAGE CHANGES (ranked by review-cycles saved per unit of work)

1. **Beat helpers THROW on out-of-range indices in verify mode; support named beats**
   (`beat('name')` resolved against the script). Eliminates D2 — six defects from one cause —
   permanently. **Highest-value change in this document.**
2. **Ship instrumentation mode as a first-class feature** (per-frame boxes, connectors, assets,
   alphas). Every gate in Part 3 depends on it; without it they cannot be written.
3. **Add `section()` declarations** → enables S7 presence and S8 previous-cut diff, i.e. catching
   "this whole section is empty".
4. **Bounded text is the default inside containers**; unbounded requires an explicit flag → kills D3.
5. **Require `subject_id` on every asset crop and every beat** → kills D4.
6. **Require `from`/`to` on every connector** → kills D5, which nothing else can catch.
7. **Ship Stage V as a pipeline stage** with the blindness rule and freshness assertion built in.
8. **Ship `approved_strings.json` + a diff gate** → kills D9.
9. **Emit the claim manifest automatically** so a "Verified:" line cannot be free-typed.
10. **Warn loudly when the narration line count changes**, naming exactly which scenes must be
    re-checked. It is the most dangerous edit in the system.

---

## PART 9 — FIRST-GO CHECKLIST

**Before rendering**
- [ ] Reference material read end to end; register target identified (R1)
- [ ] Sourced fact ledger frozen (R2)
- [ ] Every requester line classified DIRECTION / CONTENT / CORRECTION (R4)
- [ ] `approved_strings.json` populated (R5)
- [ ] Script approved **as text**; repetition = 0; no voice-reads-the-screen (R3, D10)
- [ ] Storyboard passes: one idea per frame, non-text primary visual, ≥1 moving element, `explains`
      + source per element, `subject_id` per asset and beat, `from`/`to` per connector, `section`
      names declared (R6)
- [ ] Static gates on source: I1/I2 beat-index, S2 raw index

**After rendering, before any claim**
- [ ] Instrumented gates: S1, S3, S4, S5, S6, S7, S8
- [ ] Invariant gates: C1–C8, density, subtitle fidelity, identifier sweep (frames **and** sidecars)
- [ ] **Stage V on every spoken line of every changed scene** — blind reviewer, fresh artifact
- [ ] Claim manifest emitted; "Verified:" = claimed ∩ passed-with-evidence
- [ ] Open items stated explicitly

**The standard.** A gate passing means *not provably broken in one mechanical way*. Only a human, or
a blind vision reviewer comparing the actual frame to the actual sentence, can say a video is
**good**. Build the pipeline so that judgement happens on the **first** cut.
