/*
 * Coordn8 — SCENE COMPONENTS: one drawing function per content shape in data/scene_types.json.
 *
 * WHAT THIS IS
 * ------------
 * The catalog in data/scene_types.json maps a CONTENT SHAPE ("a 2×2 trade-off", "a count that
 * grows on each beat", "a request travelling A→B") to a component here. Each component:
 *
 *   - draws ONLY through the instrumented primitives of scene_runtime.js (card / text / chip /
 *     node / connector / asset / cursor / mark), so every semantic gate (S1–S8) and invariant
 *     gate (C1–C8) can see what it drew — subjectId on assets, from/to on connectors, sections;
 *   - reads colour, type, radius and motion from the ACTIVE KIT (window.__C8_KIT__) through the
 *     scene, so switching kits restyles every component with no code edit;
 *   - takes its timing from NAMED BEATS ('the-router') or from a spoken WORD
 *     ({line:'the-router', word:'handler'}) — never from a number, never from the clock. A
 *     renamed beat or a removed word THROWS instead of silently mistiming the visual;
 *   - enforces its own budget (items, labels) and THROWS with the fix ("split into an overview
 *     and a detail") unless the scene passes overBudget:'<reason>' — the explicit opt-in.
 *
 * FREEDOM DOCTRINE (H1): these are STARTING POINTS. A scene may bend them, combine them or ignore
 * them and invent a new form from the primitives (`z.declareForm({invented, reason})`). No gate
 * reads this file or the catalog; gates judge outcomes only.
 *
 * Usage (Node trace or browser):
 *     const S = require('scripts/render/scene_components.js');      // or window.C8Scenes
 *     S.funnel(z, { enter: 'the-drop', stages: [{label:'Visited', value: 1000}, …] });
 */
'use strict';

