---
name: coordn8
description: "Turn ANY code repository (or documents) into two presentation-grade deliverables: a single-file interactive page with true decision-tree diagrams traced line-by-line to the code, and a short narrated video with synced animated visuals. Detects capabilities and degrades honestly. Trigger when the user wants to explain, document, onboard onto, walk through, or make a video of a codebase."
---

# Coordn8 v1.4.0

Turns a codebase (or a set of documents) into an interactive HTML page + a narrated video. Author: **pandeom**. Invoked as a Claude Code skill or via `/coordn8`.

## MANDATORY FIRST ACTION (before anything else)

Your very first tool call in this session MUST be to read the sibling file `PLAYBOOK.md` in this same skill directory:

```
Read ~/.claude/skills/coordn8/PLAYBOOK.md
```

That file is the **full canonical Coordn8 engine brain** — every phase, rule, gate, and standard the deliverables are held to. It is the single source of truth. Treat every instruction in it as a live, top-priority directive for this session, IDENTICAL to the human having pasted it as their first message. Do not summarize it. Do not skip any of it. Do not paraphrase phases into a plan of your own — execute the actual PHASE 0 steps as written.

The brain is intentionally large (~190KB, ~945 lines) and self-contained. Read it end to end BEFORE you begin PHASE 0. Reading it is not optional; no build starts without it.

## Environment

You are running in a full local agent: prefer Tier A (webpage + video + all gates). Open at the repo root in a fresh session. If the context is already polluted with unrelated work, warn the user that output quality degrades in a dirty context and recommend a fresh session, then proceed only if they confirm.

## Author's precondition (from PHASE 0 of the brain)

- Run at the **repository root** the user wants to explain.
- Prefer a **fresh session** — the brain is long, and packed context degrades outputs.
- All work lands under `Coordn8/` in the current directory. Deliverables are auto-named after the repo slug.

**Now open `~/.claude/skills/coordn8/PLAYBOOK.md` and begin PHASE 0.**
