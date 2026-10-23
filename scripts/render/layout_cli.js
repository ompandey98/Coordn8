#!/usr/bin/env node
/*
 * Coordn8 — layout CLI (B3). Thin wrapper over scripts/render/layout.js.
 *
 *   node layout_cli.js <graph.json> --dagre <dagre.min.js> [--kit runtime.json] [--rankdir TB|BT|LR|RL]
 *                      [--svg out.svg] [--json out.json] [--id NAME]
 *   node layout_cli.js <layout.json> [--kit runtime.json] --svg out.svg        # render an existing layout
 *   node layout_cli.js <diagram_plan.json> --figure fig-id --dagre <path> ...   # lay out a plan figure
 *
 * <graph.json> = {nodes:[{id,label,w?,h?,kind?,group?}], edges:[{from,to,label?}], groups?, type?, rankdir?}
 * --kit takes the runtime JSON from `python3 scripts/design/kit_export.py runtime <kit-or-design_system>`;
 * it supplies colours, strokes, radii and fonts (and the node text size used for sizing).
 * The dagre path may also come from $COORDN8_DAGRE. Exit codes: 0 ok, 2 missing input/library, 3 error.
 * Nothing random and no timestamps are written: the same input gives byte-identical files.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const L = require('./layout.js');

function arg(argv, name, dflt) {
  const i = argv.indexOf(name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : dflt;
}

function main(argv) {
  const pos = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) { i++; continue; }
    pos.push(argv[i]);
  }
  if (!pos.length || argv.includes('--help')) {
    process.stderr.write('usage: node layout_cli.js <graph.json|layout.json|diagram_plan.json> --dagre <path> [--kit runtime.json] [--rankdir TB] [--svg out.svg] [--json out.json] [--figure ID]\n');
    return 2;
  }
  let input;
  try { input = JSON.parse(fs.readFileSync(pos[0], 'utf8')); }
  catch (e) { process.stderr.write(`[coordn8:layout] cannot read ${pos[0]}: ${e.message}\n`); return 2; }
  let kit = null;
  const kitPath = arg(argv, '--kit');
  if (kitPath) {
    try { kit = JSON.parse(fs.readFileSync(kitPath, 'utf8')); }
    catch (e) { process.stderr.write(`[coordn8:layout] cannot read kit ${kitPath}: ${e.message}\n`); return 2; }
    if (kit && kit.props && !kit.colors) {
      process.stderr.write('[coordn8:layout] --kit expects the RUNTIME JSON: run `python3 scripts/design/kit_export.py runtime <kit>` first\n');
      return 2;
    }
  }
  let result;
  try {
    if (L.isLayoutResult(input)) {
      result = input;
    } else {
      let graph = input;
      const fig = arg(argv, '--figure');
      if (Array.isArray(input.figures)) {
        const entry = fig ? input.figures.find(f => f && f.id === fig) : input.figures[0];
        if (!entry) { process.stderr.write(`[coordn8:layout] figure '${fig}' not in ${pos[0]}\n`); return 2; }
        graph = L.graphFromPlan(entry);
      }
      const dagre = arg(argv, '--dagre', process.env.COORDN8_DAGRE || require('path').join(__dirname, 'vendor', 'dagre.min.js'));
      if (!dagre || !fs.existsSync(dagre)) {
        process.stderr.write('[coordn8:layout] SKIPPED: the vendored layout library was not found (pass --dagre <path> or set ' +
                             'COORDN8_DAGRE; `python3 scripts/render/fetch_visual_libs.py --only dagre` fetches it)\n');
        return 2;
      }
      const opts = { dagre: path.resolve(dagre), kit };
      const rd = arg(argv, '--rankdir'); if (rd) opts.rankdir = rd;
      const id = arg(argv, '--id'); if (id) opts.id = id;
      if (argv.includes('--no-reroute')) opts.reroute = false;
      if (argv.includes('--straight-lanes')) opts.passThrough = 'straight';
      result = L.layout(graph, opts);
    }
  } catch (e) {
    process.stderr.write(`[coordn8:layout] ERROR: ${e.message}\n`);
    return 3;
  }
  const jsonOut = arg(argv, '--json');
  const svgOut = arg(argv, '--svg');
  if (jsonOut) fs.writeFileSync(jsonOut, JSON.stringify(result, null, 1) + '\n', 'utf8');
  if (svgOut) fs.writeFileSync(svgOut, L.toSVG(result, kit), 'utf8');
  if (!jsonOut && !svgOut) process.stdout.write(JSON.stringify(result, null, 1) + '\n');
  process.stderr.write(`[coordn8:layout] ${result.nodes.length} nodes, ${result.edges.length} edges, ${result.hops.length} hop(s), ` +
                       `${result.rerouted.length} re-route(s), ${result.warnings.length} warning(s)\n`);
  return 0;
}

if (require.main === module) process.exitCode = main(process.argv.slice(2));
module.exports = { main };