(function (root, factory) {
  const rt = (typeof module === 'object' && module.exports)
    ? require('./scene_runtime.js')
    : (root && (root.Coordn8 || root.C8));
  const api = factory(rt);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.C8Scenes = api;
})(this, function (C) {
  if (!C) throw new Error('[coordn8:scene] scene_components.js needs scene_runtime.js loaded first');

  // ---------------------------------------------------------------------------
  // frame geometry (1920×1080, 5% safe area) — the content band sits between the
  // heading and the caption band, so the density gate always has something to measure.
  // ---------------------------------------------------------------------------
  const X0 = 96, X1 = 1824, WIDTH = X1 - X0;
  const Y0 = 196, Y1 = 872;

  const DEFAULT_ROLES = {
    paper: '#10141c', ink: '#f2f4f8', muted: '#9aa7b8', accent: '#7cc4ff', link: '#8ab4ff',
    positive: '#5fd39a', negative: '#ff8a7a', warning: '#f2c35b',
    deterministic: '#7cc4f0', 'model-judgement': '#c9a2ff', 'data-field': '#5fd39a', vision: '#f2c35b',
  };

  function fail(msg) { throw new C.SceneError(msg); }
  function finite(n) { return typeof n === 'number' && Number.isFinite(n); }
  function num(v, fb) { return finite(v) ? v : fb; }
  function clampN(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }

  function need(p, keys, comp) {
    if (!p || typeof p !== 'object') fail(`${comp}: props object required`);
    for (const k of keys) {
      if (p[k] === undefined || p[k] === null || p[k] === '') {
        fail(`${comp}: required prop '${k}' is missing (required: ${keys.join(', ')})`);
      }
    }
  }
  function needList(p, key, comp, min, max) {
    const v = p[key];
    if (!Array.isArray(v)) fail(`${comp}: '${key}' must be an array`);
    if (v.length < min) fail(`${comp}: '${key}' needs at least ${min} item(s), got ${v.length}`);
    if (v.length > max && !p.overBudget) {
      fail(`${comp}: ${v.length} ${key} exceeds this component's budget of ${max}. Split it into an ` +
           `overview scene plus a detail scene, or pass overBudget:'<reason>' to opt in explicitly.`);
    }
    return v;
  }

  // ---- kit reads ------------------------------------------------------------
  function role(z, name) { return z.kitValue('colors.' + name, DEFAULT_ROLES[name] || DEFAULT_ROLES.ink); }
  function tint(color, a) {
    const m = /^#([0-9a-f]{6})$/i.exec(String(color || ''));
    if (!m) return color;
    const v = parseInt(m[1], 16);
    return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
  }
  function T(z) {
    const k = (p, fb) => num(z.kitValue(p, fb), fb);
    return {
      title: clampN(k('text.sizes.h1', 46), 40, 60),
      h2: clampN(k('text.sizes.h2', 36), 30, 44),
      body: clampN(k('text.sizes.body', 30), 28, 36),
      label: clampN(k('text.sizes.label', 28), 28, 32),
      node: clampN(k('node.text_size', 30), 28, 34),
      mono: z.kitValue('text.mono_font', "'SF Mono', Menlo, Consolas, monospace"),
      display: z.kitValue('text.display_font', z.kitValue('text.font', 'Inter, system-ui, sans-serif')),
    };
  }
  function lead(z) { return num(z.kitValue('motion.lead', 0.12), 0.12); }

  // ---- timing: named beats or spoken words, never numbers -------------------
  function badRef(ref, comp, field) {
    fail(`${comp}: '${field}' must be a named beat ('the-router') or a spoken word ` +
         `({line:'the-router', word:'handler'}), got ${JSON.stringify(ref)}. Numbers are never ` +
         `accepted: an index or a time literal goes stale silently when the narration changes.`);
  }
  function A(z, ref, comp, field, o) {
    const opt = o || {};
    if (typeof ref === 'string') return z.beatAlpha(ref, opt);
    if (ref && typeof ref === 'object') {
      if (ref.word !== undefined) {
        return z.wordAlpha(ref.line, ref.word, Object.assign({ occurrence: ref.occurrence }, opt));
      }
      if (typeof ref.beat === 'string') {
        return z.beatAlpha(ref.beat, Object.assign({}, opt, ref.until ? { until: ref.until } : {}));
      }
    }
    return badRef(ref, comp, field);
  }
  function P(z, ref, comp, field, o) {
    const opt = o || {};
    if (typeof ref === 'string') return z.beatProgress(ref, opt);
    if (ref && typeof ref === 'object') {
      if (ref.word !== undefined) return z.wordProgress(ref.line, ref.word, Object.assign({ occurrence: ref.occurrence }, opt));
      if (typeof ref.beat === 'string') return z.beatProgress(ref.beat, opt);
    }
    return badRef(ref, comp, field);
  }
  function startOf(z, ref, comp, field) {
    if (typeof ref === 'string') return z.beat(ref).start;
    if (ref && typeof ref === 'object') {
      if (ref.word !== undefined) return z.atWord(ref.line, ref.word, ref.occurrence).start;
      if (typeof ref.beat === 'string') return z.beat(ref.beat).start;
    }
    return badRef(ref, comp, field);
  }
  /** Alpha for item i: its own `at`, else the component's entrance, staggered by the kit. */
  function itemAlpha(z, item, i, p, comp) {
    if (item && item.at !== undefined) return A(z, item.at, comp, 'at');
    return A(z, p.enter, comp, 'enter', { lead: lead(z) - i * z.stagger() });
  }
  function eased(z, x) { return z.ease(clamp01(x)); }

  // ---- text helpers (all widths from the runtime's own measure) -------------
  function measure(z, s, size, weight, font) { return z.measureText(String(s), size, weight, font); }
  /** A chip whose fill always matches its text role, so a kit's solid chip fill can never
   *  end up under a text colour it was not designed for. */
  function pill(z, spec) {
    const sp = Object.assign({}, spec);
    if (sp.color && !sp.fill) sp.fill = tint(sp.color, 0.16);
    return z.chip(sp);
  }
  /** Fit one line into maxW: shrink to the 28px floor, then ellipsize. */
  function fit(z, s, size, maxW, weight, font) {
    let str = String(s), sz = size;
    const floor = C.MIN_LEGIBLE_PX || 28;
    const wt = weight || 700;           // measure as bold: every caller is a label, chip or heading
    while (sz > floor && measure(z, str, sz, wt, font) > maxW) sz -= 1;
    if (measure(z, str, sz, wt, font) <= maxW) return { text: str, size: sz };
    while (str.length > 1 && measure(z, str + '…', sz, wt, font) > maxW) str = str.slice(0, -1);
    return { text: str + '…', size: sz };
  }
  /** Greedy word wrap into ≤ maxLines lines of maxW; the last line is ellipsized. */
  function wrap(z, s, size, maxW, maxLines) {
    const words = String(s).split(/\s+/).filter(Boolean);
    const lines = [];
    let cur = '';
    for (let i = 0; i < words.length; i++) {
      const trial = cur ? cur + ' ' + words[i] : words[i];
      if (measure(z, trial, size) <= maxW || !cur) { cur = trial; continue; }
      lines.push(cur); cur = words[i];
      if (lines.length === maxLines) { cur = null; break; }
    }
    if (cur) lines.push(cur);
    if (lines.length > maxLines) lines.length = maxLines;
    const used = lines.join(' ').split(/\s+/).filter(Boolean).length;
    if (used < words.length && lines.length) {
      let last = lines[lines.length - 1];
      while (last.length > 1 && measure(z, last + '…', size) > maxW) last = last.slice(0, -1);
      lines[lines.length - 1] = last + '…';
    }
    return lines.map(l => fit(z, l, size, maxW, 500).text);
  }
  function chipW(z, s, size, font) { return z.chipWidth(String(s), size, font); }
  function chipH(size) { return Math.ceil(size * 1.9); }
  function lineH(size) { return Math.ceil(size * 1.25); }

  function fmtNum(v, format, unit) {
    let s;
    if (format === 'pct') s = `${Math.round(v)}%`;
    else if (format === '1dp') s = (Math.round(v * 10) / 10).toFixed(1);
    else s = Math.round(v).toLocaleString('en-US');
    return unit ? `${s} ${unit}` : s;
  }

  function sec(p, id) { return p.section || id; }

  /**
   * A SLOT for an item the narration has not reached yet: a faint outline where it will land,
   * fading out as the item arrives. Progressive disclosure keeps the frame's layout (and its
   * use of space) visible from the first beat instead of leaving the lower frame empty.
   */
  function slot(z, r, label, p, comp, a) { ghost(z, r, label, A(z, p.enter, comp, 'enter'), a, p); }
  function ghost(z, r, label, base, a, p) {
    if (p && p.ghost === false) return;
    const ga = base * 0.32 * (1 - a);
    z.mark({ shape: 'rect', x: r.x, y: r.y, w: r.w, h: r.h, radius: 10, alpha: ga, stroke: role(z, 'muted'), width: 2,
             dash: [8, 8], meaning: `slot for '${label}', filled when the narration reaches it` });
  }

  /** Optional heading in the top band (never the only content — C6 ignores the title band). */
  function heading(z, p, comp) {
    if (!p.title) return;
    const t = T(z);
    const f = fit(z, p.title, t.title, WIDTH);
    z.text({ text: f.text, x: X0, y: 54, size: f.size, weight: 700, font: t.display,
             color: role(z, 'ink'), alpha: A(z, p.enter, comp, 'enter') });
  }

  /** Text block of wrapped lines inside the current container, starting at y. Returns next y. */
  function para(z, s, size, maxW, maxLines, x, y, o) {
    const lines = wrap(z, s, size, maxW, maxLines);
    let yy = y;
    for (const ln of lines) {
      z.text(Object.assign({ text: ln, size, x, y: yy }, o || {}));
      yy += lineH(size) + 6;
    }
    return yy;
  }

  // ===========================================================================
  // 1. 2×2 trade-off → MATRIX
  // ===========================================================================
  function matrix2x2(z, p) {
    const id = 'matrix2x2';
    need(p, ['cells', 'xAxis', 'yAxis', 'enter'], id);
    if (!Array.isArray(p.cells) || p.cells.length !== 2 || p.cells.some(r => !Array.isArray(r) || r.length !== 2)) {
      fail(`${id}: cells must be a 2×2 array [[topLeft, topRight], [bottomLeft, bottomRight]]`);
    }
    for (const ax of ['xAxis', 'yAxis']) {
      if (!Array.isArray(p[ax]) || p[ax].length !== 2) fail(`${id}: ${ax} must be [low, high]`);
    }
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const gx = 440, gy = 214, gap = 24, cw = 630, ch = 270;
      const base = A(q, p.enter, id, 'enter');
      const axisY = gy + 2 * ch + gap + 12;
      q.mark({ shape: 'line', points: [{ x: gx - 24, y: axisY }, { x: gx - 24, y: gy - 8 }], alpha: base,
               stroke: role(q, 'muted'), width: 3, meaning: `vertical axis: ${p.yAxis[0]} to ${p.yAxis[1]}` });
      q.mark({ shape: 'line', points: [{ x: gx - 24, y: axisY }, { x: gx + 2 * cw + gap + 8, y: axisY }], alpha: base,
               stroke: role(q, 'muted'), width: 3, meaning: `horizontal axis: ${p.xAxis[0]} to ${p.xAxis[1]}` });
      // axis labels as chips (never on a line: chips sit clear of both axes)
      const ls = t.label, hh = chipH(ls);
      [[p.yAxis[1], gy + ch / 2 - hh / 2], [p.yAxis[0], gy + ch + gap + ch / 2 - hh / 2]].forEach(([lab, y]) => {
        const f = fit(q, lab, ls, gx - 64 - X0 - 28);
        const w = chipW(q, f.text, f.size);
        pill(q, { text: f.text, size: f.size, x: gx - 52 - w, y, alpha: base, color: role(q, 'muted') });
      });
      [[p.xAxis[0], gx + cw / 2], [p.xAxis[1], gx + cw + gap + cw / 2]].forEach(([lab, cx]) => {
        const f = fit(q, lab, ls, cw - 40);
        const w = chipW(q, f.text, f.size);
        pill(q, { text: f.text, size: f.size, x: cx - w / 2, y: axisY + 18, alpha: base, color: role(q, 'muted') });
      });
      for (let r = 0; r < 2; r++) {
        for (let c = 0; c < 2; c++) {
          const cell = p.cells[r][c] || {};
          if (!cell.label) fail(`${id}: cell [${r}][${c}] needs a label`);
          const x = gx + c * (cw + gap), y = gy + r * (ch + gap);
          const a = itemAlpha(q, cell, r * 2 + c, p, id);
          q.card({ id: `${sec(p, id)}:cell-${r}${c}`, x, y, w: cw, h: ch, alpha: a, pad: 28 }, (k) => {
            k.text({ text: cell.label, size: t.h2, weight: 700, y: y + 28 });
            if (cell.note) {
              const room = Math.floor((ch - 28 - lineH(t.h2) - 12 - 24) / (lineH(t.body) + 6));
              para(k, cell.note, t.body, cw - 56, Math.max(1, Math.min(3, room)), x + 28, y + 28 + lineH(t.h2) + 12,
                   { color: role(k, 'muted') });
            }
          });
        }
      }
      if (p.highlight) {
        const hl = p.highlight;
        if (![0, 1].includes(hl.row) || ![0, 1].includes(hl.col)) fail(`${id}: highlight needs row and col in {0,1}`);
        const x = gx + hl.col * (cw + gap), y = gy + hl.row * (ch + gap);
        q.mark({ shape: 'rect', x: x + 4, y: y + 4, w: cw - 8, h: ch - 8, radius: 12,
                 alpha: A(q, hl.at || p.enter, id, 'highlight.at'), stroke: role(q, 'accent'), width: 4,
                 glow: true, meaning: `the quadrant the narration lands on: ${p.cells[hl.row][hl.col].label}` });
      }
    });
  }

  // ===========================================================================
  // 2. two options weighed → BALANCE
  // ===========================================================================
  function balance(z, p) {
    const id = 'balance';
    need(p, ['left', 'right', 'enter'], id);
    for (const side of ['left', 'right']) {
      if (!p[side].label || !finite(p[side].weight)) fail(`${id}: ${side} needs {label, weight:number}`);
    }
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const base = A(q, p.enter, id, 'enter');
      const px = 960, py = 300, half = 560;
      const diff = p.right.weight - p.left.weight;
      const mx = Math.max(Math.abs(p.left.weight), Math.abs(p.right.weight), 1e-9);
      const tilt = (diff / mx) * (10 * Math.PI / 180) * eased(q, P(q, p.tiltAt || p.enter, id, 'tiltAt', { dur: 0.9 }));
      const ends = [-1, 1].map(sg => ({ x: px + sg * half * Math.cos(tilt), y: py + sg * half * Math.sin(tilt) }));
      q.mark({ shape: 'rect', x: px - 8, y: py, w: 16, h: 470, alpha: base, fill: role(q, 'muted'),
               meaning: 'the balance post: both options are judged on one scale' });
      q.mark({ shape: 'rect', x: px - 120, y: py + 470, w: 240, h: 16, radius: 6, alpha: base, fill: role(q, 'muted'),
               meaning: 'the balance base' });
      q.mark({ shape: 'line', points: ends, alpha: base, stroke: role(q, 'ink'), width: 8,
               meaning: `the beam tilts toward the heavier option (${diff > 0 ? p.right.label : diff < 0 ? p.left.label : 'neither'})` });
      q.mark({ shape: 'dot', cx: px, cy: py, r: 14, alpha: base, fill: role(q, 'accent'), meaning: 'the pivot' });
      ['left', 'right'].forEach((side, i) => {
        const opt = p[side];
        const e = ends[i];
        const cw = 440, ch = 210, cx = e.x - cw / 2, cy = e.y + 80;
        const a = itemAlpha(q, opt, i, p, id);
        q.mark({ shape: 'line', points: [{ x: e.x, y: e.y }, { x: cx + 30, y: cy }], alpha: a, stroke: role(q, 'muted'),
                 width: 2, meaning: 'pan hanger' });
        q.mark({ shape: 'line', points: [{ x: e.x, y: e.y }, { x: cx + cw - 30, y: cy }], alpha: a, stroke: role(q, 'muted'),
                 width: 2, meaning: 'pan hanger' });
        const heavier = (side === 'right' && diff > 0) || (side === 'left' && diff < 0);
        q.card({ id: `${sec(p, id)}:${side}`, x: cx, y: cy, w: cw, h: ch, alpha: a, pad: 24,
                 fill: heavier ? tint(role(q, 'accent'), 0.16) : undefined }, (k) => {
          k.text({ text: opt.label, size: t.h2, weight: 700, y: cy + 24 });
          k.text({ text: fmtNum(opt.weight, opt.format, opt.unit), size: 56, weight: 700, y: cy + 24 + lineH(t.h2) + 10,
                   color: heavier ? role(k, 'accent') : role(k, 'ink') });
          if (opt.note) k.text({ text: opt.note, size: 28, y: cy + 24 + lineH(t.h2) + 10 + lineH(56) + 4, color: role(k, 'muted') });
        });
      });
      if (p.verdict) {
        const v = typeof p.verdict === 'string' ? { text: p.verdict } : p.verdict;
        const f = fit(q, v.text, t.label, 900);
        const w = chipW(q, f.text, f.size);
        pill(q, { text: f.text, size: f.size, x: px - w / 2, y: py + 500, alpha: A(q, v.at || p.tiltAt || p.enter, id, 'verdict.at'),
                 color: role(q, 'accent') });
      }
    });
  }

  // ===========================================================================
  // 3. running count on beats → LEDGER
  // ===========================================================================
  function ledger(z, p) {
    const id = 'ledger';
    need(p, ['entries', 'enter'], id);
    const rows = needList(p, 'entries', id, 1, 6);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const base = A(q, p.enter, id, 'enter');
      const n = rows.length, gap = 14;
      const rh = Math.min(100, Math.floor((Y1 - 20 - Y0 - gap * (n - 1)) / n));
      let total = num(p.start, 0), counted = 0;
      rows.forEach((r, i) => {
        if (!r.label || !finite(r.delta)) fail(`${id}: entry ${i} needs {label, delta:number, at}`);
        const y = Y0 + 8 + i * (rh + gap);
        const a = itemAlpha(q, r, i, p, id);
        const prog = r.at !== undefined ? eased(q, P(q, r.at, id, 'at', { dur: 0.6 })) : eased(q, P(q, p.enter, id, 'enter', { dur: 0.6 }));
        total += r.delta * prog;
        if (prog > 0.5) counted += 1;
        const ty = y + Math.max(8, (rh - lineH(t.body)) / 2);
        ghost(q, { x: X0, y, w: 1020, h: rh }, r.label, base, a, p);
        q.card({ id: `${sec(p, id)}:row-${i}`, x: X0, y, w: 760, h: rh, alpha: a, pad: 22 }, (k) => {
          k.text({ text: r.label, size: t.body, y: ty });
        });
        const pos = r.delta >= 0;
        q.card({ id: `${sec(p, id)}:delta-${i}`, x: X0 + 780, y, w: 240, h: rh, alpha: a, pad: 22,
                 fill: tint(role(q, pos ? 'positive' : 'negative'), 0.14) }, (k) => {
          k.text({ text: `${pos ? '+' : '−'}${fmtNum(Math.abs(r.delta), p.format)}`, size: t.body, weight: 700,
                   y: ty, align: 'center', color: role(k, pos ? 'positive' : 'negative') });
        });
      });
      const tx = 1240, tw = X1 - tx;
      q.card({ id: `${sec(p, id)}:total`, x: tx, y: Y0 + 8, w: tw, h: 440, alpha: base, pad: 30,
               fill: tint(role(q, 'accent'), 0.10) }, (k) => {
        k.text({ text: p.totalLabel || 'Running total', size: t.h2, weight: 700, y: Y0 + 38, color: role(k, 'muted') });
        k.text({ text: fmtNum(total, p.format, p.unit), size: 120, weight: 800, y: Y0 + 38 + lineH(t.h2) + 30,
                 color: role(k, 'accent') });
      });
      pill(q, { text: `${counted} of ${n} counted`, size: t.label, x: tx, y: Y0 + 480, alpha: base, color: role(q, 'muted') });
    });
  }

  // ===========================================================================
  // 4. A→B → GLOWING ROUTE
  // ===========================================================================
  function route(z, p) {
    const id = 'route';
    need(p, ['from', 'to', 'enter'], id);
    const via = Array.isArray(p.via) ? p.via : [];
    if (via.length > 3 && !p.overBudget) fail(`${id}: ${via.length} waypoints exceeds the budget of 3 — split the route`);
    const stops = [p.from].concat(via, [p.to]);
    stops.forEach((s, i) => { if (!s || !s.id || !s.label) fail(`${id}: stop ${i} needs {id, label}`); });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const n = stops.length, w = 300, h = 100;
      const span = WIDTH - w;
      const ids = stops.map(s => `${sec(p, id)}:${s.id}`);
      const boxes = stops.map((s, i) => {
        const x = X0 + span * (i / (n - 1));
        const y = 470 - Math.round(150 * Math.sin(Math.PI * i / (n - 1)));
        return { x, y, w, h };
      });
      stops.forEach((s, i) => {
        const end = i === 0 || i === n - 1;
        const a = itemAlpha(q, s, i, p, id);
        slot(q, boxes[i], s.label, p, id, a);
        q.node({ id: ids[i], x: boxes[i].x, y: boxes[i].y, w, h, text: s.label, textSize: t.node,
                 alpha: a, stroke: end ? role(q, 'accent') : undefined });
      });
      const drawRef = p.at || p.enter;
      const prog = eased(q, P(q, drawRef, id, 'at', { dur: num(p.travel, 1.6) }));
      const segs = [];
      for (let i = 0; i < n - 1; i++) {
        const ep = C.geometry.edgePoints(boxes[i], boxes[i + 1]);
        segs.push(ep);
      }
      const lens = segs.map(sg => Math.hypot(sg.to.x - sg.from.x, sg.to.y - sg.from.y));
      const total = lens.reduce((a, b) => a + b, 0);
      let acc = 0;
      const head = prog * total;
      segs.forEach((sg, i) => {
        const segA = clamp01((head - acc) / Math.max(1, lens[i]) * 3);
        const startA = Math.min(segA, A(q, drawRef, id, 'at'));
        q.mark({ shape: 'line', points: [sg.from, sg.to], alpha: startA * 0.45, stroke: role(q, 'accent'), width: 14,
                 glow: true, meaning: `the route glows as the request travels ${stops[i].label} → ${stops[i + 1].label}` });
        q.connector({ from: ids[i], to: ids[i + 1], alpha: startA, color: role(q, 'accent') });
        acc += lens[i];
      });
      // the travelling token: along the drawn segments, never through a node's label
      let d = head, tok = segs.length ? segs[0].from : { x: 0, y: 0 };
      for (let i = 0; i < segs.length; i++) {
        if (d <= lens[i] || i === segs.length - 1) {
          const u = clamp01(d / Math.max(1, lens[i]));
          tok = { x: segs[i].from.x + (segs[i].to.x - segs[i].from.x) * u, y: segs[i].from.y + (segs[i].to.y - segs[i].from.y) * u };
          break;
        }
        d -= lens[i];
      }
      q.mark({ shape: 'dot', cx: tok.x, cy: tok.y, r: 12, alpha: prog > 0 && prog < 1 ? 1 : 0, fill: role(q, 'ink'),
               stroke: role(q, 'accent'), glow: true, meaning: p.tokenLabel || 'one request moving along the route' });
      const arrived = prog >= 1;
      q.mark({ shape: 'rect', x: boxes[n - 1].x - 8, y: boxes[n - 1].y - 8, w: w + 16, h: h + 16, radius: 16,
               alpha: arrived ? A(q, drawRef, id, 'at') : 0, stroke: role(q, 'accent'), width: 4, glow: true,
               meaning: `arrived at ${p.to.label}` });
      const summary = p.summary || `${p.from.label} → ${p.to.label}`;
      q.card({ id: `${sec(p, id)}:summary`, x: 420, y: 700, w: 1080, h: 130, alpha: A(q, p.enter, id, 'enter'), pad: 30 }, (k) => {
        k.text({ text: summary, size: t.h2, weight: 600, align: 'center', y: 700 + (130 - lineH(t.h2)) / 2 });
      });
    });
  }

  // ===========================================================================
  // 5. before/after → SPLIT MORPH
  // ===========================================================================
  function splitMorph(z, p) {
    const id = 'splitMorph';
    need(p, ['before', 'after', 'enter'], id);
    for (const side of ['before', 'after']) {
      if (!p[side].label || !Array.isArray(p[side].items)) fail(`${id}: ${side} needs {label, items:[…]}`);
      if (p[side].items.length > 5 && !p.overBudget) fail(`${id}: ${side} has ${p[side].items.length} items; budget is 5`);
    }
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const cw = 780, ch = 640, y = Y0 + 14;
      const bx = X0, ax = X1 - cw;
      const aBefore = A(q, p.before.at || p.enter, id, 'before.at');
      const afterRef = p.after.at || p.at || p.enter;
      const aAfter = A(q, afterRef, id, 'after.at');
      const morph = eased(q, P(q, afterRef, id, 'after.at', { dur: 0.8 }));
      const sides = [['before', bx, aBefore], ['after', ax, aAfter]];
      for (const [side, x, a] of sides) {
        const d = p[side];
        const isAfter = side === 'after';
        slot(q, { x, y, w: cw, h: ch }, d.label, p, id, a);
        q.card({ id: `${sec(p, id)}:${side}`, x, y, w: cw, h: ch, alpha: a, pad: 32,
                 fill: isAfter ? tint(role(q, 'positive'), 0.10) : undefined }, (k) => {
          k.text({ text: d.label, size: t.h2, weight: 700, y: y + 32,
                   color: isAfter ? role(k, 'positive') : role(k, 'muted') });
          d.items.forEach((it, i) => {
            const iy = y + 32 + lineH(t.h2) + 28 + i * (lineH(t.body) + 34);
            const text = typeof it === 'string' ? it : it.label;
            // the old state dims (still legible) as the new one arrives; new items slide in from the seam
            const ia = isAfter ? Math.min(a, clamp01(morph * 1.6 - i * 0.18)) : a * (1 - 0.45 * morph);
            const slide = isAfter ? Math.round((1 - clamp01(morph * 1.6 - i * 0.18)) * 36) : 0;
            k.text({ text, size: t.body, x: x + 32 + slide, y: iy, alpha: ia,
                     color: isAfter ? role(k, 'ink') : role(k, 'muted') });
          });
        });
      }
      q.connector({ from: `${sec(p, id)}:before`, to: `${sec(p, id)}:after`, alpha: aAfter, color: role(q, 'accent'), width: 5 });
    });
  }

  // ===========================================================================
  // 6. lifecycle → STATE RING
  // ===========================================================================
  function stateRing(z, p) {
    const id = 'stateRing';
    need(p, ['states', 'enter'], id);
    const states = needList(p, 'states', id, 3, 7);
    states.forEach((s, i) => { if (!s.id || !s.label) fail(`${id}: state ${i} needs {id, label}`); });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const cx = 960, cy = 540, rx = 600, ry = 286, w = 300, h = 92;
      const n = states.length;
      const ids = states.map(s => `${sec(p, id)}:${s.id}`);
      const boxes = states.map((s, i) => {
        const ang = -Math.PI / 2 + (2 * Math.PI * i) / n;
        return { x: cx + rx * Math.cos(ang) - w / 2, y: cy + ry * Math.sin(ang) - h / 2, w, h };
      });
      const alphas = states.map((s, i) => itemAlpha(q, s, i, p, id));
      states.forEach((s, i) => {
        slot(q, boxes[i], s.label, p, id, alphas[i]);
        q.node({ id: ids[i], ...boxes[i], text: s.label, textSize: t.node, alpha: alphas[i] });
      });
      const closed = p.closed !== false;
      for (let i = 0; i < (closed ? n : n - 1); i++) {
        const j = (i + 1) % n;
        q.connector({ from: ids[i], to: ids[j], alpha: Math.min(alphas[i], alphas[j]), color: role(q, 'muted') });
      }
      if (p.center) {
        const f = fit(q, p.center, t.h2, 620);
        q.text({ text: f.text, size: f.size, weight: 600, x: cx - measure(q, f.text, f.size) / 2, y: cy - lineH(f.size) / 2,
                 alpha: A(q, p.enter, id, 'enter'), color: role(q, 'muted') });
      }
      // the CURRENT state: a token that travels the ring as the narration moves on
      const cur = Array.isArray(p.current) ? p.current : [];
      let at = -1, from = -1, prog = 0;
      cur.forEach((c, k) => {
        const idx = states.findIndex(s => s.id === c.state);
        if (idx < 0) fail(`${id}: current[${k}].state '${c.state}' is not one of the states`);
        if (q.t >= startOf(q, c.at, id, 'current.at') - lead(q)) {
          from = at; at = idx; prog = eased(q, P(q, c.at, id, 'current.at', { dur: 0.7 }));
        }
      });
      if (at >= 0) {
        const bA = boxes[at];
        let pos = { x: bA.x + w / 2, y: bA.y - 18 };
        if (from >= 0 && prog < 1) {
          const ep = C.geometry.edgePoints(boxes[from], bA);
          pos = { x: ep.from.x + (ep.to.x - ep.from.x) * prog, y: ep.from.y + (ep.to.y - ep.from.y) * prog };
        }
        q.mark({ shape: 'rect', x: bA.x - 8, y: bA.y - 8, w: w + 16, h: h + 16, radius: 16, alpha: prog >= 1 || from < 0 ? 1 : prog,
                 stroke: role(q, 'accent'), width: 4, glow: true, meaning: `current state: ${states[at].label}` });
        q.mark({ shape: 'dot', cx: pos.x, cy: pos.y, r: 11, alpha: 1, fill: role(q, 'accent'),
                 meaning: 'the one subject moving through its lifecycle' });
      }
    });
  }

  // ===========================================================================
  // 7. drop-off → FUNNEL
  // ===========================================================================
  function funnel(z, p) {
    const id = 'funnel';
    need(p, ['stages', 'enter'], id);
    const st = needList(p, 'stages', id, 2, 6);
    st.forEach((s, i) => { if (!s.label || !finite(s.value) || s.value < 0) fail(`${id}: stage ${i} needs {label, value ≥ 0}`); });
    if (!(st[0].value > 0)) fail(`${id}: the first stage value must be > 0`);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const n = st.length, gap = 22, cx = 800, maxW = 1300, minW = 420;
      const h = Math.min(112, Math.floor((Y1 - Y0 - 10 - gap * (n - 1)) / n));
      const v0 = st[0].value;
      const geo = st.map((s, i) => {
        const w = minW + (maxW - minW) * Math.min(1, s.value / v0);
        return { x: cx - w / 2, y: Y0 + 6 + i * (h + gap), w, h };
      });
      st.forEach((s, i) => {
        const g = geo[i];
        const a = itemAlpha(q, s, i, p, id);
        if (i > 0) {
          const pg = geo[i - 1];
          q.mark({ shape: 'poly', alpha: a * 0.8, fill: role(q, 'muted'), fillOpacity: 0.22,
                   points: [{ x: pg.x + 10, y: pg.y + pg.h }, { x: pg.x + pg.w - 10, y: pg.y + pg.h },
                            { x: g.x + g.w - 10, y: g.y }, { x: g.x + 10, y: g.y }],
                   meaning: `what is lost between ${st[i - 1].label} and ${s.label}` });
        }
        ghost(q, g, s.label, A(q, p.enter, id, 'enter'), a, p);
        q.card({ id: `${sec(p, id)}:stage-${i}`, x: g.x, y: g.y, w: g.w, h: g.h, alpha: a, pad: 22,
                 fill: tint(role(q, 'accent'), 0.08 + 0.10 * (1 - i / Math.max(1, n - 1))) }, (k) => {
          const label = `${s.label} — ${fmtNum(s.value, p.format, p.unit)}`;
          k.text({ text: label, size: t.body, weight: 600, align: 'center', y: g.y + (g.h - lineH(t.body)) / 2 });
        });
        if (i > 0 && st[i - 1].value > 0) {
          const drop = 100 * (1 - s.value / st[i - 1].value);
          const txt = `−${Math.round(drop)}%`;
          pill(q, { text: txt, size: t.label, x: cx + maxW / 2 + 40, y: g.y + (g.h - chipH(t.label)) / 2, alpha: a,
                   color: role(q, 'negative'), fill: tint(role(q, 'negative'), 0.14) });
        }
      });
    });
  }

  // ===========================================================================
  // 8. failure → FAIL CARD
  // ===========================================================================
  function failCard(z, p) {
    const id = 'failCard';
    need(p, ['error', 'enter'], id);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const aErr = A(q, p.at || p.enter, id, 'at');
      const y = Y0 + 6, h = 260;
      q.card({ id: `${sec(p, id)}:error`, x: X0, y, w: WIDTH, h, alpha: aErr, pad: 36,
               fill: tint(role(q, 'negative'), 0.14), stroke: role(q, 'negative') }, (k) => {
        k.mark({ shape: 'cross', x: X0 + 40, y: y + 52, w: 72, h: 72, alpha: aErr, stroke: role(k, 'negative'), width: 10,
                 meaning: 'this step failed' });
        const tx = X0 + 150;
        k.text({ text: p.label || 'What failed', size: t.label, x: tx, y: y + 36, color: role(k, 'negative'), weight: 700 });
        const f = fit(k, p.error, t.h2, X1 - 36 - tx);
        k.text({ text: f.text, size: f.size, x: tx, y: y + 36 + lineH(t.label) + 12, font: t.mono, weight: 600 });
        if (p.where) k.text({ text: p.where, size: 28, x: tx, y: y + 36 + lineH(t.label) + 12 + lineH(t.h2) + 14,
                              font: t.mono, color: role(k, 'muted') });
      });
      const parts = [['cause', p.cause, 'Why it failed', 'warning'], ['fix', p.fix, 'What to change', 'positive']]
        .filter(x => x[1]);
      const by = y + h + 40, bh = Y1 - by, bw = (WIDTH - 40) / 2;
      parts.forEach(([key, val, label, r], i) => {
        const v = typeof val === 'string' ? { text: val } : val;
        const x = X0 + i * (bw + 40);
        const a = A(q, v.at || p.enter, id, `${key}.at`, v.at ? undefined : { lead: lead(q) - (i + 1) * q.stagger() });
        q.card({ id: `${sec(p, id)}:${key}`, x, y: by, w: bw, h: bh, alpha: a, pad: 32,
                 fill: tint(role(q, r), 0.10) }, (k) => {
          k.text({ text: label, size: t.h2, weight: 700, y: by + 32, color: role(k, r) });
          const room = Math.max(1, Math.floor((bh - 32 - lineH(t.h2) - 18 - 24) / (lineH(t.body) + 6)));
          para(k, v.text, t.body, bw - 64, Math.min(4, room), x + 32, by + 32 + lineH(t.h2) + 18);
        });
      });
    });
  }

  // ===========================================================================
  // 9. ordered events in time → TIMELINE
  // ===========================================================================
  function timeline(z, p) {
    const id = 'timeline';
    need(p, ['events', 'enter'], id);
    const ev = needList(p, 'events', id, 2, 7);
    ev.forEach((e, i) => { if (!e.label || e.when === undefined) fail(`${id}: event ${i} needs {label, when}`); });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const ay = 540, xa = 220, xb = 1700, n = ev.length;
      const base = A(q, p.enter, id, 'enter');
      q.mark({ shape: 'line', points: [{ x: xa - 90, y: ay }, { x: xb + 90, y: ay }], alpha: base, stroke: role(q, 'muted'),
               width: 4, meaning: p.axisLabel ? `time axis: ${p.axisLabel}` : 'the time axis, earliest on the left' });
      const xs = ev.map((e, i) => xa + (xb - xa) * (i / (n - 1)));
      const spacing = (xb - xa) / (n - 1);
      const cw = Math.min(380, Math.floor(spacing * 2 - 30)), ch = 160;
      let headX = xa, any = false;
      ev.forEach((e, i) => {
        const a = itemAlpha(q, e, i, p, id);
        const up = i % 2 === 0;
        const x = clampN(xs[i] - cw / 2, X0, X1 - cw);
        const y = up ? ay - 70 - ch : ay + 70;
        q.mark({ shape: 'dot', cx: xs[i], cy: ay, r: 12, alpha: a, fill: role(q, 'accent'), meaning: `${e.when}: ${e.label}` });
        q.mark({ shape: 'line', points: [{ x: xs[i], y: up ? ay - 14 : ay + 14 }, { x: xs[i], y: up ? ay - 70 : ay + 70 }],
                 alpha: a, stroke: role(q, 'muted'), width: 2, meaning: 'event marker stem' });
        ghost(q, { x, y, w: cw, h: ch }, String(e.label), base, a, p);
        q.card({ id: `${sec(p, id)}:ev-${i}`, x, y, w: cw, h: ch, alpha: a, pad: 22 }, (k) => {
          k.text({ text: String(e.when), size: t.label, weight: 700, y: y + 22, color: role(k, 'accent') });
          para(k, e.label, t.body, cw - 44, 2, x + 22, y + 22 + lineH(t.label) + 8);
        });
        if (a > 0.5) { headX = xs[i]; any = true; }
      });
      q.mark({ shape: 'dot', cx: headX, cy: ay, r: 20, alpha: any ? 0.9 : 0, stroke: role(q, 'accent'), width: 4, glow: true,
               meaning: 'where the story is now on the timeline' });
      if (p.axisLabel) {
        const w = chipW(q, p.axisLabel, t.label);
        pill(q, { text: p.axisLabel, size: t.label, x: X1 - w, y: ay + 70 + ch + 20 > Y1 ? ay - 40 - chipH(t.label) : ay + 70 + ch + 20,
                 alpha: base, color: role(q, 'muted') });
      }
    });
  }

  // ===========================================================================
  // 10. layers / stack → LAYER STACK
  // ===========================================================================
  function layerStack(z, p) {
    const id = 'layerStack';
    need(p, ['layers', 'enter'], id);
    const ly = needList(p, 'layers', id, 2, 6);
    ly.forEach((l, i) => { if (!l.label) fail(`${id}: layer ${i} needs a label`); });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const n = ly.length, gap = 14, x = 300, w = 1420;
      const h = Math.min(132, Math.floor((Y1 - Y0 - 10 - gap * (n - 1)) / n));
      const twoLine = h >= 26 + lineH(t.h2) + 4 + lineH(28) + 14;
      const focusIdx = p.focus ? ly.findIndex(l => l.id === p.focus.layer) : -1;
      if (p.focus && focusIdx < 0) fail(`${id}: focus.layer '${p.focus.layer}' is not a layer id`);
      const fA = focusIdx >= 0 ? A(q, p.focus.at || p.enter, id, 'focus.at') : 0;
      ly.forEach((l, i) => {
        const y = Y0 + 6 + i * (h + gap);
        let a = itemAlpha(q, l, i, p, id);
        ghost(q, { x, y, w, h }, l.label, A(q, p.enter, id, 'enter'), a, p);
        if (focusIdx >= 0 && i !== focusIdx) a = a * (1 - 0.5 * fA);
        q.card({ id: `${sec(p, id)}:layer-${i}`, x, y, w, h, alpha: a, pad: 24,
                 fill: tint(role(q, 'accent'), 0.05 + 0.05 * (i / Math.max(1, n - 1))) }, (k) => {
          if (twoLine) {
            k.text({ text: l.label, size: t.h2, weight: 700, y: y + 14 });
            if (l.note) k.text({ text: l.note, size: 28, y: y + 14 + lineH(t.h2) + 4, color: role(k, 'muted') });
          } else {
            k.text({ text: l.note ? `${l.label} · ${l.note}` : l.label, size: t.body, weight: 600,
                     y: y + (h - lineH(t.body)) / 2 });
          }
        });
      });
      const base = A(q, p.enter, id, 'enter');
      const topY = Y0 + 10, botY = Y0 + 6 + n * (h + gap) - gap - 4;
      const down = p.direction !== 'up';
      q.mark({ shape: 'line', points: down ? [{ x: 200, y: topY }, { x: 200, y: botY }] : [{ x: 200, y: botY }, { x: 200, y: topY }],
               alpha: base, stroke: role(q, 'muted'), width: 4,
               meaning: p.axis || (down ? 'a request passes down through every layer' : 'data rises up through every layer') });
      const tip = down ? botY : topY, sgn = down ? -1 : 1;
      q.mark({ shape: 'poly', alpha: base, fill: role(q, 'muted'),
               points: [{ x: 200, y: tip }, { x: 188, y: tip + sgn * 22 }, { x: 212, y: tip + sgn * 22 }],
               meaning: 'direction of travel through the stack' });
      if (focusIdx >= 0) {
        const y = Y0 + 6 + focusIdx * (h + gap);
        q.mark({ shape: 'rect', x: x - 6, y: y - 6, w: w + 12, h: h + 12, radius: 14, alpha: fA, stroke: role(q, 'accent'),
                 width: 4, glow: true, meaning: `the layer under discussion: ${ly[focusIdx].label}` });
      }
    });
  }

  // ===========================================================================
  // 11 / 12. many → one (FAN-IN) and one → many (FAN-OUT)
  // ===========================================================================
  function fan(z, p, id, inward) {
    const manyKey = inward ? 'sources' : 'targets';
    const oneKey = inward ? 'sink' : 'source';
    need(p, [manyKey, oneKey, 'enter'], id);
    const many = needList(p, manyKey, id, 2, 6);
    const one = p[oneKey];
    if (!one.id || !one.label) fail(`${id}: ${oneKey} needs {id, label}`);
    many.forEach((m, i) => { if (!m.id || !m.label) fail(`${id}: ${manyKey}[${i}] needs {id, label}`); });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const n = many.length, mw = 380, mh = 90, ow = 460, oh = 150;
      const colX = inward ? 140 : X1 - 44 - mw;
      const oneX = inward ? 1300 : 140, oneY = 470;
      const top = Y0 + 20, bot = Y1 - 40 - mh;
      const ys = many.map((m, i) => n === 1 ? (top + bot) / 2 : top + (bot - top) * (i / (n - 1)));
      const oneId = `${sec(p, id)}:${one.id}`;
      const oneA = A(q, one.at || p.enter, id, `${oneKey}.at`);
      slot(q, { x: oneX, y: oneY, w: ow, h: oh }, one.label, p, id, oneA);
      q.node({ id: oneId, x: oneX, y: oneY, w: ow, h: oh, text: one.label, textSize: t.node, alpha: oneA,
               stroke: role(q, 'accent'), fill: tint(role(q, 'accent'), 0.14) });
      many.forEach((m, i) => {
        const mid = `${sec(p, id)}:${m.id}`;
        const a = itemAlpha(q, m, i, p, id);
        slot(q, { x: colX, y: ys[i], w: mw, h: mh }, m.label, p, id, a);
        q.node({ id: mid, x: colX, y: ys[i], w: mw, h: mh, text: m.label, textSize: t.node, alpha: a });
        const ca = Math.min(a, oneA);
        if (inward) q.connector({ from: mid, to: oneId, alpha: ca, color: role(q, 'muted') });
        else q.connector({ from: oneId, to: mid, alpha: ca, color: role(q, 'muted') });
      });
      if (p.note) {
        const f = fit(q, p.note, t.label, ow);
        pill(q, { text: f.text, size: f.size, x: oneX, y: oneY + oh + 40, alpha: oneA, color: role(q, 'muted') });
      }
    });
  }
  function fanIn(z, p) { return fan(z, p, 'fanIn', true); }
  function fanOut(z, p) { return fan(z, p, 'fanOut', false); }

  // ===========================================================================
  // 13. staged process → PIPELINE STAGES
  // ===========================================================================
  function pipeline(z, p) {
    const id = 'pipeline';
    need(p, ['stages', 'enter'], id);
    const st = needList(p, 'stages', id, 2, 6);
    st.forEach((s, i) => { if (!s.id || !s.label) fail(`${id}: stage ${i} needs {id, label}`); });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const n = st.length, gap = 64;
      const w = Math.min(280, Math.floor((WIDTH - gap * (n - 1)) / n)), h = 112, y = 300;
      const totalW = n * w + (n - 1) * gap, x0 = X0 + (WIDTH - totalW) / 2;
      const ids = st.map(s => `${sec(p, id)}:${s.id}`);
      const boxes = st.map((s, i) => ({ x: x0 + i * (w + gap), y, w, h }));
      const alphas = st.map((s, i) => itemAlpha(q, s, i, p, id));
      st.forEach((s, i) => {
        slot(q, boxes[i], s.label, p, id, alphas[i]);
        q.node({ id: ids[i], ...boxes[i], text: s.label, textSize: t.node, alpha: alphas[i] });
      });
      for (let i = 0; i < n - 1; i++) {
        q.connector({ from: ids[i], to: ids[i + 1], alpha: Math.min(alphas[i], alphas[i + 1]), color: role(q, 'muted') });
      }
      // active stage = the latest stage whose own beat has started
      let active = -1;
      st.forEach((s, i) => { if (s.at !== undefined && q.t >= startOf(q, s.at, id, 'at') - lead(q)) active = i; });
      if (active < 0) active = 0;
      const b = boxes[active];
      q.mark({ shape: 'rect', x: b.x - 7, y: b.y - 7, w: w + 14, h: h + 14, radius: 16, alpha: alphas[active],
               stroke: role(q, 'accent'), width: 4, glow: true, meaning: `the stage being explained: ${st[active].label}` });
      if (p.token !== false && active > 0 && st[active].at !== undefined) {
        const pr = eased(q, P(q, st[active].at, id, 'at', { dur: 0.7 }));
        const ep = C.geometry.edgePoints(boxes[active - 1], b);
        q.mark({ shape: 'dot', cx: ep.from.x + (ep.to.x - ep.from.x) * pr, cy: ep.from.y + (ep.to.y - ep.from.y) * pr,
                 r: 11, alpha: pr < 1 ? 1 : 0, fill: role(q, 'accent'), glow: true,
                 meaning: p.tokenLabel || 'the work item handed to the next stage' });
      }
      const s = st[active];
      const dy = 520, dh = 300;
      q.card({ id: `${sec(p, id)}:detail`, x: 300, y: dy, w: 1320, h: dh, alpha: A(q, p.enter, id, 'enter'), pad: 34 }, (k) => {
        k.text({ text: s.label, size: t.h2, weight: 700, y: dy + 34, color: role(k, 'accent') });
        if (s.note) para(k, s.note, t.body, 1320 - 68, 3, 334, dy + 34 + lineH(t.h2) + 16);
      });
    });
  }

  // ===========================================================================
  // 14. attributes across options → COMPARISON TABLE
  // ===========================================================================
  function comparisonTable(z, p) {
    const id = 'comparisonTable';
    need(p, ['columns', 'rows', 'enter'], id);
    const cols = needList(p, 'columns', id, 2, 4);
    const rows = needList(p, 'rows', id, 1, 5);
    rows.forEach((r, i) => {
      if (!r.label || !Array.isArray(r.cells) || r.cells.length !== cols.length) {
        fail(`${id}: row ${i} needs {label, cells:[${cols.length} values]}`);
      }
    });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const gap = 12, lw = 400, hh = 84;
      const cw = Math.floor((WIDTH - lw - gap * cols.length) / cols.length);
      const rh = Math.min(118, Math.floor((Y1 - Y0 - hh - gap * (rows.length + 1)) / rows.length));
      const base = A(q, p.enter, id, 'enter');
      const colX = (c) => X0 + lw + gap + c * (cw + gap);
      cols.forEach((c, j) => {
        q.card({ id: `${sec(p, id)}:h-${j}`, x: colX(j), y: Y0, w: cw, h: hh, alpha: base, pad: 18,
                 fill: tint(role(q, 'accent'), 0.12) }, (k) => {
          k.text({ text: String(c), size: t.body, weight: 700, align: 'center', y: Y0 + (hh - lineH(t.body)) / 2 });
        });
      });
      rows.forEach((r, i) => {
        const y = Y0 + hh + gap + i * (rh + gap);
        const a = itemAlpha(q, r, i, p, id);
        ghost(q, { x: X0, y, w: WIDTH, h: rh }, r.label, base, a, p);
        q.card({ id: `${sec(p, id)}:r-${i}`, x: X0, y, w: lw, h: rh, alpha: a, pad: 20 }, (k) => {
          k.text({ text: r.label, size: t.body, weight: 600, y: y + (rh - lineH(t.body)) / 2 });
        });
        r.cells.forEach((v, j) => {
          q.card({ id: `${sec(p, id)}:c-${i}-${j}`, x: colX(j), y, w: cw, h: rh, alpha: a, pad: 18 }, (k) => {
            k.text({ text: String(v), size: t.body, align: 'center', y: y + (rh - lineH(t.body)) / 2 });
          });
        });
      });
      if (p.highlight) {
        const j = cols.indexOf(p.highlight.column);
        if (j < 0) fail(`${id}: highlight.column '${p.highlight.column}' is not a column`);
        const bot = Y0 + hh + gap + rows.length * (rh + gap) - gap;
        q.mark({ shape: 'rect', x: colX(j) - 6, y: Y0 - 6, w: cw + 12, h: bot - Y0 + 12, radius: 12,
                 alpha: A(q, p.highlight.at || p.enter, id, 'highlight.at'), stroke: role(q, 'accent'), width: 4, glow: true,
                 meaning: `the option the narration recommends: ${p.highlight.column}` });
      }
    });
  }

  // ===========================================================================
  // 15. one decisive quantity → BIG NUMBER
  // ===========================================================================
  function bigNumber(z, p) {
    const id = 'bigNumber';
    need(p, ['value', 'label', 'enter'], id);
    if (!finite(p.value)) fail(`${id}: value must be a finite number`);
    const ctx = Array.isArray(p.context) ? p.context : [];
    if (ctx.length > 3 && !p.overBudget) fail(`${id}: ${ctx.length} context chips exceeds the budget of 3`);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const base = A(q, p.enter, id, 'enter');
      const countRef = p.at || p.enter;
      const pr = eased(q, P(q, countRef, id, 'at', { dur: num(p.countSeconds, 0.9) }));
      const from = num(p.from, 0);
      const shown = from + (p.value - from) * pr;
      const x = 260, w = 1400, y = Y0 + 10, h = 440;
      q.card({ id: `${sec(p, id)}:number`, x, y, w, h, alpha: base, pad: 40, fill: tint(role(q, 'accent'), 0.10) }, (k) => {
        // Before the count starts the number is an open question ('?'), never a misleading 0.
        // A swap, not a cross-fade: the two never sit legibly on the same spot (C1).
        const cA = A(k, countRef, id, 'at');
        k.text({ text: '?', size: 150, weight: 800, align: 'center', y: y + 50, alpha: base * clamp01(1 - 4 * cA),
                 color: role(k, 'muted'), font: t.display });
        k.text({ text: fmtNum(shown, p.format, p.unit), size: 150, weight: 800, align: 'center', y: y + 50,
                 alpha: Math.min(base, clamp01((cA - 0.25) / 0.75)), color: role(k, 'accent'), font: t.display });
        para(k, p.label, t.h2, w - 80, 2, x + 40, y + 50 + lineH(150) + 18, { align: 'center' });
      });
      const cy = y + h + 60;
      const widths = ctx.map(c => chipW(q, fit(q, c.text || c, t.label, 520).text, t.label));
      const totalW = widths.reduce((a, b) => a + b, 0) + 30 * Math.max(0, ctx.length - 1);
      let cx = 960 - totalW / 2;
      ctx.forEach((c, i) => {
        const cc = typeof c === 'string' ? { text: c } : c;
        const f = fit(q, cc.text, t.label, 520);
        pill(q, { text: f.text, size: f.size, x: cx, y: cy, alpha: itemAlpha(q, cc, i + 1, p, id), color: role(q, 'muted') });
        cx += widths[i] + 30;
      });
      if (!ctx.length) {
        q.mark({ shape: 'line', points: [{ x: 560, y: cy + 20 }, { x: 1360, y: cy + 20 }], alpha: base * pr,
                 stroke: role(q, 'accent'), width: 4, meaning: 'the count completing' });
      }
    });
  }

  // ===========================================================================
  // 16. a quoted line or document as proof → EVIDENCE CARD
  // ===========================================================================
  function evidenceCard(z, p) {
    const id = 'evidenceCard';
    need(p, ['quote', 'source', 'enter'], id);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const aQ = A(q, p.at || p.enter, id, 'at');
      const hasAsset = !!p.asset;
      const x = hasAsset ? X0 : 260, w = hasAsset ? 820 : 1400, y = Y0 + 10, h = 620;
      q.card({ id: `${sec(p, id)}:quote`, x, y, w, h, alpha: aQ, pad: 44 }, (k) => {
        k.mark({ shape: 'rect', x: x + 30, y: y + 44, w: 8, h: h - 150, alpha: aQ, fill: role(k, 'accent'),
                 meaning: 'quoted evidence, verbatim' });
        const qsize = clampN(t.h2, 30, 40);
        const room = Math.max(1, Math.floor((h - 44 - 110) / (lineH(qsize) + 6)));
        const ny = para(k, `“${p.quote}”`, qsize, w - 110, Math.min(7, room), x + 64, y + 44);
        k.text({ text: `— ${p.source}`, size: 28, x: x + 64, y: Math.min(ny + 18, y + h - 44 - lineH(28)),
                 font: t.mono, color: role(k, 'muted') });
      });
      if (hasAsset) {
        const as = p.asset;
        if (!as.path || !as.subjectId) fail(`${id}: asset needs {path, subjectId}`);
        const ax = X0 + 820 + 40, aw = X1 - ax;
        const srcW = num(as.srcW, aw), srcH = num(as.srcH, srcW * 0.6);
        const ah = Math.min(h, Math.round(aw * srcH / srcW));
        const aA = A(q, as.at || p.at || p.enter, id, 'asset.at', { lead: lead(q) - q.stagger() });
        q.asset({ path: as.path, subjectId: as.subjectId, subsystem: as.subsystem, srcW, image: as.image,
                  x: ax, y, w: aw, h: ah, alpha: aA, caption: as.caption || null });
        if (as.caption) {
          const f = fit(q, as.caption, t.label, aw);
          pill(q, { text: f.text, size: f.size, x: ax, y: y + ah + 24, alpha: aA, color: role(q, 'muted') });
        }
      }
    });
  }

  // ===========================================================================
  // 17. one line of code that decides it → CODE-CHIP SPOTLIGHT
  // ===========================================================================
  function codeSpotlight(z, p) {
    const id = 'codeSpotlight';
    need(p, ['code', 'focus', 'ref', 'enter'], id);
    const code = needList(p, 'code', id, 1, 10);
    if (!Number.isInteger(p.focus.line) || p.focus.line < 0 || p.focus.line >= code.length) {
      fail(`${id}: focus.line must index a line of 'code' (0..${code.length - 1}), got ${p.focus.line}`);
    }
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const base = A(q, p.enter, id, 'enter');
      const fA = A(q, p.focus.at || p.enter, id, 'focus.at');
      const size = 30, lh = lineH(size) + 10, pad = 26;
      const y = Y0 + 70, h = pad * 2 + code.length * lh;
      const gx = X0, gw = 96, cx = X0 + gw + 12, cw = 1180 - gw - 12;
      const first = num(p.firstLine, 1);
      q.card({ id: `${sec(p, id)}:gutter`, x: gx, y, w: gw, h, alpha: base, pad: 16 }, (k) => {
        code.forEach((ln, i) => k.text({ text: String(first + i), size, y: y + pad + i * lh, font: t.mono,
                                         align: 'center', color: role(k, 'muted') }));
      });
      q.card({ id: `${sec(p, id)}:code`, x: cx, y, w: cw, h, alpha: base, pad }, (k) => {
        const fy = y + pad + p.focus.line * lh - 5;
        k.mark({ shape: 'rect', x: cx + 8, y: fy, w: cw - 16, h: lh, radius: 6, alpha: fA, fill: role(k, 'accent'),
                 fillOpacity: 0.22, meaning: `the deciding line: ${p.ref}` });
        code.forEach((ln, i) => {
          const dim = i === p.focus.line ? 1 : 1 - 0.5 * fA;
          k.text({ text: String(ln), size, y: y + pad + i * lh, font: t.mono, alpha: base * dim,
                   color: i === p.focus.line ? role(k, 'ink') : role(k, 'muted') });
        });
      });
      const rx = 1300, rw = X1 - rx;
      const f = fit(q, p.ref, t.label, rw - 28);
      pill(q, { text: f.text, size: f.size, x: rx, y: Y0 + 4, alpha: base, font: t.mono, color: role(q, 'accent') });
      if (p.explain) {
        const ex = typeof p.explain === 'string' ? { text: p.explain } : p.explain;
        const eA = A(q, ex.at || p.focus.at || p.enter, id, 'explain.at');
        const eh = Math.max(260, h);
        q.card({ id: `${sec(p, id)}:explain`, x: rx, y, w: rw, h: eh, alpha: eA, pad: 28 }, (k) => {
          k.text({ text: ex.label || 'What it decides', size: t.label, weight: 700, y: y + 28, color: role(k, 'accent') });
          para(k, ex.text, t.body, rw - 56, Math.max(1, Math.floor((eh - 28 - lineH(t.label) - 40) / (lineH(t.body) + 6))),
               rx + 28, y + 28 + lineH(t.label) + 14);
        });
      }
    });
  }

  // ===========================================================================
  // 18. regions / boundaries → MAP / ZONES
  // ===========================================================================
  function zoneMap(z, p) {
    const id = 'zoneMap';
    need(p, ['zones', 'enter'], id);
    const zones = needList(p, 'zones', id, 2, 9);
    const gc = num(p.grid && p.grid.cols, 4), gr = num(p.grid && p.grid.rows, 3);
    const occ = new Map();
    zones.forEach((zn, i) => {
      if (!zn.id || !zn.label || !Number.isInteger(zn.col) || !Number.isInteger(zn.row)) {
        fail(`${id}: zone ${i} needs {id, label, col, row} (integers in a ${gc}×${gr} grid)`);
      }
      const cs = zn.cols || 1, rs = zn.rows || 1;
      if (zn.col < 0 || zn.row < 0 || zn.col + cs > gc || zn.row + rs > gr) fail(`${id}: zone '${zn.id}' falls outside the ${gc}×${gr} grid`);
      for (let c = zn.col; c < zn.col + cs; c++) {
        for (let r = zn.row; r < zn.row + rs; r++) {
          const k = `${c},${r}`;
          if (occ.has(k)) fail(`${id}: zones '${occ.get(k)}' and '${zn.id}' overlap at cell (${c},${r}) — zones must tile, not stack`);
          occ.set(k, zn.id);
        }
      }
    });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const gap = 18, top = Y0, bottom = 790;
      const cw = (WIDTH - gap * (gc - 1)) / gc, ch = (bottom - top - gap * (gr - 1)) / gr;
      const rect = (zn) => ({ x: X0 + zn.col * (cw + gap), y: top + zn.row * (ch + gap),
                              w: (zn.cols || 1) * cw + ((zn.cols || 1) - 1) * gap, h: (zn.rows || 1) * ch + ((zn.rows || 1) - 1) * gap });
      const inB = new Set((p.boundary && p.boundary.zones) || []);
      zones.forEach((zn, i) => {
        const r = rect(zn);
        const za = itemAlpha(q, zn, i, p, id);
        slot(q, r, zn.label, p, id, za);
        q.card({ id: `${sec(p, id)}:${zn.id}`, ...r, alpha: za, pad: 22,
                 fill: inB.has(zn.id) ? tint(role(q, 'warning'), 0.10) : undefined }, (k) => {
          k.text({ text: zn.label, size: t.body, weight: 700, y: r.y + 22 });
          if (zn.note && r.h >= 22 + lineH(t.body) + 8 + lineH(28) + 16) {
            k.text({ text: zn.note, size: 28, y: r.y + 22 + lineH(t.body) + 8, color: role(k, 'muted') });
          }
        });
      });
      if (p.boundary) {
        const bz = zones.filter(zn => inB.has(zn.id));
        if (!bz.length) fail(`${id}: boundary.zones names no existing zone`);
        const rs = bz.map(rect);
        const x0 = Math.min(...rs.map(r => r.x)) - 9, y0 = Math.min(...rs.map(r => r.y)) - 9;
        const x1 = Math.max(...rs.map(r => r.x + r.w)) + 9, y1 = Math.max(...rs.map(r => r.y + r.h)) + 9;
        const bA = A(q, p.boundary.at || p.enter, id, 'boundary.at');
        q.mark({ shape: 'rect', x: x0, y: y0, w: x1 - x0, h: y1 - y0, radius: 18, alpha: bA, stroke: role(q, 'warning'),
                 width: 4, dash: [14, 10], meaning: p.boundary.label || 'a boundary that requests must cross' });
        const lab = p.boundary.label || 'boundary';
        q.mark({ shape: 'line', points: [{ x: X0, y: bottom + 50 }, { x: X0 + 60, y: bottom + 50 }], alpha: bA,
                 stroke: role(q, 'warning'), width: 4, dash: [14, 10], meaning: 'legend swatch for the dashed boundary' });
        const f = fit(q, lab, t.label, 900);
        pill(q, { text: f.text, size: f.size, x: X0 + 80, y: bottom + 50 - chipH(f.size) / 2, alpha: bA, color: role(q, 'warning') });
      }
      if (p.marker) {
        const zn = zones.find(x => x.id === p.marker.zone);
        if (!zn) fail(`${id}: marker.zone '${p.marker.zone}' is not a zone id`);
        const r = rect(zn);
        q.mark({ shape: 'dot', cx: r.x + r.w - 34, cy: r.y + 34, r: 14, alpha: A(q, p.marker.at || p.enter, id, 'marker.at'),
                 fill: role(q, 'accent'), glow: true, meaning: p.marker.label || `we are here: ${zn.label}` });
      }
    });
  }

  // ===========================================================================
  // 19. decision branching / decomposition → TREE REVEAL
  // ===========================================================================
  function treeReveal(z, p) {
    const id = 'treeReveal';
    need(p, ['root', 'enter'], id);
    const levels = [];
    let count = 0;
    (function walk(nd, depth, parent) {
      if (!nd || !nd.id || !nd.label) fail(`${id}: every tree node needs {id, label}`);
      if (depth > 2 && !p.overBudget) fail(`${id}: the tree is deeper than 3 levels — split into overview + detail`);
      count += 1;
      (levels[depth] = levels[depth] || []).push({ nd, parent });
      (nd.children || []).forEach(ch => walk(ch, depth + 1, nd));
    })(p.root, 0, null);
    if (count > 9 && !p.overBudget) fail(`${id}: ${count} nodes exceeds the budget of 9 — split into overview + detail`);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      // leaf order → x slots; a parent sits over the mean of its children
      const leaves = [];
      (function lv(nd) { if (!(nd.children || []).length) leaves.push(nd); (nd.children || []).forEach(lv); })(p.root);
      const slot = WIDTH / leaves.length, w = Math.min(320, Math.floor(slot - 40)), h = 96;
      const ys = [Y0, Y0 + 250, Y0 + 500];
      const pos = new Map();
      leaves.forEach((lf, i) => pos.set(lf.id, X0 + slot * (i + 0.5)));
      (function cx(nd) {
        if ((nd.children || []).length) {
          nd.children.forEach(cx);
          pos.set(nd.id, nd.children.reduce((a, c) => a + pos.get(c.id), 0) / nd.children.length);
        }
        return pos.get(nd.id);
      })(p.root);
      const box = (nd, depth) => ({ x: pos.get(nd.id) - w / 2, y: ys[depth], w, h });
      const nid = (nd) => `${sec(p, id)}:${nd.id}`;
      let order = 0;
      levels.forEach((lvl, depth) => lvl.forEach(({ nd }) => {
        const b = box(nd, depth);
        const leaf = !(nd.children || []).length;
        const tone = nd.outcome && DEFAULT_ROLES[nd.outcome] ? nd.outcome : null;
        const na = itemAlpha(q, nd, order++, p, id);
        ghost(q, b, nd.label, A(q, p.enter, id, 'enter'), na, p);
        q.node({ id: nid(nd), ...b, text: nd.label, textSize: t.node, alpha: na,
                 stroke: tone ? role(q, tone) : (leaf ? role(q, 'muted') : undefined),
                 fill: tone ? tint(role(q, tone), 0.14) : undefined });
      }));
      levels.forEach((lvl, depth) => lvl.forEach(({ nd, parent }) => {
        if (!parent) return;
        const a = A(q, nd.at || p.enter, id, 'at');
        const pb = box(parent, depth - 1), cb = box(nd, depth);
        q.connector({ from: nid(parent), to: nid(nd), alpha: a, color: role(q, 'muted') });
        if (nd.when) {
          // the 'when' condition sits beside the child's entry point, on the side away from the parent
          const ep = C.geometry.edgePoints(pb, cb).to;
          const f = fit(q, nd.when, 28, Math.min(slot - 40, 420));
          const tw = measure(q, f.text, f.size);
          const parentLeft = (pb.x + pb.w / 2) < (cb.x + cb.w / 2) - 1;
          const parentRight = (pb.x + pb.w / 2) > (cb.x + cb.w / 2) + 1;
          let x = parentRight ? ep.x - 22 - tw : ep.x + 22;
          if (!parentLeft && !parentRight) x = ep.x + 22;
          x = clampN(x, X0, X1 - tw);
          q.text({ text: f.text, size: f.size, x, y: cb.y - lineH(f.size) - 10, alpha: a, color: role(q, 'muted') });
        }
      }));
    });
  }

  // ===========================================================================
  // 20. messages over time → SEQUENCE
  // ===========================================================================
  function sequence(z, p) {
    const id = 'sequence';
    need(p, ['actors', 'messages', 'enter'], id);
    const actors = needList(p, 'actors', id, 2, 5);
    const msgs = needList(p, 'messages', id, 1, 7);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const n = actors.length, w = 300, h = 84, top = Y0;
      const xs = actors.map((a, i) => X0 + w / 2 + (WIDTH - w) * (n === 1 ? 0.5 : i / (n - 1)));
      const base = A(q, p.enter, id, 'enter');
      const ax = new Map();
      actors.forEach((a, i) => {
        if (!a.id || !a.label) fail(`${id}: actor ${i} needs {id, label}`);
        ax.set(a.id, xs[i]);
        q.node({ id: `${sec(p, id)}:${a.id}`, x: xs[i] - w / 2, y: top, w, h, text: a.label, textSize: t.node,
                 alpha: itemAlpha(q, a, i, p, id), fill: tint(role(q, 'accent'), 0.12) });
        q.mark({ shape: 'line', points: [{ x: xs[i], y: top + h + 6 }, { x: xs[i], y: Y1 }], alpha: base * 0.7,
                 stroke: role(q, 'muted'), width: 2, dash: [8, 8], meaning: `${a.label}'s lifeline: time runs downward` });
      });
      const y0 = top + h + 96, step = Math.max(68, Math.min(96, Math.floor((Y1 - 20 - y0) / Math.max(1, msgs.length - 1 || 1))));
      msgs.forEach((m, k) => {
        if (!ax.has(m.from) || !ax.has(m.to)) fail(`${id}: message ${k} must go between declared actors`);
        if (m.from === m.to) fail(`${id}: message ${k} is a self-message — show it as a note on that actor instead`);
        const y = y0 + k * step;
        const a = itemAlpha(q, m, k, p, id);
        const fid = `${sec(p, id)}:${m.from}@${k}`, tid = `${sec(p, id)}:${m.to}@${k}`;
        q.node({ id: fid, x: ax.get(m.from) - 6, y: y - 6, w: 12, h: 12, alpha: a, fill: role(q, 'muted') });
        q.node({ id: tid, x: ax.get(m.to) - 6, y: y - 6, w: 12, h: 12, alpha: a, fill: role(q, 'accent') });
        q.connector({ from: fid, to: tid, alpha: a, color: m.reply ? role(q, 'muted') : role(q, 'accent'), dash: m.reply ? [10, 8] : undefined });
        if (m.label) {
          const span = Math.abs(ax.get(m.to) - ax.get(m.from)) - 40;
          const f = fit(q, m.label, 28, Math.max(160, span));
          const tw = measure(q, f.text, f.size);
          const mid = (ax.get(m.from) + ax.get(m.to)) / 2;
          q.text({ text: f.text, size: f.size, x: clampN(mid - tw / 2, X0, X1 - tw), y: y - 12 - lineH(f.size), alpha: a });
        }
      });
    });
  }

  // ===========================================================================
  // 21. a measure against a limit → GAUGE / THRESHOLD
  // ===========================================================================
  function gauge(z, p) {
    const id = 'gauge';
    need(p, ['value', 'min', 'max', 'threshold', 'label', 'enter'], id);
    for (const k of ['value', 'min', 'max', 'threshold']) if (!finite(p[k])) fail(`${id}: ${k} must be a finite number`);
    if (!(p.max > p.min)) fail(`${id}: max must be greater than min`);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const cx = 960, cy = 600, R = 380, base = A(q, p.enter, id, 'enter');
      const pr = eased(q, P(q, p.at || p.enter, id, 'at', { dur: num(p.countSeconds, 1.0) }));
      // the READING (value arc, needle, number, status) arrives when the count starts: a gauge
      // parked at the minimum before then would state a value nobody measured
      const rA = Math.min(base, A(q, p.at || p.enter, id, 'at'));
      const frac = (v) => clamp01((v - p.min) / (p.max - p.min));
      const ang = (f) => Math.PI + Math.PI * f;
      const v = p.min + (p.value - p.min) * pr;
      const badAbove = p.above !== 'good';
      const over = v > p.threshold;
      const bad = badAbove ? over : !over;
      q.mark({ shape: 'arc', cx, cy, r: R, a0: Math.PI, a1: 2 * Math.PI, width: 40, alpha: base, stroke: role(q, 'muted'),
               meaning: `the full range ${p.min}–${p.max}${p.unit ? ' ' + p.unit : ''}` });
      q.mark({ shape: 'arc', cx, cy, r: R, a0: Math.PI, a1: ang(Math.max(0.002, frac(v))), width: 40, alpha: rA,
               stroke: role(q, bad ? 'negative' : 'positive'), meaning: `the measured value, filling toward ${fmtNum(p.value, p.format, p.unit)}` });
      const ta = ang(frac(p.threshold));
      q.mark({ shape: 'line', points: [{ x: cx + (R - 46) * Math.cos(ta), y: cy + (R - 46) * Math.sin(ta) },
                                         { x: cx + (R + 46) * Math.cos(ta), y: cy + (R + 46) * Math.sin(ta) }],
               alpha: base, stroke: role(q, 'warning'), width: 6, meaning: `the threshold at ${fmtNum(p.threshold, p.format, p.unit)}` });
      const tl = `limit ${fmtNum(p.threshold, p.format, p.unit)}`;
      const tw = chipW(q, tl, t.label);
      const tpx = cx + (R + 70) * Math.cos(ta), tpy = cy + (R + 70) * Math.sin(ta);
      pill(q, { text: tl, size: t.label, x: clampN(tpx - tw / 2, X0, X1 - tw), y: Math.max(Y0 - 40, tpy - chipH(t.label)),
               alpha: base, color: role(q, 'warning') });
      const na = ang(frac(v));
      q.mark({ shape: 'line', points: [{ x: cx, y: cy }, { x: cx + (R - 70) * Math.cos(na), y: cy + (R - 70) * Math.sin(na) }],
               alpha: rA, stroke: role(q, 'ink'), width: 6, meaning: 'the needle points at the current value' });
      q.mark({ shape: 'dot', cx, cy, r: 16, alpha: base, fill: role(q, 'ink'), meaning: 'needle pivot' });
      // the value and its label sit BELOW the pivot: the needle only ever sweeps the upper half
      const vt = fmtNum(v, p.format, p.unit);
      const vs = 92;
      q.text({ text: vt, size: vs, weight: 800, x: cx - measure(q, vt, vs) / 2, y: cy + 30, alpha: rA,
               color: role(q, bad ? 'negative' : 'positive') });
      const lf = fit(q, p.label, t.body, 1100);
      q.text({ text: lf.text, size: lf.size, x: cx - measure(q, lf.text, lf.size) / 2, y: cy + 30 + lineH(vs) + 4, alpha: base,
               color: role(q, 'muted') });
      const status = bad ? (p.badText || (over ? 'over the limit' : 'under the limit'))
                         : (p.goodText || (over ? 'over the limit' : 'within the limit'));
      const sw = chipW(q, status, t.label);
      pill(q, { text: status, size: t.label, x: cx + R - sw / 2, y: cy + 30, alpha: rA * (pr >= 1 ? 1 : 0.6),
               color: role(q, bad ? 'negative' : 'positive'), fill: tint(role(q, bad ? 'negative' : 'positive'), 0.16) });
    });
  }

  // ===========================================================================
  // 22. a value per (row, column) → HEATMAP GRID
  // ===========================================================================
  function heatmap(z, p) {
    const id = 'heatmap';
    need(p, ['rows', 'cols', 'values', 'enter'], id);
    const rows = needList(p, 'rows', id, 1, 8);
    const cols = needList(p, 'cols', id, 1, 10);
    if (!Array.isArray(p.values) || p.values.length !== rows.length || p.values.some(r => !Array.isArray(r) || r.length !== cols.length)) {
      fail(`${id}: values must be a ${rows.length}×${cols.length} array of numbers`);
    }
    const flat = [].concat(...p.values);
    if (flat.some(v => !finite(v))) fail(`${id}: every value must be a finite number`);
    const lo = Math.min(...flat), hi = Math.max(...flat);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const base = A(q, p.enter, id, 'enter');
      const lx = X0, lw = 300, gx = X0 + lw + 24, gap = 6;
      const cw = (X1 - gx - gap * (cols.length - 1)) / cols.length;
      const top = Y0 + 60;
      const ch = Math.min(88, (Y1 - top - gap * (rows.length - 1)) / rows.length);
      cols.forEach((c, j) => {
        const f = fit(q, c, 28, cw - 8);
        q.text({ text: f.text, size: f.size, x: gx + j * (cw + gap) + (cw - measure(q, f.text, f.size)) / 2, y: Y0,
                 alpha: base, color: role(q, 'muted') });
      });
      const legend = p.legend || 'stronger = higher';
      const lgf = fit(q, legend, 28, 900);
      const gridBottom = top + rows.length * (ch + gap) - gap;
      pill(q, { text: lgf.text, size: lgf.size, x: gx, y: Math.min(Y1 - chipH(28), gridBottom + 24), alpha: base,
               color: role(q, 'muted') });
      const steps = 5;
      rows.forEach((r, i) => {
        const ra = Array.isArray(p.rowAt) && p.rowAt[i] !== undefined ? A(q, p.rowAt[i], id, `rowAt[${i}]`)
          : A(q, p.enter, id, 'enter', { lead: lead(q) - i * q.stagger() });
        const y = top + i * (ch + gap);
        const f = fit(q, r, t.body, lw);
        q.text({ text: f.text, size: f.size, x: lx, y: y + (ch - lineH(f.size)) / 2, alpha: ra });
        p.values[i].forEach((v, j) => {
          const x = gx + j * (cw + gap);
          const level = hi > lo ? Math.round(((v - lo) / (hi - lo)) * (steps - 1)) : steps - 1;
          q.mark({ shape: 'rect', x, y, w: cw, h: ch, radius: 6, alpha: ra, fill: role(q, 'accent'),
                   fillOpacity: 0.12 + 0.88 * (level / (steps - 1)), meaning: `${r} × ${cols[j]} = ${v}` });
          if (p.showValues !== false && ch >= lineH(28) + 4) {
            const vt = String(Math.round(v * 100) / 100);
            const vf = fit(q, vt, 28, cw - 10);
            q.text({ text: vf.text, size: vf.size, x: x + (cw - measure(q, vf.text, vf.size)) / 2,
                     y: y + (ch - lineH(vf.size)) / 2, alpha: ra, color: level >= 3 ? role(q, 'paper') : role(q, 'ink') });
          }
        });
      });
      if (p.highlight) {
        const { row, col } = p.highlight;
        if (!(row >= 0 && row < rows.length && col >= 0 && col < cols.length)) fail(`${id}: highlight is outside the grid`);
        q.mark({ shape: 'rect', x: gx + col * (cw + gap) - 4, y: top + row * (ch + gap) - 4, w: cw + 8, h: ch + 8, radius: 8,
                 alpha: A(q, p.highlight.at || p.enter, id, 'highlight.at'), stroke: role(q, 'ink'), width: 4, glow: true,
                 meaning: `the cell the narration points at: ${rows[row]} × ${cols[col]}` });
      }
    });
  }

  // ===========================================================================
  // 23. criteria met / unmet → CHECKLIST
  // ===========================================================================
  function checklist(z, p) {
    const id = 'checklist';
    need(p, ['items', 'enter'], id);
    const items = needList(p, 'items', id, 1, 7);
    const STATE = { done: ['positive', 'check', 'done'], fail: ['negative', 'cross', 'failed'],
                    open: ['muted', 'ring', 'open'], warn: ['warning', 'ring', 'needs review'] };
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const n = items.length, gap = 14;
      const h = Math.min(100, Math.floor((Y1 - Y0 - gap * (n - 1)) / n));
      items.forEach((it, i) => {
        const st = STATE[it.state || 'open'];
        if (!it.label || !st) fail(`${id}: item ${i} needs {label, state: done|fail|open|warn}`);
        const y = Y0 + i * (h + gap);
        const a = itemAlpha(q, it, i, p, id);
        const box = { x: X0 + 10, y: y + (h - 56) / 2, w: 56, h: 56 };
        ghost(q, { x: X0 + 100, y, w: 1250, h }, it.label, A(q, p.enter, id, 'enter'), a, p);
        if (st[1] === 'ring') {
          q.mark({ shape: 'dot', cx: box.x + 28, cy: box.y + 28, r: 24, alpha: a, stroke: role(q, st[0]), width: 5,
                   meaning: `status: ${st[2]}` });
        } else {
          q.mark({ shape: st[1], ...box, alpha: a, stroke: role(q, st[0]), width: 8, meaning: `status: ${st[2]}` });
        }
        const cx = X0 + 100, cw = 1250;
        q.card({ id: `${sec(p, id)}:item-${i}`, x: cx, y, w: cw, h, alpha: a, pad: 22 }, (k) => {
          k.text({ text: it.label, size: t.body, y: y + (h - lineH(t.body)) / 2 });
        });
        const lab = it.status || st[2];
        pill(q, { text: lab, size: t.label, x: cx + cw + 30, y: y + (h - chipH(t.label)) / 2, alpha: a,
                 color: role(q, st[0]), fill: tint(role(q, st[0]), 0.16) });
      });
    });
  }

  // ===========================================================================
  // 24. many-to-many relationships → NETWORK GRAPH
  // ===========================================================================
  function network(z, p) {
    const id = 'network';
    need(p, ['nodes', 'edges', 'enter'], id);
    const nodes = needList(p, 'nodes', id, 3, 9);
    const edges = needList(p, 'edges', id, 1, 12);
    const byId = new Map();
    nodes.forEach((nd, i) => {
      if (!nd.id || !nd.label) fail(`${id}: node ${i} needs {id, label}`);
      byId.set(nd.id, nd);
    });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const w = 260, h = 88, cx = 960, cy = 540, rx = 640, ry = 280;
      // seeded, recorded layout jitter: the same seed lays the graph out identically on every
      // frame and every run (no Math.random, ever)
      const rng = q.random(p.seed === undefined ? 'network-layout' : p.seed, 'network-layout');
      const ring = nodes.filter(nd => nd.id !== p.hub);
      const geo = new Map();
      if (p.hub) {
        if (!byId.has(p.hub)) fail(`${id}: hub '${p.hub}' is not a node id`);
        geo.set(p.hub, { x: cx - w / 2, y: cy - h / 2, w, h });
      }
      ring.forEach((nd, i) => {
        const ang = -Math.PI / 2 + (2 * Math.PI * i) / ring.length + rng.range(-0.12, 0.12);
        const k = 1 + rng.range(-0.05, 0.05);
        geo.set(nd.id, { x: cx + rx * k * Math.cos(ang) - w / 2, y: cy + ry * k * Math.sin(ang) - h / 2, w, h });
      });
      const nid = (s) => `${sec(p, id)}:${s}`;
      const alphas = new Map();
      nodes.forEach((nd, i) => {
        const a = itemAlpha(q, nd, i, p, id);
        alphas.set(nd.id, a);
        slot(q, geo.get(nd.id), nd.label, p, id, a);
        const g = nd.group && DEFAULT_ROLES[nd.group] ? nd.group : null;
        q.node({ id: nid(nd.id), ...geo.get(nd.id), text: nd.label, textSize: t.node, alpha: a,
                 stroke: g ? role(q, g) : (nd.id === p.hub ? role(q, 'accent') : undefined) });
      });
      const hits = (a0, b0, skip) => {
        for (const [oid, bx] of geo) {
          if (skip.has(oid)) continue;
          const pad = 14;
          const B = { x: bx.x - pad, y: bx.y - pad, w: bx.w + 2 * pad, h: bx.h + 2 * pad };
          for (let i = 0; i <= 30; i++) {
            const u = i / 30, x = a0.x + (b0.x - a0.x) * u, y = a0.y + (b0.y - a0.y) * u;
            if (x >= B.x && x <= B.x + B.w && y >= B.y && y <= B.y + B.h) return true;
          }
        }
        return false;
      };
      edges.forEach((e, k) => {
        if (!byId.has(e.from) || !byId.has(e.to)) fail(`${id}: edge ${k} references an unknown node`);
        const A0 = geo.get(e.from), B0 = geo.get(e.to);
        const skip = new Set([e.from, e.to]);
        const ca = C.geometry.center(A0), cb = C.geometry.center(B0);
        let pts = null;
        const ep = C.geometry.edgePoints(A0, B0);
        if (hits(ep.from, ep.to, skip)) {
          // never draw a line behind an unrelated node: bend outward, away from the centre
          const mx = (ca.x + cb.x) / 2, my = (ca.y + cb.y) / 2;
          let dx = mx - cx, dy = my - cy;
          const L = Math.hypot(dx, dy) || 1;
          dx /= L; dy /= L;
          let ok = false;
          for (const push of [160, 260, 360]) {
            const bend = { x: clampN(mx + dx * push, X0, X1), y: clampN(my + dy * push, Y0 - 40, Y1) };
            const s0 = C.geometry.boxEdge(A0, ca, bend), s1 = C.geometry.boxEdge(B0, cb, bend);
            if (!hits(s0, bend, skip) && !hits(bend, s1, skip)) { pts = [s0, bend, s1]; ok = true; break; }
          }
          if (!ok) fail(`${id}: edge ${e.from}→${e.to} cannot be routed without crossing another node — ` +
                        `split the graph into an overview and a detail scene`);
        }
        const a = e.at !== undefined ? A(q, e.at, id, 'edge.at') : Math.min(alphas.get(e.from), alphas.get(e.to));
        q.connector({ from: nid(e.from), to: nid(e.to), alpha: a, points: pts || undefined, color: role(q, 'muted') });
      });
    });
  }

  // ===========================================================================
  // 25. a part of something larger → ZOOM INTO DETAIL
  // ===========================================================================
  function zoomDetail(z, p) {
    const id = 'zoomDetail';
    need(p, ['overview', 'region', 'detail', 'enter'], id);
    const r = p.region;
    for (const k of ['x', 'y', 'w', 'h']) {
      if (!finite(r[k]) || r[k] < 0 || r[k] > 1) fail(`${id}: region.${k} must be a fraction 0..1 of the overview`);
    }
    if (r.x + r.w > 1 || r.y + r.h > 1) fail(`${id}: region must lie inside the overview`);
    if (!p.detail.label) fail(`${id}: detail needs a label`);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const base = A(q, p.enter, id, 'enter');
      const zoomRef = p.at || p.enter;
      const zA = A(q, zoomRef, id, 'at');
      const ox = X0, oy = Y0, ow = 800, oh = 620;
      const ix = ox + 24, iy = oy + 24 + lineH(t.body) + 16, iw = ow - 48, ih = oh - (iy - oy) - 24;
      q.card({ id: `${sec(p, id)}:overview`, x: ox, y: oy, w: ow, h: oh, alpha: base, pad: 24 }, (k) => {
        k.text({ text: p.overview.label || 'The whole', size: t.body, weight: 700, y: oy + 24, color: role(k, 'muted') });
        const ov = p.overview;
        if (ov.asset) {
          if (!ov.asset.path || !ov.asset.subjectId) fail(`${id}: overview.asset needs {path, subjectId}`);
          const srcW = num(ov.asset.srcW, iw);
          k.asset({ path: ov.asset.path, subjectId: ov.asset.subjectId, subsystem: ov.asset.subsystem, srcW,
                    image: ov.asset.image, x: ix, y: iy, w: iw, h: ih, alpha: base });
        } else {
          const parts = Math.max(4, Math.min(16, num(ov.parts, 9)));
          const cols = Math.ceil(Math.sqrt(parts)), rowsN = Math.ceil(parts / cols), g = 12;
          const bw = (iw - g * (cols - 1)) / cols, bh = (ih - g * (rowsN - 1)) / rowsN;
          for (let i = 0; i < parts; i++) {
            const c = i % cols, rr = Math.floor(i / cols);
            k.mark({ shape: 'rect', x: ix + c * (bw + g), y: iy + rr * (bh + g), w: bw, h: bh, radius: 8, alpha: base,
                     fill: role(k, 'muted'), fillOpacity: 0.18, meaning: 'one part of the whole' });
          }
        }
      });
      const rg = { x: ix + r.x * iw, y: iy + r.y * ih, w: Math.max(24, r.w * iw), h: Math.max(24, r.h * ih) };
      const rid = `${sec(p, id)}:region`;
      q.node({ id: rid, ...rg, alpha: zA, fill: tint(role(q, 'accent'), 0.18), stroke: role(q, 'accent') });
      const dx = ox + ow + 160, dw = X1 - dx, dh = oh;
      q.card({ id: `${sec(p, id)}:detail`, x: dx, y: oy, w: dw, h: dh, alpha: zA, pad: 34,
               fill: tint(role(q, 'accent'), 0.08) }, (k) => {
        k.text({ text: p.detail.label, size: t.h2, weight: 700, y: oy + 34, color: role(k, 'accent') });
        let y = oy + 34 + lineH(t.h2) + 18;
        (p.detail.lines || []).slice(0, 6).forEach((ln, i) => {
          const la = p.detail.lineAt && p.detail.lineAt[i] !== undefined ? A(k, p.detail.lineAt[i], id, 'detail.lineAt') : zA;
          y = para(k, ln, t.body, dw - 68, 2, dx + 34, y, { alpha: la });
          y += 8;
        });
      });
      // zoom rays: the detail IS that region, enlarged (marks, so no text can sit on them)
      q.mark({ shape: 'line', points: [{ x: rg.x + rg.w, y: rg.y }, { x: dx, y: oy }], alpha: zA * 0.6,
               stroke: role(q, 'accent'), width: 2, dash: [6, 8], meaning: 'zoom ray: top edge of the enlarged region' });
      q.mark({ shape: 'line', points: [{ x: rg.x + rg.w, y: rg.y + rg.h }, { x: dx, y: oy + dh }], alpha: zA * 0.6,
               stroke: role(q, 'accent'), width: 2, dash: [6, 8], meaning: 'zoom ray: bottom edge of the enlarged region' });
      q.connector({ from: rid, to: `${sec(p, id)}:detail`, alpha: zA, color: role(q, 'accent'), width: 4 });
    });
  }

  // ===========================================================================
  // 26. this caused that → CAUSE → EFFECT CHAIN
  // ===========================================================================
  function causeChain(z, p) {
    const id = 'causeChain';
    need(p, ['links', 'enter'], id);
    const links = needList(p, 'links', id, 2, 6);
    links.forEach((l, i) => { if (!l.id || !l.label) fail(`${id}: link ${i} needs {id, label}`); });
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const n = links.length, w = Math.min(320, Math.floor((WIDTH - 40 * (n - 1)) / n)), h = 110;
      const ys = [300, 600];
      const boxes = links.map((l, i) => ({ x: X0 + (WIDTH - w) * (i / (n - 1)), y: ys[i % 2], w, h }));
      const ids = links.map(l => `${sec(p, id)}:${l.id}`);
      const alphas = links.map((l, i) => itemAlpha(q, l, i, p, id));
      links.forEach((l, i) => {
        const last = i === n - 1, first = i === 0;
        slot(q, boxes[i], l.label, p, id, alphas[i]);
        q.node({ id: ids[i], ...boxes[i], text: l.label, textSize: t.node, alpha: alphas[i],
                 stroke: last ? role(q, 'negative') : (first ? role(q, 'warning') : undefined),
                 fill: last ? tint(role(q, 'negative'), 0.14) : undefined });
      });
      for (let i = 0; i < n - 1; i++) {
        q.connector({ from: ids[i], to: ids[i + 1], alpha: alphas[i + 1], color: role(q, 'muted') });
      }
      const tag = (i, text, r) => {
        const b = boxes[i], top = (i % 2) === 0;
        pill(q, { text, size: t.label, x: b.x, y: top ? b.y - chipH(t.label) - 16 : b.y + h + 16, alpha: alphas[i], color: role(q, r) });
      };
      tag(0, p.causeLabel || 'cause', 'warning');
      tag(n - 1, p.effectLabel || 'effect', 'negative');
    });
  }

  // ===========================================================================
  // 27. quantities against a baseline → BAR COMPARE
  // ===========================================================================
  function barCompare(z, p) {
    const id = 'barCompare';
    need(p, ['bars', 'enter'], id);
    const bars = needList(p, 'bars', id, 2, 7);
    bars.forEach((b, i) => { if (!b.label || !finite(b.value) || b.value < 0) fail(`${id}: bar ${i} needs {label, value ≥ 0}`); });
    if (p.baseline && !finite(p.baseline.value)) fail(`${id}: baseline.value must be a number`);
    const t = T(z);
    z.section(sec(p, id), (q) => {
      q.declareForm({ catalog: id });
      heading(q, p, id);
      const lw = 420, bx = X0 + lw + 24, maxLen = 1080;
      const scale = Math.max(...bars.map(b => b.value), p.baseline ? p.baseline.value : 0) || 1;
      const n = bars.length, gap = 22;
      const bottom = p.baseline ? Y1 - 70 : Y1;
      const h = Math.min(84, Math.floor((bottom - Y0 - gap * (n - 1)) / n));
      bars.forEach((b, i) => {
        const y = Y0 + i * (h + gap);
        const a = itemAlpha(q, b, i, p, id);
        const grow = eased(q, b.at !== undefined ? P(q, b.at, id, 'at', { dur: 0.8 }) : P(q, p.enter, id, 'enter', { dur: 0.8, lead: -i * q.stagger() }));
        const len = Math.max(4, maxLen * (b.value / scale) * grow);
        const hot = p.highlight === b.label;
        const f = fit(q, b.label, t.body, lw);
        q.text({ text: f.text, size: f.size, x: X0 + lw - measure(q, f.text, f.size), y: y + (h - lineH(f.size)) / 2, alpha: a });
        q.mark({ shape: 'rect', x: bx, y, w: len, h, radius: 6, alpha: a, fill: hot ? role(q, 'accent') : role(q, 'ink'),
                 fillOpacity: hot ? 1 : 0.35 + 0.4 * (b.value / scale), meaning: `${b.label}: ${b.value}${p.unit ? ' ' + p.unit : ''}` });
        const vt = fmtNum(b.value * grow, p.format, p.unit);
        q.text({ text: vt, size: 28, x: bx + len + 16, y: y + (h - lineH(28)) / 2, alpha: a, weight: 700,
                 color: hot ? role(q, 'accent') : role(q, 'ink') });
      });
      if (p.baseline) {
        const blA = A(q, p.baseline.at || p.enter, id, 'baseline.at');
        const x = bx + maxLen * (p.baseline.value / scale);
        q.mark({ shape: 'line', points: [{ x, y: Y0 - 14 }, { x, y: bottom + 6 }], alpha: blA, stroke: role(q, 'warning'),
                 width: 4, dash: [10, 8], meaning: `the baseline: ${p.baseline.label} = ${p.baseline.value}` });
        const lab = `${p.baseline.label}: ${fmtNum(p.baseline.value, p.format, p.unit)}`;
        const cw = chipW(q, lab, t.label);
        pill(q, { text: lab, size: t.label, x: clampN(x - cw / 2, X0, X1 - cw), y: bottom + 16, alpha: blA, color: role(q, 'warning') });
      }
    });
  }

  const COMPONENTS = {
    matrix2x2, balance, ledger, route, splitMorph, stateRing, funnel, failCard, timeline, layerStack,
    fanIn, fanOut, pipeline, comparisonTable, bigNumber, evidenceCard, codeSpotlight, zoneMap, treeReveal,
    sequence, gauge, heatmap, checklist, network, zoomDetail, causeChain, barCompare,
  };

  return Object.assign({
    names() { return Object.keys(COMPONENTS); },
    has(name) { return Object.prototype.hasOwnProperty.call(COMPONENTS, name); },
    helpers: { fit, wrap, tint, role, sizes: T, alpha: A, progress: P, frame: { X0, X1, Y0, Y1 } },
  }, COMPONENTS);
});
