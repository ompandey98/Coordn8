# The Teaching Principles Behind Coordn8

Coordn8's deliverables aren't documentation dumps — they're *teaching artifacts*.
Both the interactive page and the video are governed by the same pedagogy the
best short-form teaching channels (the TED-Ed / Kurzgesagt school) use. These are
enforced rules in the brain, not vibes.

## Step 1 — identify the learner before you teach

The single biggest lever on whether an explanation lands is **fit to audience**.
Coordn8 determines, before designing anything:

- **Role** — new engineer, senior/SME, leadership, external/OSS, auditor, mixed.
- **Prior knowledge** — do they know the domain? the stack? nothing?
- **Goal** — onboard and contribute, evaluate/adopt, approve/fund, debug, teach onward.
- **Setting** — self-serve reading, a live talk, a README companion, a wiki.

That decision changes depth, entry point, section order, tone, and video pace —
see the audience table in [`engine/PLAYBOOK.md` §0.3](../engine/PLAYBOOK.md). A leadership deck
opens on impact and keeps the machinery shallow; an SME page jumps straight to
the decision surface; a newcomer gets the scaffolded full walk.

## Step 2 — the ten principles

1. **Curiosity gap first.** Open with a concrete problem or surprising question
   the reader *feels*, before any machinery. Care precedes gears.
2. **One idea per beat.** Each section and each scene advances exactly one idea.
   If a sentence doesn't serve its beat's idea, cut it.
3. **Concrete before abstract.** Show a specific instance, then generalize. The
   worked example *is* the lesson.
4. **Progressive disclosure.** Reveal complexity in layers. Build the mental
   model before showing the full decision tree.
5. **Explicit bridges.** Between beats, say *why* we're moving on: "now that we
   know X, here's how Y works."
6. **Narrative spine.** Problem → idea → machinery → it working on a real case →
   payoff. Even a reference doc rides this arc.
7. **Signal over noise.** Every visual encodes information (data moving, a case
   progressing, a fork taken). No decoration for its own sake.
8. **Retrieval & closure.** Recap by closing the loop back to the opening
   problem, leaving a compressed model the reader can restate.
9. **Respect the intelligent novice.** Never condescend, never overwhelm.
10. **Show the seams honestly.** Say where certainty comes from — deterministic
    code vs. model judgment vs. raw field vs. vision.

## Step 3 — register: when to be formal vs. informal

- **Default:** warm but precise. Never stiff, never sloppy.
- **More formal** for: senior/leadership/audit audiences; correctness-critical
  rules; stating guarantees or limitations.
- **More informal** for: the hook, building intuition, encouraging a newcomer,
  narrating a worked example.
- **Never** use insider jargon as a label; introduce every domain term in plain
  words once, then use it consistently from the glossary.

## How this shows up in the deliverables

- **Webpage:** the tab order follows the audience's entry point; each flow uses
  progressive disclosure (mental model → branches); the "walk an example"
  stepper is principle 3 made interactive; the source-of-truth color legend is
  principle 10 made visual.
- **Video:** the six-beat arc (hook → what → why → machinery → example → payoff)
  is principles 1, 3, 5, 6, and 8 in sequence; pacing follows the audience table;
  every visual entering exactly on its sentence is principle 7 plus the sync
  discipline.

The Creative Freedom Clause lets Coordn8 deviate from any structural default when
a different form teaches *this* project better — but it must be able to name which
principle the deviation serves.
