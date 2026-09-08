# Host integration — using the agent host's real mechanisms

Coordn8's engine describes a **fleet**: readers, a cartographer, storyboarders, renderers,
adversaries, a claim auditor. That fleet is only real if the host actually runs the roles
in parallel, with separate contexts, and if long work does not block the session.

"Spawn sub-agents" is aspirational. **"Dispatch these six Task calls in one message"** is
executable. This page is the concrete mapping, and it is enforced: `PLAYBOOK.md` §R.3c
carries the same rules, `tests/audit_addendum.py` asserts they are present, and
`tests/run_tests.py` fails if they disappear.

---

## Claude Code

| Engine concept | Mechanism | Why it matters |
|---|---|---|
| Fleet roles (R.1, F.6) | **Task tool**, `subagent_type: general-purpose` unless a better-fitting agent exists — **all independent calls in ONE message** | Independent Task calls in one message run **concurrently, each with its own context**. In separate messages they serialise. Separate contexts are also what makes verifier **blindness** (F.5) real rather than promised. |
| Phase/scene tracking | **TodoWrite** | A long build has many stages. Without a live list the user cannot see progress and a dropped stage is invisible — that is how "it skipped Stage V" happens. Exactly one item `in_progress`. |
| Long renders, installs | **Bash `run_in_background: true`** + **BashOutput** polling | A render can run for an hour. Blocking means no progress, no mid-run questions, and a timeout that discards completed work. The render already emits progress/ETA (2.5b) — surface it. |
| Phase-0 plan (0.7) | **plan mode / ExitPlanMode** | Makes plan approval an explicit gate, and gives the user a first-class chance to widen/narrow the deliverable scope before anything is built. |
| Repo ingest (0.1) | **Read / Glob / Grep**, batched in one message | Faster than shelling out, paginates large files, and parallel when batched. Reserve Bash for the render, the gates and ffmpeg. |
| Design refs, icons | **WebSearch / WebFetch** | Permitted by 0.4a rule 10 as an *enhancement*. Anything fetched must be open-licensed and **vendored/inlined** so the offline contract holds. |
| Continuing a spawned role | **SendMessage** to that agent | It keeps its context (its reads, its findings). A fresh Task starts cold. |

### The concurrency rule

> If two things do not depend on each other, they go out in the **same message**.

Task dispatches, Reads, Greps, Bash calls — all of them. Serialising independent work is
the most common way a capable host is wasted.

### One fan-out wave (Phase 1 of the fleet)

One message containing five Task calls:

```
Task(subagent_type="general-purpose", description="read auth subsystem",
     prompt="Read <paths> END TO END. Emit _research/reads/auth.md with every fact
             carrying a file:line. Do not summarise, do not skip long files. Flag any
             doc that disagrees with the code.")
Task(... "read ingest subsystem"  -> _research/reads/ingest.md)
Task(... "read render subsystem"  -> _research/reads/render.md)
Task(... "transcribe prior cut"   -> _research/reference_transcript.md)
Task(... "classify requester lines DIRECTION/CONTENT/CORRECTION" -> _research/brief.md)
```

Then a **serial** join (the cartographer) merges them — serial because it genuinely needs
every result at once (R.2).

### Blindness is a dispatch property

A verifier is a **separate Task whose prompt does not contain the builder's claims**
(F.5 rule 1). Hand a reviewer your checklist and it verifies the checklist while missing
everything else — which is what actually happened. The claim-auditor is dispatched
**last**, and is the only role that receives the claim list.

---

## Other hosts

| Host | Fleet execution | What stays identical |
|---|---|---|
| Codex / AGENTS.md CLIs | shell-level parallelism (`orchestrate.py` + the adaptive pool); roles as separate invocations | stage ladder, bus files, blindness |
| Cursor / Kiro | agent runs the ladder; pool for parallelism; roles as sequential role-scoped passes | stage ladder, bus files, blindness |
| No spawn, no shell | ONE agent performs each role **in sequence**, writing the same bus files and re-reading only its predecessor's artefact | order and blindness |

The **order and the blindness** are what must survive, not the parallelism. Never claim a
fleet ran when it did not — say which roles were sequential.

---

## Coordn8's own commands are part of the toolkit

Reach for these instead of re-deriving their work by hand:

| Command | Use it for |
|---|---|
| `coordn8 doctor --probe` | before any render: measured engine, frames/s, frame size, projected time + disk |
| `coordn8 orchestrate <build>` | the fail-early stage ladder on an adaptive pool |
| `coordn8 quality <build>` | measured per-scene output quality, six dimensions, in parallel |
| `coordn8 perf <build>` | run health — too slow, or too hard on the host |
| `coordn8 disk <build>` / `clean` | what the build holds on disk, and reclaiming it |
| `capture_scene.py stream` | render with **zero** frames written to disk |
| `scene_runtime.js` | author scenes so the D1–D5 defect classes are impossible, not merely detected |

---

## Verifying this is still true

```bash
python3 tests/audit_addendum.py     # asserts the host mechanisms are named in the brain
python3 tests/run_tests.py          # fails if R.3c or its rules regress
```
