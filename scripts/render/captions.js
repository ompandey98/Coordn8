/*
 * Coordn8 — IN-SCENE CAPTION OVERLAY (E1 colour marks + E8 caption styles).
 * Zero dependencies. Runs in a browser (canvas or DOM) and in Node (layout / trace only).
 *
 * WHAT IT DRAWS
 *   The caption cue active at time t, in the active caption STYLE (data/caption_styles.json,
 *   resolved by scripts/render/caption_marks.py into window.__C8_CAPTION_STYLE__), with:
 *     - colour MARKS from the display text: ⟦good:…⟧ ⟦bad:…⟧ ⟦key:…⟧ (canonical syntax), each
 *       painted in its ROLE colour from the active kit PLUS its non-colour cue (underline,
 *       strike-through, box, weight) — meaning never rides on hue alone;
 *     - at most max_marks (never more than 2) marks per caption: extra marks are drawn plain
 *       and a finding is recorded;
 *     - the CURRENT WORD highlighted when the style asks for it, from the alignment output
 *       window.__C8_WORDS__ = {lines:[{name, text, start, end, words:[{w, start, end, i}]}],
 *       source, fps} (absolute times; scripts/render/align.py): the line is found by the cue's
 *       name, else by time, and the word by its index `i` in that line. If the timings do not
 *       match the caption's words, a character-proportional estimate is used and reported.
 *
 * THE LEGIBILITY FLOOR (never shrink)
 *   Caption type is never below 28 px at 1080p. A style or an override below that is CLAMPED
 *   to 28 px and a finding is recorded; text that then does not fit in max_lines is TRUNCATED
 *   with an ellipsis (and a finding), never shrunk. Unreadable text is a worse defect than a
 *   truncated one.
 *
 * HOW IT DRAWS
 *   draw(target, t, opts) — `target` may be:
 *     a scene from scene_runtime.js (has .text): the scrim is a card and every word span goes
 *       through the runtime's own text primitive, so captions appear in the instrumentation
 *       trace like any other text (and the semantic gates see them);
 *     a CanvasRenderingContext2D: drawn directly;
 *     a DOM element: rendered as positioned <span>s (no canvas needed);
 *     null: layout only (Node / tests).
 *   Everything is a pure function of t — no clock, no randomness, no CSS animation.
 */
'use strict';

