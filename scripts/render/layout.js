/*
 * Coordn8 — DETERMINISTIC DIAGRAM LAYOUT (B3).  Zero dependencies besides the vendored,
 * sha-pinned layered-layout library (dagre, MIT) that scripts/render/fetch_visual_libs.py
 * puts into a build's _build/vendor/. Runs as `require()` in Node and as a <script> in a
 * scene or page (exports window.Coordn8Layout).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Hand-computed coordinates are where connector defects come from: a line drawn through a box
 * that is not its endpoint, a label sitting on its own line, an arrowhead buried inside its
 * target, two strokes stacked on each other. Rules alone do not stop them — they have to be
 * held by every author on every figure. This engine computes the geometry so the rules hold by
 * construction, and scripts/gates/check_connectors.py checks the result independently.
 *
 *   1. layered layout (rank -> order -> coordinates) by the vendored library, rankdir TB|BT|LR|RL
 *   2. ORTHOGONAL routing: elbows only, never a diagonal (opts.orthogonal, the default)
 *   3. attach points at L*k/(N+1) along a side for the N connectors on that side
 *   4. parallel segments >= 12 px apart (tracks inside each inter-layer gap are ordered so a
 *      vertical never runs alongside another within 12 px; the gap grows until they fit)
 *   5. every label gets an opaque MASK rect 8 px off its line (rule: 6-10 px), clear of nodes,
 *      other labels and other lines
 *   6. a HOP (small arc) wherever two connectors cross
 *   7. no segment passes behind an unrelated box: a pass-through that would is re-routed to
 *      the nearest free lane (recorded in result.rerouted)
 *   8. the arrowhead ENDS ON the target's border, never inside it
 *   9. paint order = topological order of the containment graph: groups (outer before inner),
 *      then edges, then labels, then nodes
 *  10. same input -> BYTE-IDENTICAL output: coordinates rounded, input order kept, no random
 *      ids, no timestamps
 *
 * Catalog-free by design (freedom doctrine): the engine never looks at a diagram-type catalog.
 * Any graph lays out, including a diagram form nobody has named yet.
 *
 *   const L = require('./layout.js');
 *   const res = L.layout({nodes:[{id:'a'},{id:'b'}], edges:[{from:'a', to:'b', label:'calls'}]},
 *                        {dagre: '_build/vendor/dagre.min.js', rankdir: 'TB'});
 *   const svg = L.toSVG(res, kitRuntime);     // kit = scripts/design/kit_export.py runtime JSON
 */
'use strict';

