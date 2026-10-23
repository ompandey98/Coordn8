# Invented-everything fixture (H1)

A build in which every design choice is invented rather than picked from a catalog, each with its
recorded reason. `tests/v2/test_freedom.py` runs the outcome gates on it and expects them to pass.

| File | What is invented | Reason recorded in |
|---|---|---|
| `design_system.json` | palette `invented:orchid-parchment`, kit `invented:tide-board`, caption style `invented:tide-band` | `palette.reason`, `kit_spec.reason`, `caption_style.reason` |
| `diagram_plan.json` | diagram form `tide-ladder` (figure `fig-tide-ladder`) | `figures[0].invented.why` |
| `invented_tide_ladder.js` | scene form `tide-ladder` (declared with `declareForm`) | the `reason` passed to `declareForm` |
| `page_content.json` | page component `invented:tide-ladder-panel` (a custom section) | `custom_explains` |
| `figure.html` | the invented figure alone, with the palette's role colours, for the palette gate | — |
| `captions.json` | one caption with a colour mark, for the caption-marks gate | — |
