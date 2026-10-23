---
name: coordn8-frame-review
description: "Automatic blind frame review (Stage V) for a Coordn8 video. Runs automatically after every render, with no human step: builds contact sheets at exact beat-map times (85-90% through every spoken line, 4 frames before every cut, -16/-3/+2 around every key reveal), dispatches the coordn8-frame-reviewer agent BLIND with only the sheets and the exact sentences, collects its verdicts and validates them with stage_v.py check. A frame that contradicts its sentence blocks the build. Also ranks variant treatments blind (H4). Trigger after any capture_scene.py / orchestrate.py render, when a video was re-rendered, or when asked to review, check or rank the frames."
---

# Coordn8 frame review — automatic, after every render

> **Limits:** never send text, audio or code to any non-Anthropic hosted AI service. Local open-source voice models installed by Coordn8 are allowed. Read-only on anything outside the build folder unless your role says otherwise. Never name any external project in output that ships.

The reviewer is a Claude subagent reading PNG/JPEG images with its Read tool. No other
vision service is ever used, and no frame or sentence leaves this machine.

## When this runs

This step is **automatic**. The pipeline invokes it **after every render** — every time a
scene is captured, the video is re-encoded, or `orchestrate.py` reaches its judgement stage
(stage 5). The post-render hook reminds you; you do not wait for the user to ask, and you
never present a video as done while this review is missing, stale or blocked. A review that
has not run is an OPEN ITEM, never a pass.

`<pkg>` below is `${CLAUDE_PLUGIN_ROOT}`; when that is unset (the npx install), use the
`pkg_root` from `coordn8 doctor --json`. `<build>` is the `Coordn8/` folder of the project.

## The steps (in this order, every time)

1. **Build the sheets and the blind brief.**
   `python3 <pkg>/scripts/review/frame_review.py prepare <build>`
   (add `--prev <previous build folder>` when a previous cut exists; `<build>/_build/prev/`
   is used automatically when it holds a video and beat map).
   This asserts freshness first — it REFUSES if the video is older than any scene source,
   beat map or storyboard (re-render, then re-run). Frames are decoded straight into memory
   and composed into one sheet per scene under `<build>/_build/review/`; no frame file is
   ever written, and the step fails if one is left on disk. It writes
   `blind_brief.md`, `blind_brief.json` and `verdicts.template.json`, and refuses to write a
   brief that contains any builder claim.
   Exit 2 (SKIPPED) means no video, no ffmpeg or no Pillow: record Stage V as an open item
   with that reason; never call it passed.

2. **Dispatch the `coordn8-frame-reviewer` agent BLIND.** Give it ONLY the path of
   `<build>/_build/review/blind_brief.md`. The brief lists the sheet images and the exact
   spoken sentences and the answer template — nothing else. Do NOT pass the storyboard,
   `claims.json`, `verification.json`, gate output, your own description of the video, or
   what you expect it to find: a reviewer pointed at a checklist finds only checklist items.
   For a long video, dispatch one reviewer per sheet in parallel; each fills only its own
   slots.

3. **Write its verdicts JSON.** The reviewer has Read only, so it returns the filled
   template as one fenced JSON block. Save that reply **verbatim** (no edits, no
   "improvements") as `<build>/_build/review/verdicts.json`. When several reviewers ran,
   concatenate their `verdicts` and `events` lists into that one file.

4. **Collect and validate.**
   `python3 <pkg>/scripts/review/frame_review.py collect <build>/_build/review/verdicts.json`
   This checks the sheets are still fresh and unmodified, that every spoken line has exactly
   one verdict for its exact sentence (a missing slot fails), writes
   `<build>/_build/stage_v/verdicts.json` and runs
   `python3 <pkg>/scripts/gates/stage_v.py check <build>/_build/stage_v/verdicts.json`.
   The result is in `<build>/_build/review/stage_v.json`.

5. **Block on `contradicts`.** If any verdict says `shows_what_is_said: contradicts`, the
   status is BLOCK (non-zero exit): the build is not done. Fix the scene, re-render, and run
   this skill again from step 1. Other findings are a ranked defect list — fix the
   load-bearing ones, then re-review. `frame_review.py status <build>` reports the current
   state; `frame_review.py clean <build>` removes the sheets once the review has passed.

Report the result the way the gates are reported: say what was reviewed (which frames),
that a PASS means "a blind reviewer found no contradiction on the sampled frames", and what
was NOT verified (motion between samples, audio). Never write "looks good" without this
review behind it.

## Degraded path — hosts without subagents

When the host cannot spawn subagents, perform the same `coordn8-frame-reviewer` role
yourself, in sequence, as a separate pass: read ONLY `blind_brief.md` and the sheet images
it lists (do not re-read the storyboard, claims or your own notes first), fill
`verdicts.template.json` field by field, save it as `verdicts.json`, then run steps 4 and 5
unchanged. The blindness rule is the same: the artifact and the sentence, nothing else.
If the host cannot read images at all, Stage V is an OPEN ITEM — say so; never mark it passed.

## Variant ranking (H4)

For the opening, the hero figure and the payoff, each variant folder
(`_build/variants/<decision>/v1`, `v2`, ...) holds one contact sheet.
`python3 <pkg>/scripts/review/frame_review.py rank-prepare <variants_dir>` copies the sheets
under anonymous labels (A, B, C in no meaningful order) and writes `review/rank_brief.md`.
Dispatch `coordn8-frame-reviewer` with only that brief, save its reply as
`review/ranking.json`, then `frame_review.py rank <variants_dir>` maps the labels back,
sinks any treatment that contradicts its sentence, and writes `review/ranked.json` (best +
alternates, each with the reviewer's reason). Library: `frame_review.rank(variants_dir)`
returns the ranked list, or `[]` while the reviewer has not ranked yet.