(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.C8Captions = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this),
function (root) {
  const MIN_PX = 28;
  const MAX_LINES = 2;
  const MAX_CHARS = 42;
  const MAX_MARKS = 2;
  const OPEN = '⟦', CLOSE = '⟧';
  const EST_EM = 0.54;                      // deterministic advance estimate when nothing can measure
  const DEFAULT_MARKS = {
    good: { role: 'positive', styles: ['underline'], color: '#2a7436' },
    bad: { role: 'negative', styles: ['strike-through'], color: '#a92f2f' },
    key: { role: 'accent', styles: ['box'], color: '#7a5f00' },
  };
  const DEFAULT_STYLE = {
    id: 'kit-default', font_px: 32, weight: 500, line_height: 1.28, max_lines: 2,
    max_chars_per_line: 42, overflow: 'truncate', align_text: 'center',
    position: { anchor: 'bottom', align: 'center', inset_pct: 7, width_pct: 72 },
    text: { role: 'ink' }, scrim: { role: 'paper', alpha: 0.9, radius_px: 12, pad_px: 18 },
    highlight: { mode: 'none' }, reveal: 'all',
  };

  function finite(n) { return typeof n === 'number' && Number.isFinite(n); }
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }

  // ------------------------------------------------------------------ marks
  const NAME = '[A-Za-z][A-Za-z0-9_-]*';
  const MARK_RE = new RegExp(OPEN + '(?:(' + NAME + '):([^' + OPEN + CLOSE + ']*)' + CLOSE +
    '|(' + NAME + ')' + CLOSE + '([^' + OPEN + CLOSE + ']*)' + OPEN + '/\\3' + CLOSE + ')', 'g');
  const FRAG_RE = new RegExp(OPEN + '/?' + NAME + '(?::|' + CLOSE + ')|[' + OPEN + CLOSE + ']', 'g');

  /** Same grammar as caption_marks.py: plain text + per-character mark owner. */
  function parse(text) {
    const s = String(text === undefined || text === null ? '' : text);
    if (s.indexOf(OPEN) < 0 && s.indexOf(CLOSE) < 0) return { plain: s, spans: [], errors: [] };
    let plain = '', pos = 0, n = 0;
    const spans = [], errors = [];
    const left = seg => {
      if (seg.indexOf(OPEN) >= 0 || seg.indexOf(CLOSE) >= 0) {
        errors.push('malformed mark syntax');
        return seg.replace(FRAG_RE, '');
      }
      return seg;
    };
    MARK_RE.lastIndex = 0;
    let m;
    while ((m = MARK_RE.exec(s)) !== null) {
      plain += left(s.slice(pos, m.index));
      const alias = m[1] === undefined;
      const name = alias ? m[3] : m[1];
      const inner = alias ? m[4] : m[2];
      spans.push({ index: n++, name, text: inner, start: plain.length, end: plain.length + inner.length });
      plain += inner;
      pos = m.index + m[0].length;
    }
    plain += left(s.slice(pos));
    return { plain, spans, errors };
  }

  function strip(text) { return parse(text).plain; }

  /** [{w, mark, idx}] over the whitespace-split plain text (same as caption_marks.tokens). */
  function words(text) {
    const p = parse(text);
    const owner = new Array(p.plain.length).fill(null);
    for (const sp of p.spans) for (let i = sp.start; i < sp.end; i++) owner[i] = sp.index;
    const names = {};
    for (const sp of p.spans) names[sp.index] = sp.name;
    const out = [];
    const re = /\S+/g;
    let m;
    while ((m = re.exec(p.plain)) !== null) {
      let idx = null;
      for (let i = m.index; i < m.index + m[0].length; i++) if (owner[i] !== null) { idx = owner[i]; break; }
      out.push({ w: m[0], idx, mark: idx === null ? null : names[idx] });
    }
    return out;
  }

  // ------------------------------------------------------------------ cues (mirror of subtitles.py)
  /** Sentences [{text,start,end}] -> readable cues, packed like the .srt (42 chars, 2 lines). */
  function segment(sentences, opts) {
    const o = opts || {};
    const maxChars = Math.min(MAX_CHARS, o.max_chars_per_line || MAX_CHARS);
    const maxLines = Math.min(MAX_LINES, o.max_lines || MAX_LINES);
    const cap = 7.0 * 0.9;
    const out = [];
    const rows = (sentences || []).filter(s => strip(s.text || '').trim())
      .slice().sort((a, b) => (a.start || 0) - (b.start || 0));
    for (const s of rows) {
      // `wi` = index among the line's WORD tokens (edge punctuation stripped; tokens with no
      // letter or digit are not words) — the same `i` the alignment output uses
      let wi = 0;
      const toks = words(s.text).map(tk => isWord(tk.w) ? Object.assign({}, tk, { wi: wi++ }) : tk);
      const lineName = s.name || s.id || null;
      const lines = [];
      let cur = [];
      for (const tk of toks) {
        const cand = cur.concat([tk]).map(x => x.w).join(' ');
        if (cur.length && cand.length > maxChars) { lines.push(cur); cur = [tk]; } else cur.push(tk);
      }
      if (cur.length) lines.push(cur);
      let groups = [];
      for (let i = 0; i < lines.length; i += maxLines) groups.push([].concat(...lines.slice(i, i + maxLines)));
      const start = +s.start || 0, end = Math.max(start, +s.end || start);
      const span = end - start;
      // slow narration: split finer, down to one line per cue (same rule as subtitles.py)
      const effCap = 7.0 * 0.85;
      if (span > effCap && lines.length > groups.length && Math.ceil(span / effCap) > groups.length) {
        groups = lines.map(l => l.slice());
      }
      const total = groups.reduce((a, g) => a + g.map(x => x.w).join(' ').length, 0) || 1;
      let t = start;
      for (const g of groups) {
        const txt = g.map(x => x.w).join(' ');
        const dur = Math.min(span * txt.length / total, cap);
        const e = Math.min(end, t + dur);
        // `tokens` keep the sentence-level mark identity (a two-word mark stays ONE mark)
        out.push({ start: t, end: e, tokens: g, text: txt, name: lineName, line_start: start, line_end: end });
        t = e;
      }
    }
    return out;
  }

  function cueAt(cues, t) {
    for (const c of cues || []) if (t >= c.start && t < c.end) return c;
    return null;
  }

  // ------------------------------------------------------------------ style + kit
  function activeStyle(opts) {
    const o = opts || {};
    const kit = o.kit || (root && root.__C8_KIT__) || null;
    let st = o.style || (root && root.__C8_CAPTION_STYLE__) || null;
    if (!st && kit && kit.caption) {
      const k = kit.caption;
      st = Object.assign({}, DEFAULT_STYLE, {
        id: 'kit:' + (kit.kit || 'active'), font_px: k.size, weight: k.weight,
        max_lines: k.max_lines, position: {
          anchor: (k.position || '').indexOf('top') === 0 ? 'top' : 'bottom',
          align: (k.position || '').indexOf('left') >= 0 ? 'left' : 'center', inset_pct: 7,
          width_pct: k.width_pct || 72 },
        colors: { text: k.color, scrim: k.scrim, highlight: (kit.colors || {}).accent },
        marks: k.marks, max_marks: k.max_marks,
        scrim: { role: 'paper', alpha: 1, radius_px: k.scrim_radius, pad_px: k.pad },
      });
    }
    return Object.assign({}, DEFAULT_STYLE, st || {});
  }

  function colorsOf(style, kit) {
    const c = Object.assign({}, style.colors || {});
    const kc = (kit && kit.colors) || {};
    const role = r => (r && kc[r.role || r]) || null;
    if (!c.text) c.text = role(style.text) || '#f2f4f8';
    if (c.scrim === undefined) c.scrim = style.scrim ? (role(style.scrim) || 'rgba(10,12,18,0.82)') : null;
    if (!c.outline && style.outline) c.outline = role(style.outline) || '#000000';
    if (!c.highlight) c.highlight = role(style.highlight) || kc.accent || '#f4d35e';
    return c;
  }

  function marksOf(style, kit) {
    const out = {};
    const km = (kit && kit.caption && kit.caption.marks) || {};
    for (const n of Object.keys(DEFAULT_MARKS)) out[n] = Object.assign({}, DEFAULT_MARKS[n]);
    for (const n of Object.keys(km)) out[n] = Object.assign({}, out[n] || {}, km[n]);
    const sm = style.marks || {};
    for (const n of Object.keys(sm)) out[n] = Object.assign({}, out[n] || {}, sm[n]);
    return out;
  }

  // ------------------------------------------------------------------ measuring
  function makeMeasure(target, font, weight) {
    if (target && typeof target._measure === 'function') return (s, px) => target._measure(s, px);
    let ctx = null;
    if (target && typeof target.measureText === 'function') ctx = target;
    else if (typeof document !== 'undefined' && document.createElement) {
      try { ctx = document.createElement('canvas').getContext('2d'); } catch (e) { ctx = null; }
    }
    if (ctx) {
      return (s, px) => {
        ctx.save && ctx.save();
        ctx.font = `${weight} ${px}px ${font}`;
        const w = ctx.measureText(s).width;
        ctx.restore && ctx.restore();
        return finite(w) && w > 0 ? w : s.length * px * EST_EM;
      };
    }
    return (s, px) => s.length * px * EST_EM;
  }

  // ------------------------------------------------------------------ layout
  /**
   * Lay one cue out in a style. Returns {font_px, lines, box, truncated, findings}.
   * opts: {width, height, font_px (override), measure, kit}
   */
  function layout(textOrTokens, style, opts) {
    const o = opts || {};
    const W = o.width || 1920, H = o.height || 1080;
    const scale = H / 1080;
    const findings = [];
    let want = finite(o.font_px) ? o.font_px : style.font_px;
    if (!finite(want) || want < MIN_PX) {
      findings.push({ code: 'font_clamped', from: want, to: MIN_PX, style: style.id,
        message: `caption font ${want}px is below the ${MIN_PX}px floor at 1080p — clamped, never shrunk` });
      want = MIN_PX;
    }
    const px = want * scale;
    const lh = finite(style.line_height) ? style.line_height : 1.28;
    const maxLines = Math.max(1, Math.min(MAX_LINES, style.max_lines || MAX_LINES));
    const maxChars = Math.max(12, Math.min(MAX_CHARS, style.max_chars_per_line || MAX_CHARS));
    const pos = style.position || {};
    const scrim = style.scrim || null;
    const pad = ((scrim && finite(scrim.pad_px)) ? scrim.pad_px : (finite(style.pad_px) ? style.pad_px : 16)) * scale;
    const boxW = W * (finite(pos.width_pct) ? pos.width_pct : 72) / 100;
    const inner = boxW - 2 * pad;
    const measure = o.measure || ((s, p) => s.length * p * EST_EM);
    const space = measure(' ', px) || px * 0.27;

    const toks = Array.isArray(textOrTokens) ? textOrTokens : words(textOrTokens);
    const lines = [];
    let cur = [], curW = 0, curChars = 0;
    for (const tk of toks) {
      const w = measure(tk.w, px);
      const addW = cur.length ? curW + space + w : w;
      const addC = cur.length ? curChars + 1 + tk.w.length : tk.w.length;
      if (cur.length && (addW > inner || addC > maxChars)) {
        lines.push(cur); cur = [tk]; curW = w; curChars = tk.w.length;
      } else { cur.push(tk); curW = addW; curChars = addC; }
    }
    if (cur.length) lines.push(cur);

    let truncated = false;
    let kept = lines;
    if (lines.length > maxLines) {
      truncated = true;
      kept = lines.slice(0, maxLines).map(l => l.slice());
      const last = kept[kept.length - 1];
      const dropped = [].concat(...lines.slice(maxLines)).map(x => x.w);
      // ellipsize the last kept line until it (plus the ellipsis) fits width and char budget
      const fits = l => {
        const s = l.map(x => x.w).join(' ') + '…';
        return measure(s, px) <= inner && s.length <= maxChars;
      };
      while (last.length > 1 && !fits(last)) dropped.unshift(last.pop().w);
      last[last.length - 1] = Object.assign({}, last[last.length - 1], { w: last[last.length - 1].w + '…' });
      findings.push({ code: 'truncated', style: style.id, font_px: want, dropped_words: dropped.length,
        message: `caption needs ${lines.length} lines at ${want}px in style '${style.id}' (max ${maxLines}) — ` +
                 `truncated with an ellipsis, ${dropped.length} word(s) not shown; split the cue instead` });
    }
    // a single word wider than the line: ellipsize inside the word (never shrink)
    for (const l of kept) {
      for (let i = 0; i < l.length; i++) {
        if (measure(l[i].w, px) > inner) {
          let s = l[i].w;
          while (s.length > 1 && measure(s + '…', px) > inner) s = s.slice(0, -1);
          l[i] = Object.assign({}, l[i], { w: s + '…' });
          truncated = true;
          findings.push({ code: 'word_truncated', style: style.id, message: 'a single word is wider than the caption line — ellipsized, never shrunk' });
        }
      }
    }

    const lineH = px * lh;
    const boxH = kept.length * lineH + 2 * pad;
    const inset = (finite(pos.inset_pct) ? pos.inset_pct : 7) / 100;
    const align = pos.align || 'center', anchor = pos.anchor || 'bottom';
    const bx = align === 'center' ? (W - boxW) / 2 : (align === 'left' ? W * inset : W - W * inset - boxW);
    // the box keeps its max-lines footprint so the caption never jumps between 1- and 2-line cues
    const fullH = maxLines * lineH + 2 * pad;
    const by = anchor === 'bottom' ? (H - H * inset - fullH) + (fullH - boxH)
      : (anchor === 'top' ? H * inset : (H - boxH) / 2);
    const textAlign = style.align_text || 'center';
    const outLines = kept.map((l, li) => {
      const ws = l.map(x => ({ text: x.w, mark: x.mark, idx: x.idx, wi: x.wi, w: measure(x.w, px) }));
      const lw = ws.reduce((a, x) => a + x.w, 0) + space * Math.max(0, ws.length - 1);
      let x = textAlign === 'left' ? bx + pad : (textAlign === 'right' ? bx + boxW - pad - lw : bx + (boxW - lw) / 2);
      const y = by + pad + li * lineH + (lineH - px) / 2;
      for (const wd of ws) { wd.x = x; wd.y = y; x += wd.w + space; }
      return { words: ws, w: lw, y };
    });
    return { font_px: want, px, line_height: lh, lines: outLines, truncated,
             box: { x: bx, y: by, w: boxW, h: boxH }, findings };
  }

  // ------------------------------------------------------------------ timing within a cue
  const EDGE_RE = /^[^\p{L}\p{N}_]+|[^\p{L}\p{N}_]+$/gu;
  function normWord(w) { return strip(String(w || '')).replace(/…$/, '').replace(EDGE_RE, ''); }
  function isWord(w) { return /[\p{L}\p{N}_]/u.test(normWord(w)); }

  /** The alignment line for a cue: by the cue's name, else the line spoken at t, else the line
   *  overlapping the cue window most. `timed` is the window.__C8_WORDS__ object. */
  function lineFor(timed, cue, t) {
    const lines = timed && Array.isArray(timed.lines) ? timed.lines : [];
    if (!lines.length) return { line: null, by: null };
    if (cue.name) {
      const l = lines.find(x => x.name === cue.name);
      if (l) return { line: l, by: 'name' };
    }
    let l = lines.find(x => finite(x.start) && finite(x.end) && t >= x.start && t < x.end);
    if (l) return { line: l, by: 'time' };
    let best = null, bo = 0;
    for (const x of lines) {
      const ov = Math.min(x.end, cue.end) - Math.max(x.start, cue.start);
      if (ov > bo) { bo = ov; best = x; }
    }
    return { line: best, by: best ? 'time' : null };
  }

  /** Give caption tokens without `wi` their word index in the line (cues not built by segment()):
   *  the consecutive run of line words equal to the cue's words, nearest the cue start. */
  function assignIndices(tokens, line, cue) {
    if (tokens.every(x => !isWord(x.w) || Number.isInteger(x.wi))) return tokens;
    const lw = line.words.map(w => String(w.w).toLowerCase());
    const q = tokens.filter(x => isWord(x.w)).map(x => normWord(x.w).toLowerCase());
    let best = -1, bd = Infinity;
    for (let k = 0; k + q.length <= lw.length; k++) {
      let ok = true;
      for (let j = 0; j < q.length; j++) if (lw[k + j] !== q[j]) { ok = false; break; }
      if (ok) { const d = Math.abs((line.words[k].start || 0) - cue.start); if (d < bd) { bd = d; best = k; } }
    }
    if (best < 0) return tokens;
    let j = 0;
    return tokens.map(x => isWord(x.w) ? Object.assign({}, x, { wi: best + j++ }) : x);
  }

  /** Per caption token {start, end}: from the alignment line (word by index), else an estimate. */
  function wordTimes(cue, tokens, timed, t) {
    const n = tokens.length;
    const tt = finite(t) ? t : cue.start;
    const { line, by } = lineFor(timed, cue, tt);
    let reason = timed ? 'no alignment line for this caption' : 'no word timings (window.__C8_WORDS__)';
    if (line && Array.isArray(line.words) && line.words.length) {
      const toks = assignIndices(tokens, line, cue);
      const byI = new Map(line.words.map(w => [w.i, w]));
      let ok = true, prev = null;
      const times = toks.map(x => {
        if (!isWord(x.w)) return prev ? { start: prev.start, end: prev.end } : null;   // "—" rides with its word
        const w = Number.isInteger(x.wi) ? byI.get(x.wi) : null;
        if (!w || String(w.w).toLowerCase() !== normWord(x.w).toLowerCase() || !finite(w.start)) { ok = false; return null; }
        prev = { start: w.start, end: finite(w.end) ? w.end : w.start + 0.2, i: w.i };
        return prev;
      });
      if (ok) {
        const first = times.find(Boolean);
        return { source: 'word-timings', line: line.name, by,
                 times: times.map(x => x || { start: first ? first.start : cue.start, end: first ? first.end : cue.start }) };
      }
      reason = `word timings for line '${line.name}' do not match the caption's words`;
    }
    // character-proportional estimate over the cue window
    const total = tokens.reduce((a, x) => a + x.w.length + 1, 0) || 1;
    const span = Math.max(0, cue.end - cue.start);
    let acc = 0;
    const times = tokens.slice(0, n).map(x => {
      const s = cue.start + span * acc / total;
      acc += x.w.length + 1;
      return { start: s, end: cue.start + span * acc / total };
    });
    return { source: 'estimate', reason, times };
  }

  // ------------------------------------------------------------------ findings
  function record(findings) {
    if (!root) return;
    const sink = root.__C8_FINDINGS__ = root.__C8_FINDINGS__ || [];
    for (const f of findings) {
      const key = 'captions:' + f.code + ':' + (f.style || '') + ':' + (f.cue || '');
      if (!sink.some(x => x.key === key)) sink.push(Object.assign({ key, source: 'captions.js' }, f));
    }
  }

  // ------------------------------------------------------------------ draw
  /**
   * Draw the caption active at t. opts: {cues | sentences, style, kit, words, width, height,
   * font_px (override), font}. Returns {cue, layout, drawn, findings} (cue null between cues).
   */
  function draw(target, t, opts) {
    const o = opts || {};
    if (!finite(t)) throw new Error('[coordn8:captions] draw() needs a finite t, got ' + t);
    const kit = o.kit || (root && root.__C8_KIT__) || null;
    const style = activeStyle(o);
    const colors = colorsOf(style, kit);
    const marks = marksOf(style, kit);
    const cap = Math.min(MAX_MARKS, finite(style.max_marks) ? style.max_marks : MAX_MARKS);
    const cues = o.cues || segment(o.sentences || (root && root.__C8_BEATMAP__ && root.__C8_BEATMAP__.sentences) || [], style);
    const cue = cueAt(cues, t);
    const W = o.width || (target && target.width) || 1920;
    const H = o.height || (target && target.height) || 1080;
    if (!cue) return { cue: null, layout: null, drawn: [], findings: [] };
    const font = o.font || style.font || (kit && kit.text && kit.text.font) || 'Inter, system-ui, sans-serif';
    const weight = style.weight || 500;
    const measure = makeMeasure(target, font, weight);
    const L = layout(cue.tokens || cue.text, style, { width: W, height: H, font_px: o.font_px, measure });
    const findings = L.findings.map(f => Object.assign({ cue: Math.round(cue.start * 1000) }, f));

    // per-caption mark cap + unknown marks
    const seen = [];
    for (const ln of L.lines) for (const wd of ln.words) if (wd.mark !== null && seen.indexOf(wd.idx) < 0) seen.push(wd.idx);
    const allowed = seen.slice(0, cap);
    if (seen.length > cap) {
      findings.push({ code: 'too_many_marks', cue: Math.round(cue.start * 1000), count: seen.length, cap,
        message: `${seen.length} marks in one caption (cap ${cap}) — the extra marks are drawn plain` });
    }
    for (const ln of L.lines) for (const wd of ln.words) {
      if (wd.mark && !marks[wd.mark]) {
        findings.push({ code: 'unknown_mark', cue: Math.round(cue.start * 1000), mark: wd.mark,
          message: `mark '${wd.mark}' is not a mark of the active kit — drawn plain` });
      }
    }

    // timing: optional fade (style.fade_s, default none — a caption is readable from its first
    // frame), word reveal, current word
    const fade = finite(style.fade_s) ? style.fade_s : 0;
    const a = fade > 0 ? Math.min(clamp01((t - cue.start) / fade + 0.5), clamp01((cue.end - t) / fade + 0.5)) : 1;
    const flat = [].concat(...L.lines.map(l => l.words));
    const wt = wordTimes(cue, flat.map(x => ({ w: x.text, wi: x.wi })), o.words || (root && root.__C8_WORDS__) || null, t);
    let current = -1;
    for (let i = 0; i < wt.times.length; i++) if (t >= wt.times[i].start) current = i;
    const hl = style.highlight || {};

    const drawn = [];
    flat.forEach((wd, i) => {
      const markOk = wd.mark && marks[wd.mark] && allowed.indexOf(wd.idx) >= 0;
      const m = markOk ? marks[wd.mark] : null;
      let color = m ? (m.color || colors.text) : colors.text;
      let wWeight = weight;
      const deco = m ? (m.styles || []).slice() : [];
      for (const s of deco) { const mm = /^weight-(\d+)$/.exec(s); if (mm) wWeight = +mm[1]; }
      const isCur = hl.mode === 'word' && i === current;
      const isPhrase = hl.mode === 'phrase' && !!m;
      if ((isCur || isPhrase) && hl.style === 'color') color = colors.highlight;
      if ((isCur || isPhrase) && hl.style === 'weight') wWeight = Math.max(wWeight, 800);
      const extra = [];
      if ((isCur || isPhrase) && hl.style === 'box') extra.push('hl-box');
      if ((isCur || isPhrase) && hl.style === 'underline') extra.push('hl-underline');
      let wa = a;
      if (style.reveal === 'word') wa = a * clamp01((t - wt.times[i].start + 0.12) / 0.12);
      const caps = deco.some(s => /caps/.test(s));
      drawn.push({ text: caps ? wd.text.toUpperCase() : wd.text, x: wd.x, y: wd.y, w: wd.w, size: L.px,
                   color, weight: wWeight, alpha: wa, mark: m ? wd.mark : null, role: m ? m.role : null,
                   deco: deco.concat(extra), current: isCur });
    });

    paint(target, L, drawn, colors, style, a, font, H / 1080);
    record(findings);
    return { cue, layout: L, drawn, findings, timing: wt.source, timing_line: wt.line || null,
             timing_reason: wt.reason || null, alpha: a, style: style.id };
  }

  function paint(target, L, drawn, colors, style, a, font, scale) {
    if (!target) return;
    const isScene = typeof target.text === 'function' && typeof target.card === 'function';
    const ctx = isScene ? target.ctx : (typeof target.fillText === 'function' ? target : null);
    const scrim = style.scrim;
    if (isScene) {
      if (scrim && colors.scrim && a > 0.001) {
        target.card({ kind: 'caption-scrim', x: L.box.x, y: L.box.y, w: L.box.w, h: L.box.h,
                      alpha: a, fill: colors.scrim, stroke: 'rgba(0,0,0,0)', radius: (scrim.radius_px || 0) * scale });
      }
      if (ctx && style.outline && colors.outline) {
        ctx.save(); ctx.textBaseline = 'top'; ctx.lineJoin = 'round';
        ctx.lineWidth = (style.outline.px || 4) * scale * 2; ctx.strokeStyle = colors.outline;
        for (const d of drawn) {
          if (d.alpha <= 0.001) continue;
          ctx.globalAlpha = d.alpha; ctx.font = `${d.weight} ${d.size}px ${font}`;
          ctx.strokeText(d.text, d.x, d.y);
        }
        ctx.restore();
      }
      for (const d of drawn) {
        if (d.alpha <= 0.001) continue;
        target.text({ text: d.text, x: d.x, y: d.y, size: d.size, color: d.color, weight: d.weight,
                      alpha: d.alpha, font, unbounded: false });
      }
      if (ctx) decorate(ctx, drawn, colors, scale);
      return;
    }
    if (ctx) {
      ctx.save();
      if (scrim && colors.scrim && a > 0.001) {
        ctx.globalAlpha = a; ctx.fillStyle = colors.scrim;
        const r = (scrim.radius_px || 0) * scale;
        if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(L.box.x, L.box.y, L.box.w, L.box.h, r); ctx.fill(); }
        else ctx.fillRect(L.box.x, L.box.y, L.box.w, L.box.h);
      }
      ctx.textBaseline = 'top';
      for (const d of drawn) {
        if (d.alpha <= 0.001) continue;
        ctx.globalAlpha = d.alpha;
        ctx.font = `${d.weight} ${d.size}px ${font}`;
        if (style.outline && colors.outline) {
          ctx.lineJoin = 'round'; ctx.lineWidth = (style.outline.px || 4) * scale * 2;
          ctx.strokeStyle = colors.outline; ctx.strokeText(d.text, d.x, d.y);
        }
        ctx.fillStyle = d.color; ctx.fillText(d.text, d.x, d.y);
      }
      ctx.restore();
      decorate(ctx, drawn, colors, scale);
      return;
    }
    if (typeof target.appendChild === 'function' && typeof document !== 'undefined') {
      let host = target.querySelector ? target.querySelector('#c8-captions') : null;
      if (!host) {
        host = document.createElement('div'); host.id = 'c8-captions';
        host.setAttribute('aria-live', 'off');
        target.appendChild(host);
      }
      const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
      host.style.cssText = `position:absolute;left:${L.box.x}px;top:${L.box.y}px;width:${L.box.w}px;` +
        `height:${L.box.h}px;opacity:${a};pointer-events:none;` +
        (scrim && colors.scrim ? `background:${colors.scrim};border-radius:${(scrim.radius_px || 0) * scale}px;` : '');
      host.innerHTML = drawn.map(d => {
        const deco = [];
        if (d.deco.some(s => /underline/.test(s)) || d.deco.indexOf('hl-underline') >= 0) deco.push('underline');
        if (d.deco.indexOf('strike-through') >= 0) deco.push('line-through');
        const box = d.deco.indexOf('box') >= 0 || d.deco.indexOf('hl-box') >= 0;
        const outline = style.outline && colors.outline
          ? `-webkit-text-stroke:${(style.outline.px || 4) * scale}px ${colors.outline};paint-order:stroke fill;` : '';
        return `<span class="c8-word${d.mark ? ' c8-mark-' + esc(d.mark) : ''}${d.current ? ' c8-current' : ''}" ` +
          `data-mark="${esc(d.mark || '')}" style="position:absolute;left:${d.x - L.box.x}px;top:${d.y - L.box.y}px;` +
          `font:${d.weight} ${d.size}px ${font};color:${d.color};opacity:${d.alpha};white-space:nowrap;` +
          (deco.length ? `text-decoration:${deco.join(' ')};text-decoration-thickness:${Math.max(2, d.size * 0.07)}px;` : '') +
          (box ? `outline:${Math.max(2, d.size * 0.07)}px solid ${d.deco.indexOf('hl-box') >= 0 ? colors.highlight : d.color};outline-offset:2px;` : '') +
          outline + `">${esc(d.text)}</span>`;
      }).join('');
    }
  }

  /** Non-colour cues on a canvas: underline, strike-through, box, highlight box/underline. */
  function decorate(ctx, drawn, colors, scale) {
    ctx.save();
    for (const d of drawn) {
      if (d.alpha <= 0.001 || !d.deco.length) continue;
      ctx.globalAlpha = d.alpha;
      const th = Math.max(2, d.size * 0.07);
      const has = k => d.deco.some(s => s === k || s.indexOf(k) >= 0);
      ctx.strokeStyle = d.color; ctx.lineWidth = th;
      if (has('underline')) {
        if (has('dashed')) ctx.setLineDash && ctx.setLineDash([th * 3, th * 2]);
        else if (has('dotted')) ctx.setLineDash && ctx.setLineDash([th, th * 1.5]);
        else ctx.setLineDash && ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(d.x, d.y + d.size * 1.08); ctx.lineTo(d.x + d.w, d.y + d.size * 1.08); ctx.stroke();
        ctx.setLineDash && ctx.setLineDash([]);
      }
      if (has('strike-through')) {
        ctx.beginPath(); ctx.moveTo(d.x, d.y + d.size * 0.55); ctx.lineTo(d.x + d.w, d.y + d.size * 0.55); ctx.stroke();
      }
      if (d.deco.indexOf('box') >= 0 || d.deco.indexOf('hl-box') >= 0) {
        ctx.strokeStyle = d.deco.indexOf('hl-box') >= 0 ? colors.highlight : d.color;
        ctx.strokeRect(d.x - 6 * scale, d.y - 4 * scale, d.w + 12 * scale, d.size * 1.22 + 8 * scale);
      }
    }
    ctx.restore();
  }

  return { MIN_PX, MAX_MARKS, parse, strip, words, segment, cueAt, layout, draw, activeStyle,
           wordTimes, lineFor, DEFAULT_STYLE };
});

// ---------------------------------------------------------------------------
// Node CLI:  node captions.js layout '<json opts>'   (used by the Python tests)
//   opts: {text, style, font_px, width, height, t, sentences, words, kit}
// ---------------------------------------------------------------------------
if (typeof module === 'object' && typeof require === 'function' && require.main === module) {
  const C = module.exports;
  const [cmd, arg] = process.argv.slice(2);
  const o = JSON.parse(arg || '{}');
  let out;
  if (cmd === 'layout') {
    out = C.layout(o.text || '', Object.assign({}, C.DEFAULT_STYLE, o.style || {}),
                   { width: o.width, height: o.height, font_px: o.font_px });
  } else if (cmd === 'draw') {
    out = C.draw(null, o.t || 0, o);
  } else if (cmd === 'segment') {
    out = C.segment(o.sentences || [], o.style || {});
  } else {
    process.stderr.write('usage: node captions.js layout|draw|segment <json>\n');
    process.exit(2);
  }
  process.stdout.write(JSON.stringify(out) + '\n');
}
