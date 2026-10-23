// SRC: tests/v2/fixtures/scenes/_util.js — builds deterministic synthetic narration timings for fixtures
'use strict';
/*
 * Fixture narration helper. Real builds get line and word timings from the voice engine
 * (scripts/render/align.py writes window.__C8_WORDS__); fixtures need the SAME shapes with
 * deterministic numbers, so this lays each line out at a fixed speaking rate and spreads its
 * words evenly inside it. Pure arithmetic — no clock, no randomness.
 */
const path = require('path');

const ROOT = path.join(__dirname, '..', '..', '..', '..');
const RUNTIME = path.join(ROOT, 'scripts', 'render', 'scene_runtime.js');
const COMPONENTS = path.join(ROOT, 'scripts', 'render', 'scene_components.js');

function build(lines, opts) {
  const o = opts || {};
  const perWord = o.perWord || 0.34, gap = o.gap || 0.3, pad = 0.2;
  let t = 0;
  const script = [], wl = [];
  for (const ln of lines) {
    const toks = ln.text.split(/\s+/).filter(Boolean);
    const dur = Math.max(1.6, pad * 2 + toks.length * perWord);
    const start = Math.round(t * 1000) / 1000, end = Math.round((t + dur) * 1000) / 1000;
    const words = toks.map((w, i) => {
      const ws = start + pad + i * perWord;
      return { w, start: Math.round(ws * 1000) / 1000, end: Math.round((ws + perWord * 0.85) * 1000) / 1000, i };
    });
    script.push({ name: ln.name, text: ln.text, start, end, subjectId: ln.subjectId || null });
    wl.push({ name: ln.name, text: ln.text, start, end, words });
    t += dur + gap;
  }
  return { script, words: { source: 'engine', lines: wl } };
}

module.exports = { build, RUNTIME, COMPONENTS, ROOT };