(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.Coordn8Layout = api;
  else if (root && typeof root === 'object') root.Coordn8Layout = api;
})(typeof self !== 'undefined' ? self : this, function () {

  const VERSION = '1.0.0';
  // ---- the connector rules, as numbers (one place) --------------------------------------------
  const RULES = Object.freeze({
    PARALLEL_MIN: 12,       // two parallel segments keep at least this far apart
    TRACK_STEP: 16,         // spacing between horizontal tracks in a gap (> PARALLEL_MIN)
    LABEL_GAP: 8,           // label mask edge to its own line
    LABEL_GAP_MIN: 6,       // ... allowed band 6..10
    LABEL_GAP_MAX: 10,
    LABEL_CLEAR: 4,         // a label keeps this far from nodes, other labels and other lines
    HOP_R: 5,               // crossing hop radius
    CORNER_CLEAR: 8,        // a port keeps this far from a box corner
    LANE_CLEAR: 12,         // a re-routed pass-through keeps this far from a box
    CHANNEL_GAP: 24,        // outside channel for back edges, from the outermost box
    SELF_LOOP: 24,          // how far a self-loop leaves its box
    MARGIN: 40,             // outer margin of the canvas
    GRID: 4,                // node origins on a 4 px grid
  });

  // ---- finding the vendored layered-layout library -------------------------------------------
  const _libCache = {};
  function findDagre(opts) {
    const d = opts && opts.dagre;
    if (d && typeof d === 'object' && typeof d.layout === 'function') return d;
    if (typeof d === 'string' && d) {
      if (_libCache[d]) return _libCache[d];
      if (typeof require !== 'function') throw new Error('layout(): a library PATH needs Node; in a page load dagre.min.js as a <script> first');
      const fs = require('fs');
      const src = fs.readFileSync(d, 'utf8');
      // The vendored single-file build declares `var dagre = ...` and does not set
      // module.exports, so it is evaluated as a script and its global is returned.
      let lib = null;
      try { lib = new Function(src + '\n;return (typeof dagre !== "undefined") ? dagre : undefined;')(); } catch (e) { lib = null; }
      if (!lib || typeof lib.layout !== 'function') {
        let mod = null;
        try { mod = require(require('path').resolve(d)); } catch (e) { mod = null; }
        if (mod && typeof mod.layout === 'function') lib = mod;
        else if (mod && mod.default && typeof mod.default.layout === 'function') lib = mod.default;
      }
      if (!lib || typeof lib.layout !== 'function') throw new Error(`layout(): ${d} did not define the layered-layout library (expected a global 'dagre' with layout())`);
      _libCache[d] = lib;
      return lib;
    }
    const g = (typeof globalThis !== 'undefined') ? globalThis : (typeof window !== 'undefined' ? window : {});
    if (g.dagre && typeof g.dagre.layout === 'function') return g.dagre;
    throw new Error('layout(): layered-layout library not found — pass opts.dagre (the library object or the path ' +
                    'of the vendored dagre.min.js; scripts/render/fetch_visual_libs.py fetches it) or load it as a <script> first');
  }

  // ---- small numeric helpers -----------------------------------------------------------------
  function num(v, d) { return (typeof v === 'number' && isFinite(v)) ? v : d; }
  function r1(n) { const v = Math.round(n * 10) / 10; return (v === 0 || Object.is(v, -0)) ? 0 : v; }
  function ceilTo(n, q) { return Math.ceil(n / q - 1e-9) * q; }
  function roundTo(n, q) { return Math.round(n / q) * q; }
  function fmt(n) { const v = r1(n); return String(v); }

  // ---- text measurement (no font metrics: a calibrated per-character budget) ----------------
  // Every wide/full-width character costs 1 em, combining marks cost nothing, every other
  // character costs 0.60 em — the budget that fits common sans and mono faces.
  function isMark(c) {
    return (c >= 0x0300 && c <= 0x036F) || (c >= 0x1AB0 && c <= 0x1AFF) || (c >= 0x1DC0 && c <= 0x1DFF) ||
           (c >= 0x20D0 && c <= 0x20FF) || (c >= 0xFE20 && c <= 0xFE2F);
  }
  function isWide(c) {
    return (c >= 0x1100 && c <= 0x115F) || (c >= 0x2E80 && c <= 0xA4CF) || (c >= 0xAC00 && c <= 0xD7A3) ||
           (c >= 0xF900 && c <= 0xFAFF) || (c >= 0xFE30 && c <= 0xFE4F) || (c >= 0xFF00 && c <= 0xFF60) ||
           (c >= 0xFFE0 && c <= 0xFFE6) || (c >= 0x1F300 && c <= 0x1FAFF) || (c >= 0x20000 && c <= 0x3FFFD);
  }
  function textUnits(s) {
    let u = 0;
    for (const ch of String(s)) {
      const c = ch.codePointAt(0);
      if (isMark(c)) continue;
      u += isWide(c) ? 1.0 : 0.6;
    }
    return u;
  }
  function wrap(text, maxUnits) {
    const words = String(text).split(/\s+/).filter(Boolean);
    const lines = [];
    let cur = '';
    for (const w of words) {
      const cand = cur ? cur + ' ' + w : w;
      if (!cur || textUnits(cand) <= maxUnits) cur = cand;
      else { lines.push(cur); cur = w; }
    }
    if (cur) lines.push(cur);
    return lines.length ? lines : [''];
  }

  // ---- geometry helpers -------------------------------------------------------------------------
  function overlapArea(a, b) {
    const dx = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
    const dy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    return (dx > 0 && dy > 0) ? dx * dy : 0;
  }
  function inflate(b, p) { return { x: b.x - p, y: b.y - p, w: b.w + 2 * p, h: b.h + 2 * p }; }
  /** Distance between an axis-aligned rect and an axis-aligned segment (0 = touching/crossing). */
  function rectSegDist(r, a, b) {
    const sx0 = Math.min(a.x, b.x), sx1 = Math.max(a.x, b.x);
    const sy0 = Math.min(a.y, b.y), sy1 = Math.max(a.y, b.y);
    const dx = Math.max(r.x - sx1, 0, sx0 - (r.x + r.w));
    const dy = Math.max(r.y - sy1, 0, sy0 - (r.y + r.h));
    return Math.hypot(dx, dy);
  }
  /** Does a segment pass through the interior of a box (shrunk by `inset`)? */
  function segThroughBox(a, b, box, inset) {
    const r = { x: box.x + inset, y: box.y + inset, w: box.w - 2 * inset, h: box.h - 2 * inset };
    if (r.w <= 0 || r.h <= 0) return false;
    const sx0 = Math.min(a.x, b.x), sx1 = Math.max(a.x, b.x);
    const sy0 = Math.min(a.y, b.y), sy1 = Math.max(a.y, b.y);
    if (sx0 === sx1) return sx0 > r.x && sx0 < r.x + r.w && sy1 > r.y && sy0 < r.y + r.h;
    if (sy0 === sy1) return sy0 > r.y && sy0 < r.y + r.h && sx1 > r.x && sx0 < r.x + r.w;
    // general segment: sample (diagonals only exist when orthogonal routing is off)
    for (let i = 0; i <= 64; i++) {
      const t = i / 64, x = a.x + (b.x - a.x) * t, y = a.y + (b.y - a.y) * t;
      if (x > r.x && x < r.x + r.w && y > r.y && y < r.y + r.h) return true;
    }
    return false;
  }
  function simplify(pts) {
    const out = [];
    for (const p of pts) {
      const q = out[out.length - 1];
      if (q && Math.abs(q.x - p.x) < 0.01 && Math.abs(q.y - p.y) < 0.01) continue;
      out.push({ x: p.x, y: p.y });
    }
    // drop collinear middle points
    let changed = true;
    while (changed && out.length > 2) {
      changed = false;
      for (let i = 1; i < out.length - 1; i++) {
        const a = out[i - 1], b = out[i], c = out[i + 1];
        if ((Math.abs(a.x - b.x) < 0.01 && Math.abs(b.x - c.x) < 0.01) ||
            (Math.abs(a.y - b.y) < 0.01 && Math.abs(b.y - c.y) < 0.01)) {
          out.splice(i, 1); changed = true; break;
        }
      }
    }
    return out;
  }
  function segments(pts) {
    const s = [];
    for (let i = 0; i < pts.length - 1; i++) s.push([pts[i], pts[i + 1], i]);
    return s;
  }

  // ---- input normalisation -----------------------------------------------------------------
  function normalise(graph, opts) {
    if (!graph || !Array.isArray(graph.nodes) || !graph.nodes.length) {
      throw new Error('layout(): graph.nodes must be a non-empty array of {id, label?, w?, h?, kind?, group?}');
    }
    const kit = opts.kit || null;
    const kitNode = (kit && kit.node) || {};
    const fontSize = num(opts.fontSize, num(kitNode.text_size, 14));
    const floor = num(kit && kit.min_legible_px, 0);
    const labelSize = num(opts.labelSize, Math.max(10, floor, Math.round(fontSize * 0.8)));
    const lineH = 1.25;
    const pad = num(opts.nodePad, ceilTo(fontSize * 0.85, 2));
    const maxW = num(opts.maxNodeWidth, ceilTo(fontSize * 16, 8));
    const minW = num(opts.minNodeWidth, ceilTo(fontSize * 6, 8));
    const warnings = [];
    const ids = new Set();
    const nodes = graph.nodes.map((n, i) => {
      if (!n || n.id === undefined || n.id === null || String(n.id) === '') throw new Error(`layout(): node #${i} has no id`);
      const id = String(n.id);
      if (ids.has(id)) throw new Error(`layout(): duplicate node id '${id}'`);
      ids.add(id);
      const label = (n.label !== undefined && n.label !== null) ? String(n.label) : id;
      const lines = wrap(label, Math.max(1, (maxW - 2 * pad) / fontSize));
      const textW = Math.max.apply(null, lines.map(textUnits)) * fontSize;
      const textH = lines.length * fontSize * lineH;
      let w, h;
      if (num(n.w, 0) > 0) {
        w = ceilTo(n.w, 8);
        if (textW + 2 * pad > w + 0.5) warnings.push(`node '${id}': label is wider than the given w=${n.w}`);
      } else w = ceilTo(Math.max(minW, textW + 2 * pad), 8);
      if (num(n.h, 0) > 0) h = ceilTo(n.h, 8);
      else h = ceilTo(Math.max(fontSize * 2.8, textH + 2 * Math.max(8, pad * 0.75)), 8);
      return { id, label, lines, w, h, kind: n.kind || null, group: (n.group !== undefined && n.group !== null) ? String(n.group) : null,
               accent: !!n.accent, idx: i };
    });
    const byId = new Map(nodes.map(n => [n.id, n]));

    // groups (containment). Undeclared groups referenced by a node are created (with a warning).
    const groups = [];
    const gById = new Map();
    for (const [i, g] of (Array.isArray(graph.groups) ? graph.groups : []).entries()) {
      if (!g || g.id === undefined || g.id === null) throw new Error(`layout(): group #${i} has no id`);
      const id = String(g.id);
      if (gById.has(id) || byId.has(id)) throw new Error(`layout(): duplicate id '${id}' (group ids and node ids share one namespace)`);
      const gr = { id, label: g.label !== undefined && g.label !== null ? String(g.label) : id,
                   parent: (g.parent !== undefined && g.parent !== null) ? String(g.parent) : null, idx: i };
      groups.push(gr); gById.set(id, gr);
    }
    for (const n of nodes) {
      if (n.group && !gById.has(n.group)) {
        if (byId.has(n.group)) throw new Error(`layout(): node '${n.id}' is grouped under node '${n.group}' — a group must be a group, not a node`);
        const gr = { id: n.group, label: n.group, parent: null, idx: groups.length };
        groups.push(gr); gById.set(gr.id, gr);
        warnings.push(`group '${n.group}' was referenced by node '${n.id}' but not declared; created with its id as label`);
      }
    }
    for (const g of groups) {
      if (g.parent && !gById.has(g.parent)) throw new Error(`layout(): group '${g.id}' has unknown parent '${g.parent}'`);
    }
    const groupOrder = topoGroups(groups, gById);
    const usedGroups = new Set(nodes.filter(n => n.group).map(n => n.group));
    for (const g of groupOrder.slice().reverse()) if (usedGroups.has(g.id) && g.parent) usedGroups.add(g.parent);
    for (const g of groups) if (!usedGroups.has(g.id)) warnings.push(`group '${g.id}' contains no nodes and is not drawn`);

    const edges = (Array.isArray(graph.edges) ? graph.edges : []).map((e, i) => {
      if (!e) throw new Error(`layout(): edge #${i} is empty`);
      const from = String(e.from), to = String(e.to);
      if (!byId.has(from)) throw new Error(`layout(): edge #${i} comes from unknown node '${e.from}'`);
      if (!byId.has(to)) throw new Error(`layout(): edge #${i} goes to unknown node '${e.to}'`);
      const label = (e.label !== undefined && e.label !== null && String(e.label) !== '') ? String(e.label) : null;
      const lw = label ? Math.ceil(textUnits(label) * labelSize + 12) : 0;
      const lh = label ? Math.ceil(labelSize * 1.5) : 0;
      return { id: e.id !== undefined && e.id !== null ? String(e.id) : 'e' + i, from, to, label, lw, lh,
               kind: e.kind || null, dashed: !!e.dashed, minlen: Math.max(1, Math.round(num(e.minlen, 1))),
               weight: Math.max(0, num(e.weight, 1)), idx: i };
    });
    const eids = new Set();
    for (const e of edges) { if (eids.has(e.id)) throw new Error(`layout(): duplicate edge id '${e.id}'`); eids.add(e.id); }
    return { nodes, byId, groups: groupOrder.filter(g => usedGroups.has(g.id)), gById, edges, warnings,
             style: { fontSize, labelSize, lineHeight: lineH, pad } };
  }

  /** Depth-first search in input order; an edge into a node still on the stack closes a cycle. */
  function markCycles(N) {
    const out = new Map(N.nodes.map(n => [n.id, []]));
    for (const e of N.edges) if (e.from !== e.to) out.get(e.from).push(e);
    const state = new Map();
    for (const root of N.nodes) {
      if (state.get(root.id)) continue;
      const stack = [[root.id, 0]];
      state.set(root.id, 1);
      while (stack.length) {
        const top = stack[stack.length - 1];
        const list = out.get(top[0]);
        if (top[1] >= list.length) { state.set(top[0], 2); stack.pop(); continue; }
        const e = list[top[1]++];
        const s = state.get(e.to);
        if (s === 1) e.reversed = true;
        else if (!s) { state.set(e.to, 1); stack.push([e.to, 0]); }
      }
    }
  }

  /** Topological order of the containment graph (outer groups first; input order breaks ties). */
  function topoGroups(groups, gById) {
    const out = [], state = new Map();
    function visit(g, chain) {
      const s = state.get(g.id);
      if (s === 2) return;
      if (s === 1) throw new Error(`layout(): group containment cycle: ${chain.concat(g.id).join(' -> ')}`);
      state.set(g.id, 1);
      if (g.parent) visit(gById.get(g.parent), chain.concat(g.id));
      state.set(g.id, 2);
      out.push(g);
    }
    for (const g of groups) visit(g, []);
    return out;
  }

  // ---- one layout attempt in "TB space" (ranks run downwards) ------------------------------
  function attempt(lib, N, P) {
    const T = P.transposed;
    const tbW = n => Math.max(T ? n.h : n.w, n.sideNeed || 0), tbH = n => (T ? n.w : n.h);
    const hasGroups = N.groups.length > 0;
    const dg = new lib.graphlib.Graph({ compound: hasGroups, multigraph: true });
    dg.setGraph({ rankdir: 'TB', nodesep: P.nodesep, ranksep: P.ranksep, edgesep: P.edgesep, marginx: 0, marginy: 0,
                  ranker: P.ranker });
    dg.setDefaultEdgeLabel(() => ({}));
    for (const g of N.groups) dg.setNode('g:' + g.id, {});
    for (const n of N.nodes) dg.setNode('n:' + n.id, { width: tbW(n), height: tbH(n) });
    for (const g of N.groups) if (g.parent) dg.setParent('g:' + g.id, 'g:' + g.parent);
    for (const n of N.nodes) if (n.group) dg.setParent('n:' + n.id, 'g:' + n.group);
    for (const e of N.edges) {
      if (e.from === e.to) {
        // the library reserves room beside the box for a self-loop; we route it ourselves
        dg.setEdge('n:' + e.from, 'n:' + e.to, { width: RULES.SELF_LOOP + RULES.LABEL_GAP + (e.label ? (T ? e.lh : e.lw) : 0),
                                                 height: e.label ? (T ? e.lw : e.lh) : 0 }, e.id);
        continue;
      }
      const lab = e.label ? { width: (T ? e.lh : e.lw) + RULES.LABEL_GAP, height: (T ? e.lw : e.lh), labelpos: 'r', labeloffset: RULES.LABEL_GAP } : {};
      // edges that close a cycle (input-order depth-first search) are handed over reversed, so
      // the ranking follows the order the author wrote the graph in
      const [v, w] = e.reversed ? [e.to, e.from] : [e.from, e.to];
      dg.setEdge('n:' + v, 'n:' + w, Object.assign({ minlen: e.minlen, weight: e.weight }, lab), e.id);
    }
    lib.layout(dg);

    // boxes, snapped: centre on a 4 px grid, sizes are multiples of 8 -> origins on the grid
    const box = new Map();
    for (const n of N.nodes) {
      const d = dg.node('n:' + n.id);
      const cx = roundTo(d.x, RULES.GRID), cy = roundTo(d.y, RULES.GRID);
      box.set(n.id, { x: cx - tbW(n) / 2, y: cy - tbH(n) / 2, w: tbW(n), h: tbH(n), rank: d.rank, cx, cy });
    }
    const gbox = new Map();
    for (const g of N.groups) {
      const d = dg.node('g:' + g.id);
      if (d && isFinite(d.x) && isFinite(d.width)) gbox.set(g.id, { x: d.x - d.width / 2, y: d.y - d.height / 2, w: d.width, h: d.height });
    }

    // layers by rank
    const ranks = Array.from(new Set(N.nodes.map(n => box.get(n.id).rank))).sort((a, b) => a - b);
    const layerOf = new Map();
    const bands = ranks.map(() => ({ top: Infinity, bottom: -Infinity, nodes: [] }));
    for (const n of N.nodes) {
      const b = box.get(n.id), li = ranks.indexOf(b.rank);
      layerOf.set(n.id, li);
      bands[li].top = Math.min(bands[li].top, b.y);
      bands[li].bottom = Math.max(bands[li].bottom, b.y + b.h);
      bands[li].nodes.push(n.id);
    }
    const L = bands.length;
    // gap g (g = -1 .. L-1) lies between layer g and layer g+1; the two outer gaps are virtual
    const gaps = new Map();
    for (let g = -1; g < L; g++) gaps.set(g, { idx: g, top: g >= 0 ? bands[g].bottom : null, bottom: g + 1 < L ? bands[g + 1].top : null, jogs: [] });

    const warnings = [];
    const rerouted = [];
    const E = N.edges.map(e => ({ e, kind: null }));

    // classify
    for (const R of E) {
      const e = R.e, lu = layerOf.get(e.from), lv = layerOf.get(e.to);
      R.lu = lu; R.lv = lv;
      R.kind = e.from === e.to ? 'self' : (lv > lu ? 'forward' : (lv < lu ? 'back' : 'flat'));
      const d = R.kind === 'self' ? null : (e.reversed ? dg.edge({ v: 'n:' + e.to, w: 'n:' + e.from, name: e.id })
                                                        : dg.edge({ v: 'n:' + e.from, w: 'n:' + e.to, name: e.id }));
      R.dpts = (d && d.points) || [];
    }

    // pass-through lanes for long forward edges (from the library's dummy positions), re-routed
    // away from any box they would pass behind
    const taken = new Map();   // layer -> [x...]
    for (const R of E) {
      R.pass = [];
      if (R.kind !== 'forward' || R.lv - R.lu < 2) continue;
      const sb = box.get(R.e.from);
      for (let j = R.lu + 1; j < R.lv; j++) {
        const mid = (bands[j].top + bands[j].bottom) / 2;
        let x;
        if (P.passThrough === 'straight') x = sb.cx;
        else {
          let best = null;
          for (const p of R.dpts) if (p.y >= bands[j].top - 1 && p.y <= bands[j].bottom + 1 && (best === null || Math.abs(p.y - mid) < Math.abs(best.y - mid))) best = p;
          x = best ? best.x : sb.cx;
        }
        x = roundTo(x, 2);
        let nx = x;
        if (P.reroute !== false) nx = freeLane(x, bands[j].nodes.map(id => box.get(id)), taken.get(j) || []);
        if (Math.abs(nx - x) > 0.01) {
          const hitBox = bands[j].nodes.some(id => { const b = box.get(id); return x > b.x && x < b.x + b.w; });
          rerouted.push({ edge: R.e.id, layer: j, from_x: x, to_x: nx, reason: hitBox ? 'would pass behind an unrelated box' : 'kept 12 px from a neighbouring lane or box' });
        }
        if (!taken.has(j)) taken.set(j, []);
        taken.get(j).push(nx);
        R.pass.push(nx);
      }
    }

    // attachment points: L*k/(N+1) along each side, ordered by where the connector heads
    const sides = new Map();
    const BIG = 1e7;
    function attach(nodeId, side, R, end, key) {
      const k = nodeId + '|' + side;
      if (!sides.has(k)) sides.set(k, []);
      sides.get(k).push({ R, end, key, sub: R.e.idx * 2 + (end === 'src' ? 0 : 1) });
    }
    for (const R of E) {
      const e = R.e;
      if (R.kind === 'self') { attach(e.from, 'right', R, 'src', -1); attach(e.to, 'right', R, 'dst', 0); continue; }
      const bv = box.get(e.to), bu = box.get(e.from);
      if (R.kind === 'forward') {
        attach(e.from, 'bottom', R, 'src', R.pass.length ? R.pass[0] : bv.cx);
        attach(e.to, 'top', R, 'dst', R.pass.length ? R.pass[R.pass.length - 1] : bu.cx);
      } else if (R.kind === 'back') {
        attach(e.from, 'bottom', R, 'src', BIG + e.idx);
        attach(e.to, 'top', R, 'dst', BIG + e.idx);
      } else {
        attach(e.from, 'bottom', R, 'src', bv.cx);
        attach(e.to, 'bottom', R, 'dst', bu.cx);
      }
    }
    for (const [k, list] of Array.from(sides.entries()).sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))) {
      const [nodeId, side] = k.split('|');
      const b = box.get(nodeId);
      list.sort((a, c) => (a.key - c.key) || (a.sub - c.sub));
      const n = list.length;
      const len = (side === 'right' || side === 'left') ? b.h : b.w;
      if (len / (n + 1) < RULES.PARALLEL_MIN || len / (n + 1) < RULES.CORNER_CLEAR) {
        warnings.push(`node '${nodeId}': ${n} connectors on one side leave ${r1(len / (n + 1))} px between ports (< ${RULES.PARALLEL_MIN})`);
      }
      list.forEach((a, i) => {
        const off = len * (i + 1) / (n + 1);
        let p;
        if (side === 'bottom') p = { x: b.x + off, y: b.y + b.h };
        else if (side === 'top') p = { x: b.x + off, y: b.y };
        else p = { x: b.x + b.w, y: b.y + off };
        if (a.end === 'src') a.R.start = p; else a.R.end = p;
      });
    }

    // straighten pass-through lanes onto a port when that lane is free in every layer it crosses
    for (const R of E) {
      if (R.kind !== 'forward' || !R.pass.length || P.passThrough === 'straight' || P.reroute === false) continue;
      for (const cand of [R.end.x, R.start.x]) {
        if (R.pass.every(x => Math.abs(x - cand) < 0.01)) break;
        let free = true;
        for (let k = 0; k < R.pass.length && free; k++) {
          const j = R.lu + 1 + k;
          const others = (taken.get(j) || []).filter((x, i, arr) => !(Math.abs(x - R.pass[k]) < 0.01 && arr.indexOf(x) === i));
          if (Math.abs(freeLane(cand, bands[j].nodes.map(id => box.get(id)), others) - cand) > 0.01) free = false;
        }
        if (!free) continue;
        for (let k = 0; k < R.pass.length; k++) {
          const j = R.lu + 1 + k, lanes = taken.get(j), at = lanes.findIndex(x => Math.abs(x - R.pass[k]) < 0.01);
          if (at >= 0) lanes[at] = cand;
          R.pass[k] = cand;
        }
        break;
      }
    }

    // back-edge channels, outside everything they pass
    let backK = 0;
    for (const R of E) {
      if (R.kind !== 'back') continue;
      let maxR = -Infinity;
      for (let j = R.lv; j <= R.lu; j++) for (const id of bands[j].nodes) { const b = box.get(id); maxR = Math.max(maxR, b.x + b.w); }
      for (const [j, xs] of taken) if (j >= R.lv && j <= R.lu) for (const x of xs) maxR = Math.max(maxR, x);
      for (const gb of gbox.values()) maxR = Math.max(maxR, gb.x + gb.w);
      R.channel = roundTo(maxR + RULES.CHANNEL_GAP + RULES.TRACK_STEP * backK, 2);
      backK += 1;
    }

    // jogs: one horizontal run per gap an edge crosses
    for (const R of E) {
      R.jogs = [];
      if (R.kind === 'self') continue;
      let xs, gl, spans;
      if (R.kind === 'forward') {
        xs = [R.start.x].concat(R.pass, [R.end.x]);
        gl = []; for (let g = R.lu; g < R.lv; g++) gl.push(g);
        spans = gl.map(() => ['up', 'down']);
      } else if (R.kind === 'back') {
        xs = [R.start.x, R.channel, R.end.x];
        gl = [R.lu, R.lv - 1];
        spans = [['up', 'up'], ['down', 'down']];
      } else {
        xs = [R.start.x, R.end.x];
        gl = [R.lu];
        spans = [['up', 'up']];
      }
      for (let k = 0; k < gl.length; k++) {
        const jog = { R, k, gap: gl[k], xFrom: xs[k], xTo: xs[k + 1], from: spans[k][0], to: spans[k][1],
                      straight: Math.abs(xs[k] - xs[k + 1]) < 0.5 && spans[k][0] !== spans[k][1], track: 0, y: null };
        R.jogs.push(jog);
        gaps.get(gl[k]).jogs.push(jog);
      }
    }

    // order and assign tracks in each gap
    let shortfall = 0;
    for (const gp of Array.from(gaps.values())) {
      const J = gp.jogs.filter(j => !j.straight);
      const T2 = assignTracks(J);
      const need = RULES.TRACK_STEP * (T2 + 1);
      if (gp.top === null && gp.bottom === null) continue;
      if (gp.top === null) gp.top = gp.bottom - Math.max(need, RULES.TRACK_STEP * 2);
      else if (gp.bottom === null) gp.bottom = gp.top + Math.max(need, RULES.TRACK_STEP * 2);
      const h = gp.bottom - gp.top;
      if (T2 > 0 && h < need) shortfall = Math.max(shortfall, need - h);
      for (const j of J) j.y = roundTo(gp.top + h * j.track / (T2 + 1), 0.5);
    }

    // assemble the routes
    for (const R of E) {
      const e = R.e;
      if (R.kind === 'self') {
        const p1 = R.start, p2 = R.end, x = p1.x + RULES.SELF_LOOP;
        R.points = [p1, { x, y: p1.y }, { x, y: p2.y }, p2];
        continue;
      }
      const pts = [R.start];
      let x = R.start.x;
      for (const j of R.jogs) {
        if (j.straight) continue;
        pts.push({ x, y: j.y });
        pts.push({ x: j.xTo, y: j.y });
        x = j.xTo;
      }
      pts.push({ x, y: R.end.y });
      if (Math.abs(x - R.end.x) > 0.01) {
        // final straight run must arrive on the port; all jogs end on the port x, so this only
        // happens when every jog was straight but ports differ (cannot: straight means equal)
        pts.splice(pts.length - 1, 0, { x, y: (pts[pts.length - 1].y + R.end.y) / 2 }, { x: R.end.x, y: (pts[pts.length - 1].y + R.end.y) / 2 });
      }
      pts[pts.length - 1] = { x: R.end.x, y: R.end.y };
      R.points = simplify(pts);
      void e;
    }

    // residual collisions with unrelated boxes (reported; the gate judges them independently)
    for (const R of E) {
      for (const [a, b] of segments(R.points)) {
        for (const n of N.nodes) {
          if (n.id === R.e.from || n.id === R.e.to) continue;
          if (segThroughBox(a, b, box.get(n.id), 1)) warnings.push(`edge '${R.e.id}' passes behind node '${n.id}'`);
        }
      }
    }

    // hops at crossings
    const hops = computeHops(E, P.cornerRadius, P.arrowSize);

    // labels
    const placed = [];
    let labelFail = 0;
    for (const R of E) {
      if (!R.e.label) { R.labelBox = null; continue; }
      const lw = T ? R.e.lh : R.e.lw, lh = T ? R.e.lw : R.e.lh;
      const best = placeLabel(R, lw, lh, E, N, box, placed, P);
      if (!best.valid) labelFail = Math.max(labelFail, lh + RULES.LABEL_GAP * 2);
      R.labelBox = best.rect; R.labelSide = best.side; R.labelValid = best.valid;
      placed.push(best.rect);
    }
    return { ok: shortfall <= 0 && labelFail <= 0, shortfall, labelFail, E, box, gbox, hops, warnings, rerouted, bands };
  }

  /** Nearest x that keeps LANE_CLEAR from every box in the layer and from every lane taken. */
  function freeLane(x, boxes, lanes) {
    const iv = boxes.map(b => [b.x - RULES.LANE_CLEAR, b.x + b.w + RULES.LANE_CLEAR])
      .concat(lanes.map(t => [t - RULES.TRACK_STEP, t + RULES.TRACK_STEP]));
    const inside = c => iv.some(([lo, hi]) => c > lo + 1e-6 && c < hi - 1e-6);
    if (!inside(x)) return x;
    const cand = [];
    for (const [lo, hi] of iv) cand.push(lo, hi);
    cand.sort((a, b) => (Math.abs(a - x) - Math.abs(b - x)) || (a - b));
    for (const c of cand) if (!inside(c)) return roundTo(c, 2);
    return x;
  }

  /**
   * Order the horizontal runs of one gap and give each a track (1 = top). Hard rule: a run whose
   * vertical rises from the TOP of the gap within 12 px of another run's vertical that descends to
   * the BOTTOM must sit above it, or the two verticals would run side by side. Soft rule: fewer
   * crossings. Runs that do not overlap horizontally may share a track.
   */
  function assignTracks(J) {
    if (!J.length) return 0;
    const n = J.length;
    const verts = j => [{ x: j.xFrom, span: j.from }, { x: j.xTo, span: j.to }];
    const lo = j => Math.min(j.xFrom, j.xTo), hi = j => Math.max(j.xFrom, j.xTo);
    const before = Array.from({ length: n }, () => new Set());   // before[b] = runs that must be above b
    for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) {
      if (a === b) continue;
      for (const va of verts(J[a])) for (const vb of verts(J[b])) {
        if (Math.abs(va.x - vb.x) < RULES.PARALLEL_MIN && va.span === 'up' && vb.span === 'down') before[b].add(a);
      }
    }
    const inRange = (x, j) => x > lo(j) + 0.5 && x < hi(j) - 0.5;
    const crossIfAbove = (A, B) => {       // crossings when A runs above B
      let c = 0;
      for (const v of verts(A)) if (v.span === 'down' && inRange(v.x, B)) c++;
      for (const v of verts(B)) if (v.span === 'up' && inRange(v.x, A)) c++;
      return c;
    };
    const key = j => [(j.xFrom + j.xTo) / 2, j.R.e.idx, j.k];
    const cmpKey = (a, b) => { const ka = key(J[a]), kb = key(J[b]); return (ka[0] - kb[0]) || (ka[1] - kb[1]) || (ka[2] - kb[2]); };
    const done = new Set(), order = [];
    while (order.length < n) {
      let avail = [];
      for (let i = 0; i < n; i++) if (!done.has(i) && Array.from(before[i]).every(p => done.has(p))) avail.push(i);
      if (!avail.length) {   // a cycle of hard rules cannot all hold; break it deterministically
        avail = [];
        for (let i = 0; i < n; i++) if (!done.has(i)) avail.push(i);
      }
      let pick = null, pickScore = null;
      for (const a of avail.sort(cmpKey)) {
        let s = 0;
        for (const b of avail) if (b !== a) s += crossIfAbove(J[b], J[a]) - crossIfAbove(J[a], J[b]);
        if (pickScore === null || s > pickScore) { pick = a; pickScore = s; }
      }
      done.add(pick); order.push(pick);
    }
    let maxT = 0;
    const placedRuns = [];
    for (const i of order) {
      const A = J[i];
      let t = 1;
      for (const p of before[i]) if (J[p].track) t = Math.max(t, J[p].track + 1);
      for (const Q of placedRuns) {
        const overlap = lo(A) - RULES.PARALLEL_MIN < hi(Q) && lo(Q) - RULES.PARALLEL_MIN < hi(A);
        if (overlap) t = Math.max(t, Q.track + 1);
      }
      A.track = t; maxT = Math.max(maxT, t);
      placedRuns.push(A);
    }
    return maxT;
  }

  /** Every proper crossing of a horizontal and a vertical run of two different edges gets a hop. */
  function computeHops(E, cornerR, arrowLen) {
    const hops = [];
    const segInfo = R => segments(R.points).map(([a, b, i]) => {
      const horiz = Math.abs(a.y - b.y) < 0.01, vert = Math.abs(a.x - b.x) < 0.01;
      const startR = i === 0 ? 0 : cornerR;
      const endR = i === R.points.length - 2 ? arrowLen : cornerR;
      return { a, b, i, horiz, vert, startR, endR };
    });
    const infos = E.map(R => segInfo(R));
    for (let A = 0; A < E.length; A++) for (let B = 0; B < E.length; B++) {
      if (A === B) continue;
      for (const s of infos[A]) {
        if (!s.horiz) continue;
        for (const v of infos[B]) {
          if (!v.vert) continue;
          const x = v.a.x, y = s.a.y;
          const hx0 = Math.min(s.a.x, s.b.x), hx1 = Math.max(s.a.x, s.b.x);
          const vy0 = Math.min(v.a.y, v.b.y), vy1 = Math.max(v.a.y, v.b.y);
          if (!(x > hx0 + 1 && x < hx1 - 1 && y > vy0 + 1 && y < vy1 - 1)) continue;
          // room on each candidate segment, measured to the drawable ends
          const dirH = Math.sign(s.b.x - s.a.x);
          const roomH = Math.min(dirH > 0 ? x - s.a.x - s.startR : s.a.x - x - s.startR,
                                 dirH > 0 ? s.b.x - x - s.endR : x - s.b.x - s.endR);
          const dirV = Math.sign(v.b.y - v.a.y);
          const roomV = Math.min(dirV > 0 ? y - v.a.y - v.startR : v.a.y - y - v.startR,
                                 dirV > 0 ? v.b.y - y - v.endR : y - v.b.y - v.endR);
          let onEdge = E[A].e.id, over = E[B].e.id, axis = 'h', seg = s.i, room = roomH;
          if (roomH < RULES.HOP_R + 2 && roomV > roomH) { onEdge = E[B].e.id; over = E[A].e.id; axis = 'v'; seg = v.i; room = roomV; }
          const r = Math.max(2, Math.min(RULES.HOP_R, room - 1));
          hops.push({ x, y, edge: onEdge, over, axis, seg, r });
        }
      }
    }
    // stable, de-duplicated
    const seen = new Set(), out = [];
    for (const h of hops.sort((p, q) => (p.y - q.y) || (p.x - q.x) || (p.edge < q.edge ? -1 : p.edge > q.edge ? 1 : 0))) {
      const k = `${r1(h.x)},${r1(h.y)}`;
      if (seen.has(k)) continue;
      seen.add(k); out.push(h);
    }
    return out;
  }

  /** Candidate label positions along the edge's own segments; the first that breaks no rule wins. */
  function placeLabel(R, lw, lh, E, N, box, placed, P) {
    const segs = segments(R.points).map(([a, b, i]) => ({ a, b, i, len: Math.abs(a.x - b.x) + Math.abs(a.y - b.y) }));
    // nearest the source first: a branch label reads best right after its fork
    const order = segs.slice();
    const G = RULES.LABEL_GAP;
    let best = null;
    const score = (rect, segIdx) => {
      let bad = 0;
      for (const n of N.nodes) if (overlapArea(rect, inflate(box.get(n.id), RULES.LABEL_CLEAR)) > 0) bad += 10;
      for (const o of placed) if (overlapArea(rect, inflate(o, RULES.LABEL_CLEAR)) > 0) bad += 10;
      for (const Q of E) {
        if (!Q.points) continue;
        for (const [a, b, i] of segments(Q.points)) {
          const d = rectSegDist(rect, a, b);
          if (Q === R) { if (i !== segIdx && d < RULES.LABEL_GAP_MIN) bad += 3; }
          else if (d < RULES.LABEL_CLEAR) bad += 5;
        }
      }
      return bad;
    };
    if (R.kind === 'self') {
      const outer = R.points[1].x, ym = (R.points[1].y + R.points[2].y) / 2;
      const c = [{ rect: { x: outer + G, y: ym - lh / 2, w: lw, h: lh }, side: 'right', seg: 1 },
                 { rect: { x: outer - lw / 2 - RULES.SELF_LOOP / 2, y: R.points[1].y - G - lh, w: lw, h: lh }, side: 'above', seg: 0 }];
      for (const k of c) {
        const bad = score(k.rect, k.seg);
        if (bad === 0) return { rect: k.rect, side: k.side, valid: true };
        if (!best || bad < best.bad) best = { rect: k.rect, side: k.side, valid: false, bad };
      }
    }
    for (const s of order) {
      const vertical = Math.abs(s.a.x - s.b.x) < 0.01;
      const cands = [];
      if (vertical) {
        const y0 = Math.min(s.a.y, s.b.y) + 2, y1 = Math.max(s.a.y, s.b.y) - 2;
        if (y1 - y0 < lh) continue;
        const mid = (y0 + y1) / 2 - lh / 2;
        for (const side of ['right', 'left']) {
          const x = side === 'right' ? s.a.x + G : s.a.x - G - lw;
          for (let k = 0; k * 8 <= (y1 - y0 - lh) / 2 + 0.01; k++) {
            for (const sg of (k ? [1, -1] : [0])) {
              const y = mid + sg * k * 8;
              if (y < y0 - 0.01 || y + lh > y1 + 0.01) continue;
              cands.push({ rect: { x, y, w: lw, h: lh }, side });
            }
          }
        }
      } else {
        const x0 = Math.min(s.a.x, s.b.x) + 2, x1 = Math.max(s.a.x, s.b.x) - 2;
        if (x1 - x0 < lw) continue;
        const mid = (x0 + x1) / 2 - lw / 2;
        for (const side of ['above', 'below']) {
          const y = side === 'above' ? s.a.y - G - lh : s.a.y + G;
          for (let k = 0; k * 8 <= (x1 - x0 - lw) / 2 + 0.01; k++) {
            for (const sg of (k ? [1, -1] : [0])) {
              const x = mid + sg * k * 8;
              if (x < x0 - 0.01 || x + lw > x1 + 0.01) continue;
              cands.push({ rect: { x, y, w: lw, h: lh }, side });
            }
          }
        }
      }
      for (const c of cands) {
        const bad = score(c.rect, s.i);
        if (bad === 0) return { rect: c.rect, side: c.side, valid: true };
        if (!best || bad < best.bad) best = { rect: c.rect, side: c.side, valid: false, bad };
      }
    }
    if (best) return best;
    // no segment long enough: put it beside the longest segment's midpoint
    const s = order[0];
    const mx = (s.a.x + s.b.x) / 2, my = (s.a.y + s.b.y) / 2;
    const vertical = Math.abs(s.a.x - s.b.x) < 0.01;
    return { rect: vertical ? { x: mx + G, y: my - lh / 2, w: lw, h: lh } : { x: mx - lw / 2, y: my - G - lh, w: lw, h: lh },
             side: vertical ? 'right' : 'above', valid: false };
  }

  // ---- the public layout() --------------------------------------------------------------------
  function layout(graph, opts) {
    opts = opts || {};
    const lib = findDagre(opts);
    const N = normalise(graph, opts);
    markCycles(N);
    // a side carrying N connectors needs (N+1) * TRACK_STEP so the ports stay >= 12 px apart
    const outs = new Map(), ins = new Map();
    for (const e of N.edges) {
      if (e.from === e.to) continue;
      outs.set(e.from, (outs.get(e.from) || 0) + 1);
      ins.set(e.to, (ins.get(e.to) || 0) + 1);
    }
    for (const n of N.nodes) n.sideNeed = ceilTo((Math.max(outs.get(n.id) || 0, ins.get(n.id) || 0) + 1) * RULES.TRACK_STEP, 8);
    const rankdir = String(opts.rankdir || graph.rankdir || 'TB').toUpperCase();
    if (!['TB', 'BT', 'LR', 'RL'].includes(rankdir)) throw new Error(`layout(): rankdir must be TB, BT, LR or RL (got ${rankdir})`);
    const transposed = rankdir === 'LR' || rankdir === 'RL';
    const kitConn = (opts.kit && opts.kit.connector) || {};
    const orthogonal = opts.orthogonal !== undefined ? !!opts.orthogonal : true;
    const curved = (opts.routing || kitConn.routing) === 'curved';
    const cornerRadius = num(opts.cornerRadius, curved ? 16 : 8);
    const arrowSize = num(opts.arrowSize, Math.max(8, Math.round(N.style.fontSize * 0.6)));
    const P = {
      transposed, nodesep: num(opts.nodesep, Math.max(48, Math.round(N.style.fontSize * 3))),
      ranksep: num(opts.ranksep, Math.max(56, Math.round(N.style.fontSize * 4))), edgesep: num(opts.edgesep, 24),
      ranker: opts.ranker || 'network-simplex', passThrough: opts.passThrough || 'library', reroute: opts.reroute,
      cornerRadius, arrowSize,
    };
    if (!orthogonal) return straightLayout(lib, N, P, graph, opts, rankdir);
    let res = null, attempts = 0;
    const maxAttempts = 8;
    for (; attempts < maxAttempts; attempts++) {
      res = attempt(lib, N, P);
      if (res.ok) break;
      if (attempts === maxAttempts - 1) break;
      P.ranksep += Math.max(16, Math.ceil(res.shortfall), Math.ceil(res.labelFail));
      if (res.labelFail > 0) P.nodesep += 16;
    }
    attempts += 1;
    const warnings = N.warnings.concat(res.warnings);
    if (!res.ok) {
      if (res.shortfall > 0) warnings.push(`a gap is ${r1(res.shortfall)} px short of the space its connectors need`);
      for (const R of res.E) if (R.e.label && !R.labelValid) warnings.push(`edge '${R.e.id}': no label position clears every rule`);
    }
    return finish(N, res, P, graph, opts, rankdir, attempts, warnings, true);
  }

  /** opts.orthogonal === false: the library's own polyline, clipped to the borders. */
  function straightLayout(lib, N, P, graph, opts, rankdir) {
    const res = attempt(lib, N, P);
    for (const R of res.E) {
      if (R.kind === 'self') continue;
      const a = res.box.get(R.e.from), b = res.box.get(R.e.to);
      const mids = R.dpts.slice(1, -1).map(p => ({ x: p.x, y: p.y }));
      const ca = { x: a.cx, y: a.cy }, cb = { x: b.cx, y: b.cy };
      const first = mids.length ? mids[0] : cb, last = mids.length ? mids[mids.length - 1] : ca;
      R.points = simplify([borderPoint(a, ca, first)].concat(mids, [borderPoint(b, cb, last)]));
      R.labelBox = R.e.label ? { x: (R.points[0].x + R.points[R.points.length - 1].x) / 2 + RULES.LABEL_GAP,
                                 y: (R.points[0].y + R.points[R.points.length - 1].y) / 2,
                                 w: P.transposed ? R.e.lh : R.e.lw, h: P.transposed ? R.e.lw : R.e.lh } : null;
      R.labelSide = 'right';
    }
    res.hops = [];
    return finish(N, res, P, graph, opts, rankdir, 1, N.warnings.concat(res.warnings), false);
  }
  function borderPoint(b, c, toward) {
    const dx = toward.x - c.x, dy = toward.y - c.y;
    if (!dx && !dy) return { x: c.x, y: c.y };
    const s = Math.min(dx ? (b.w / 2) / Math.abs(dx) : Infinity, dy ? (b.h / 2) / Math.abs(dy) : Infinity);
    return { x: c.x + dx * s, y: c.y + dy * s };
  }

  /** TB space -> the requested direction, shifted onto a margin, rounded, ordered for painting. */
  function finish(N, res, P, graph, opts, rankdir, attempts, warnings, orthogonal) {
    const tp = rankdir === 'TB' ? (p => ({ x: p.x, y: p.y }))
      : rankdir === 'BT' ? (p => ({ x: p.x, y: -p.y }))
      : rankdir === 'LR' ? (p => ({ x: p.y, y: p.x }))
      : (p => ({ x: -p.y, y: p.x }));
    const trect = r => {
      const a = tp({ x: r.x, y: r.y }), b = tp({ x: r.x + r.w, y: r.y + r.h });
      return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y) };
    };
    const axisMap = a => (P.transposed ? (a === 'h' ? 'v' : 'h') : a);
    const nodes = N.nodes.map(n => Object.assign({ n }, trect(res.box.get(n.id))));
    const edges = res.E.map(R => ({ R, points: R.points.map(tp), label: R.labelBox ? trect(R.labelBox) : null }));
    const hops = res.hops.map(h => Object.assign({}, h, tp(h), { axis: axisMap(h.axis) }));
    const groups = N.groups.filter(g => res.gbox.has(g.id)).map(g => Object.assign({ g }, trect(res.gbox.get(g.id))));

    // arrowheads: tip ON the target border, body outside it
    const A = P.arrowSize, half = Math.max(3, Math.round(A * 0.45 * 10) / 10);
    for (const E of edges) {
      const pts = E.points, tip = pts[pts.length - 1], prev = pts[pts.length - 2];
      const dx = Math.sign(r1(tip.x - prev.x)), dy = Math.sign(r1(tip.y - prev.y));
      const len = Math.hypot(tip.x - prev.x, tip.y - prev.y) || 1;
      const ux = orthogonal ? dx : (tip.x - prev.x) / len, uy = orthogonal ? dy : (tip.y - prev.y) / len;
      const bx = tip.x - ux * A, by = tip.y - uy * A;
      E.arrow = { tip: { x: tip.x, y: tip.y }, dir: { x: ux, y: uy },
                  points: [{ x: tip.x, y: tip.y }, { x: bx - uy * half, y: by + ux * half }, { x: bx + uy * half, y: by - ux * half }] };
    }

    // group labels: first inside corner that clears nodes, lines and labels
    const ls = N.style.labelSize;
    for (const G of groups) {
      const lw = Math.ceil(textUnits(G.g.label) * ls + 12), lh = Math.ceil(ls * 1.5);
      const cands = [
        { x: G.x + 8, y: G.y + 4 }, { x: G.x + G.w - 8 - lw, y: G.y + 4 },
        { x: G.x + 8, y: G.y + G.h - 4 - lh }, { x: G.x + G.w - 8 - lw, y: G.y + G.h - 4 - lh }];
      let chosen = null;
      for (const c of cands) {
        const r = { x: c.x, y: c.y, w: lw, h: lh };
        let bad = false;
        for (const n of nodes) if (overlapArea(r, inflate(n, 2)) > 0) bad = true;
        for (const E of edges) {
          for (const [a, b] of segments(E.points)) if (rectSegDist(r, a, b) < RULES.LABEL_CLEAR) bad = true;
          if (E.label && overlapArea(r, inflate(E.label, 2)) > 0) bad = true;
        }
        if (!bad) { chosen = r; break; }
      }
      if (!chosen) { chosen = { x: cands[0].x, y: cands[0].y, w: lw, h: lh }; warnings.push(`group '${G.g.id}': no label corner clears every line`); }
      G.labelBox = chosen;
    }

    // bbox over everything that paints
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const grow = (x, y) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); };
    const growR = r => { grow(r.x, r.y); grow(r.x + r.w, r.y + r.h); };
    nodes.forEach(growR); groups.forEach(growR); groups.forEach(G => growR(G.labelBox));
    for (const E of edges) { E.points.forEach(p => grow(p.x, p.y)); if (E.label) growR(E.label); E.arrow.points.forEach(p => grow(p.x, p.y)); }
    for (const h of hops) { grow(h.x - h.r, h.y - h.r); grow(h.x + h.r, h.y + h.r); }
    const M = num(opts.margin, RULES.MARGIN);
    const sx = ceilTo(M - x0, RULES.GRID), sy = ceilTo(M - y0, RULES.GRID);
    const W = ceilTo(x1 + sx + M, RULES.GRID), H = ceilTo(y1 + sy + M, RULES.GRID);
    const P2 = p => ({ x: r1(p.x + sx), y: r1(p.y + sy) });
    const R2 = r => ({ x: r1(r.x + sx), y: r1(r.y + sy), w: r1(r.w), h: r1(r.h) });

    const outNodes = nodes.map(o => Object.assign({ id: o.n.id, label: o.n.label, lines: o.n.lines }, R2(o),
      { kind: o.n.kind, group: o.n.group, accent: o.n.accent }));
    const outGroups = groups.map(G => Object.assign({ id: G.g.id, label: G.g.label, parent: G.g.parent }, R2(G),
      { label_box: R2(G.labelBox) }));
    const outEdges = edges.map(E => {
      const e = E.R.e;
      let label = null;
      if (e.label && E.label) {
        const lb = R2(E.label);
        label = { text: e.label, x: lb.x, y: lb.y, w: lb.w, h: lb.h, mask: true, gap: RULES.LABEL_GAP,
                  side: E.R.labelSide || null, valid: E.R.labelValid !== false };
      }
      return { id: e.id, from: e.from, to: e.to, route: E.R.kind, kind: e.kind, dashed: e.dashed,
               points: E.points.map(P2), arrowAt: 'to',
               arrow: { tip: P2(E.arrow.tip), dir: { x: r1(E.arrow.dir.x), y: r1(E.arrow.dir.y) }, points: E.arrow.points.map(P2) },
               label };
    });
    const outHops = hops.map(h => ({ x: r1(h.x + sx), y: r1(h.y + sy), r: r1(h.r), edge: h.edge, over: h.over, axis: h.axis, seg: h.seg }));

    // paint order = topological order of the containment graph
    const paint = [{ kind: 'background', id: 'background' }];
    for (const G of groups) paint.push({ kind: 'group', id: G.g.id, parent: G.g.parent });
    for (const E of outEdges) paint.push({ kind: 'edge', id: E.id });
    for (const E of outEdges) if (E.label) paint.push({ kind: 'label', id: E.id });
    for (const n of outNodes) paint.push({ kind: 'node', id: n.id, group: n.group });
    paint.forEach((p, i) => { p.index = i; });

    return {
      schema: 1,
      engine: { name: 'coordn8-layout', version: VERSION, library: 'dagre', library_version: String((findDagre(opts).version) || '') },
      id: String(opts.id || graph.id || 'diagram'),
      type: graph.type ? String(graph.type) : null,
      title: graph.title ? String(graph.title) : null,
      desc: graph.desc ? String(graph.desc) : null,
      rankdir, orthogonal,
      style: { fontSize: N.style.fontSize, labelSize: N.style.labelSize, lineHeight: N.style.lineHeight,
               arrowSize: P.arrowSize, cornerRadius: P.cornerRadius, hopRadius: RULES.HOP_R },
      rules: RULES,
      nodes: outNodes, groups: outGroups, edges: outEdges, hops: outHops,
      paint_order: paint,
      bbox: { x: 0, y: 0, w: W, h: H },
      rerouted: res.rerouted, warnings, attempts, ranksep: P.ranksep, nodesep: P.nodesep,
    };
  }

  // ---- SVG ------------------------------------------------------------------------------------------
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  /** rgba(...) -> {hex, opacity}: strict SVG consumers paint rgba() as opaque black. */
  function paint(c, fallback) {
    const s = (typeof c === 'string' && c.trim()) ? c.trim() : fallback;
    const m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(s);
    if (m) {
      const h = [m[1], m[2], m[3]].map(v => Math.max(0, Math.min(255, Math.round(+v))).toString(16).padStart(2, '0')).join('');
      const a = m[4] === undefined ? 1 : Math.max(0, Math.min(1, +m[4]));
      return { color: '#' + h, opacity: a < 1 ? r1(a * 100) / 100 : null };
    }
    return { color: s, opacity: null };
  }
  function paintAttr(name, c, fallback) {
    const p = paint(c, fallback);
    return `${name}="${esc(p.color)}"` + (p.opacity !== null ? ` ${name}-opacity="${p.opacity}"` : '');
  }

  /** Neutral fallbacks only — every colour, stroke, radius and font otherwise comes from the kit. */
  const NEUTRAL = { paper: '#ffffff', ink: '#222222', muted: '#5c5c5c', accent: '#222222', link: '#222222',
                    font: 'system-ui, -apple-system, Segoe UI, sans-serif' };

  function kitStyle(kit) {
    const k = kit || {};
    const col = k.colors || {};
    const paper = col.paper || (k.background && k.background.color) || NEUTRAL.paper;
    const ink = col.ink || (k.text && k.text.color) || NEUTRAL.ink;
    const muted = col.muted || NEUTRAL.muted;
    const node = k.node || {}, conn = k.connector || {}, card = k.card || {};
    return {
      kit: k.kit || null, paper, ink, muted,
      accent: col.accent || NEUTRAL.accent, link: col.link || NEUTRAL.link,
      font: (k.text && k.text.font) || NEUTRAL.font,
      textColor: (k.text && k.text.color) || ink,
      nodeFill: node.fill || paper, nodeStroke: node.stroke || ink, nodeStrokeW: num(node.stroke_width, 1.5),
      nodeRadius: num(node.radius, 6), nodeWeight: (k.text && k.text.weights && k.text.weights.strong) || 600,
      connColor: conn.color || muted, connW: num(conn.width, 1.5), connDash: Array.isArray(conn.dash) ? conn.dash : [],
      arrow: conn.arrow || 'triangle', routing: conn.routing || null,
      groupStroke: card.stroke || muted, groupStrokeW: num(card.stroke_width, 1), groupRadius: num(card.radius, 8),
    };
  }

  function pathD(edge, hops, cornerR, arrowLen) {
    const pts = edge.points;
    const n = pts.length;
    const dirs = [];
    for (let i = 0; i < n - 1; i++) {
      const dx = pts[i + 1].x - pts[i].x, dy = pts[i + 1].y - pts[i].y, L = Math.hypot(dx, dy) || 1;
      dirs.push({ x: dx / L, y: dy / L, len: L });
    }
    // usable length of each segment (the last one stops at the arrowhead base)
    const avail = dirs.map((d, i) => d.len - (i === n - 2 ? arrowLen : 0));
    const radius = [];  // radius at interior point i (1..n-2)
    for (let i = 1; i < n - 1; i++) {
      const straight = Math.abs(dirs[i - 1].x * dirs[i].y - dirs[i - 1].y * dirs[i].x) < 1e-6;
      radius[i] = straight ? 0 : Math.max(0, Math.min(cornerR, dirs[i - 1].len / 2, avail[i] / 2));
    }
    let d = `M${fmt(pts[0].x)} ${fmt(pts[0].y)}`;
    for (let i = 0; i < n - 1; i++) {
      const a = pts[i], dir = dirs[i];
      const rin = i > 0 ? radius[i] : 0;
      const end = i === n - 2
        ? { x: pts[i + 1].x - dir.x * arrowLen, y: pts[i + 1].y - dir.y * arrowLen }
        : { x: pts[i + 1].x - dir.x * radius[i + 1], y: pts[i + 1].y - dir.y * radius[i + 1] };
      const start = { x: a.x + dir.x * rin, y: a.y + dir.y * rin };
      const mine = hops.filter(h => h.edge === edge.id && h.seg === i)
        .map(h => ({ h, t: (h.x - start.x) * dir.x + (h.y - start.y) * dir.y }))
        .sort((p, q) => p.t - q.t);
      const total = (end.x - start.x) * dir.x + (end.y - start.y) * dir.y;
      for (const { h, t } of mine) {
        const r = h.r;
        if (t - r < 0 || t + r > total) continue;
        const p0 = { x: start.x + dir.x * (t - r), y: start.y + dir.y * (t - r) };
        const p1 = { x: start.x + dir.x * (t + r), y: start.y + dir.y * (t + r) };
        // horizontal runs hop over the top; vertical runs hop to the right
        const sweep = Math.abs(dir.x) > 0.5 ? (dir.x > 0 ? 1 : 0) : (dir.y > 0 ? 1 : 0);
        d += ` L${fmt(p0.x)} ${fmt(p0.y)} A${fmt(r)} ${fmt(r)} 0 0 ${sweep} ${fmt(p1.x)} ${fmt(p1.y)}`;
      }
      d += ` L${fmt(end.x)} ${fmt(end.y)}`;
      if (i < n - 2 && radius[i + 1] > 0) {
        const nd = dirs[i + 1], r = radius[i + 1], c = pts[i + 1];
        const cross = dir.x * nd.y - dir.y * nd.x;
        d += ` A${fmt(r)} ${fmt(r)} 0 0 ${cross > 0 ? 1 : 0} ${fmt(c.x + nd.x * r)} ${fmt(c.y + nd.y * r)}`;
      }
    }
    return d;
  }

  function arrowSVG(edge, S, color) {
    const pts = edge.arrow.points;
    const tip = edge.arrow.tip, dir = edge.arrow.dir;
    const fillAttr = paintAttr('fill', color, S.muted);
    const kind = String(S.arrow || 'triangle');
    if (kind === 'dot') {
      const r = Math.max(2.5, Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y) / 3);
      return `<circle cx="${fmt(tip.x - dir.x * r)}" cy="${fmt(tip.y - dir.y * r)}" r="${fmt(r)}" ${fillAttr}/>`;
    }
    const poly = pts.map(p => `${fmt(p.x)},${fmt(p.y)}`).join(' ');
    if (/chevron/.test(kind)) {
      const ordered = [pts[1], pts[0], pts[2]].map(p => `${fmt(p.x)},${fmt(p.y)}`).join(' ');
      return `<polyline points="${ordered}" fill="none" ${paintAttr('stroke', color, S.muted)} stroke-width="${fmt(S.connW)}" stroke-linejoin="round" stroke-linecap="round"/>`;
    }
    return `<polygon points="${poly}" ${fillAttr}/>`;
  }

  /**
   * SVG of a layout result in the active kit (scripts/design/kit_export.py runtime JSON). Elements
   * are emitted in result.paint_order; every value is rounded, so the same result and kit always
   * give the same bytes.
   */
  function toSVG(result, kit, opts) {
    opts = opts || {};
    if (!result || !Array.isArray(result.nodes) || !Array.isArray(result.paint_order)) throw new Error('toSVG(): not a layout result');
    const S = kitStyle(kit);
    const st = result.style || {};
    const fs = num(st.fontSize, 14), ls = num(st.labelSize, 11), lhF = num(st.lineHeight, 1.25);
    const cornerR = num(st.cornerRadius, 8), arrowLen = num(st.arrowSize, 8);
    const W = result.bbox.w, H = result.bbox.h;
    const slug = String(opts.id || result.id || 'diagram').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '') || 'diagram';
    const byNode = new Map(result.nodes.map(n => [n.id, n]));
    const byEdge = new Map(result.edges.map(e => [e.id, e]));
    const byGroup = new Map((result.groups || []).map(g => [g.id, g]));
    const title = result.title || result.id || 'Diagram';
    const desc = result.desc || ('Connections: ' + result.edges.map(e => {
      const a = byNode.get(e.from), b = byNode.get(e.to);
      return `${a ? a.label : e.from} to ${b ? b.label : e.to}${e.label ? ' (' + e.label.text + ')' : ''}`;
    }).join('; ')).slice(0, 600);
    const dashAttr = (e) => {
      const dash = e.dashed ? [5, 4] : S.connDash;
      return dash && dash.length ? ` stroke-dasharray="${dash.map(fmt).join(' ')}"` : '';
    };
    const edgeColor = e => (e.kind === 'accent' ? S.accent : e.kind === 'link' ? S.link : S.connColor);
    const out = [];
    out.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${fmt(W)} ${fmt(H)}" width="${fmt(W)}" height="${fmt(H)}" ` +
             `role="img" aria-labelledby="${slug}-title ${slug}-desc" data-c8-layout="${esc(result.engine ? result.engine.version : '')}"` +
             `${S.kit ? ` data-kit="${esc(S.kit)}"` : ''}${result.type ? ` data-type="${esc(result.type)}"` : ''}>`);
    out.push(`<title id="${slug}-title">${esc(title)}</title>`);
    out.push(`<desc id="${slug}-desc">${esc(desc)}</desc>`);
    const textAttrs = (size, color, weight) => `font-family="${esc(S.font)}" font-size="${fmt(size)}"` +
      (weight ? ` font-weight="${weight}"` : '') + ` ${paintAttr('fill', color, S.ink)} text-anchor="middle"`;
    for (const p of result.paint_order) {
      const pi = p.index;
      if (p.kind === 'background') {
        out.push(`<rect data-paint="${pi}" data-c8="background" x="0" y="0" width="${fmt(W)}" height="${fmt(H)}" ${paintAttr('fill', S.paper, NEUTRAL.paper)}/>`);
      } else if (p.kind === 'group') {
        const g = byGroup.get(p.id);
        if (!g) continue;
        const lb = g.label_box;
        out.push(`<g data-paint="${pi}" data-c8="group" data-group-id="${esc(g.id)}"${g.parent ? ` data-parent="${esc(g.parent)}"` : ''}>` +
          `<rect x="${fmt(g.x)}" y="${fmt(g.y)}" width="${fmt(g.w)}" height="${fmt(g.h)}" rx="${fmt(Math.min(S.groupRadius, g.h / 2))}" ` +
          `${paintAttr('fill', S.muted, NEUTRAL.muted)} fill-opacity="0.06" ${paintAttr('stroke', S.groupStroke, S.muted)} stroke-width="${fmt(S.groupStrokeW)}" stroke-dasharray="6 4"/>` +
          (lb ? `<text x="${fmt(lb.x + lb.w / 2)}" y="${fmt(lb.y + lb.h / 2 + ls * 0.35)}" ${textAttrs(ls, S.muted, 600)}>${esc(g.label)}</text>` : '') +
          `</g>`);
      } else if (p.kind === 'edge') {
        const e = byEdge.get(p.id);
        if (!e) continue;
        const color = edgeColor(e);
        out.push(`<g data-paint="${pi}" data-c8="edge" data-edge-id="${esc(e.id)}" data-from="${esc(e.from)}" data-to="${esc(e.to)}">` +
          `<path d="${pathD(e, result.hops || [], cornerR, arrowLen)}" fill="none" ${paintAttr('stroke', color, S.muted)} stroke-width="${fmt(S.connW)}"` +
          `${dashAttr(e)} stroke-linejoin="round" stroke-linecap="butt"/>` + arrowSVG(e, S, color) + `</g>`);
      } else if (p.kind === 'label') {
        const e = byEdge.get(p.id);
        if (!e || !e.label) continue;
        const L = e.label;
        out.push(`<g data-paint="${pi}" data-c8="label" data-edge-id="${esc(e.id)}">` +
          `<rect data-c8="mask" x="${fmt(L.x)}" y="${fmt(L.y)}" width="${fmt(L.w)}" height="${fmt(L.h)}" rx="2" ${paintAttr('fill', S.paper, NEUTRAL.paper)}/>` +
          `<text x="${fmt(L.x + L.w / 2)}" y="${fmt(L.y + L.h / 2 + ls * 0.35)}" ${textAttrs(ls, S.muted, null)}>${esc(L.text)}</text></g>`);
      } else if (p.kind === 'node') {
        const n = byNode.get(p.id);
        if (!n) continue;
        const lines = n.lines && n.lines.length ? n.lines : [n.label];
        const total = lines.length * fs * lhF;
        const first = n.y + n.h / 2 - total / 2 + fs * lhF / 2 + fs * 0.35;
        const stroke = n.accent ? S.accent : S.nodeStroke;
        const texts = lines.map((ln, i) => `<text x="${fmt(n.x + n.w / 2)}" y="${fmt(first + i * fs * lhF)}" ${textAttrs(fs, S.textColor, S.nodeWeight)}>${esc(ln)}</text>`).join('');
        out.push(`<g data-paint="${pi}" data-c8="node" data-node-id="${esc(n.id)}"${n.group ? ` data-group="${esc(n.group)}"` : ''}>` +
          `<rect x="${fmt(n.x)}" y="${fmt(n.y)}" width="${fmt(n.w)}" height="${fmt(n.h)}" rx="${fmt(Math.min(S.nodeRadius, n.h / 2))}" ` +
          `${paintAttr('fill', S.nodeFill, S.paper)} ${paintAttr('stroke', stroke, S.ink)} stroke-width="${fmt(S.nodeStrokeW)}"/>` + texts + `</g>`);
      }
    }
    out.push('</svg>');
    return out.join('\n') + '\n';
  }

  /** Specs a scene can hand straight to scene_runtime.js node()/connector(). */
  function toScene(result) {
    return {
      nodes: result.nodes.map(n => ({ id: n.id, x: n.x, y: n.y, w: n.w, h: n.h, text: n.label })),
      connectors: result.edges.map(e => ({ from: e.from, to: e.to, points: e.points.map(p => ({ x: p.x, y: p.y })), arrowAt: 'to' })),
    };
  }

  /** A diagram_plan.json figure entry -> a graph (kept nodes, their names, kept edges). */
  function graphFromPlan(entry) {
    if (!entry || !Array.isArray(entry.kept)) throw new Error('graphFromPlan(): entry has no kept node list');
    const names = entry.names || {};
    return {
      id: entry.id, type: entry.type || null,
      nodes: entry.kept.map(id => ({ id: String(id), label: names[id] !== undefined ? String(names[id]) : String(id) })),
      edges: (entry.edges_kept || []).map(p => (Array.isArray(p) ? { from: String(p[0]), to: String(p[1]) }
                                                               : { from: String(p.from), to: String(p.to), label: p.label || null })),
    };
  }

  function isLayoutResult(x) {
    return !!(x && x.schema === 1 && Array.isArray(x.paint_order) && Array.isArray(x.nodes) && x.bbox);
  }

  return { VERSION, RULES, layout, toSVG, toScene, graphFromPlan, isLayoutResult, findDagre, textUnits, _internals: { wrap, assignTracks, freeLane } };
});
