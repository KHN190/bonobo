"""The same table, drawn. Reads `bench/fuzz.jsonl` (and the live rows, if there are any) and writes one HTML file.

Nothing here imports a planner. It reads lines of JSON and lays them out, which is the whole contract: a picture
that can only be produced by re-running the model is a picture of the model, not of what it did, and the moment
the drawing depends on the code it draws, a regression can hide in both at once. So: no `bonobo` import beyond
`paths`, no estimate recomputed, no number invented. If a value is not in the table, the page says "unmeasured".

Four views, each answering a question the swept tests cannot:

    decision map   any two dimensions against each other, everything else pinned; one square per cell, coloured
                   AND labelled by what was picked. Click a square for every column's cost, saving, whether it
                   was admissible, and the reason it was not. This is where "the planner does something different
                   over there" stops being a suspicion.
    margins        the histogram of best-minus-runner-up, in seconds, plus the list of the tightest cells. A cell
                   at 0.1 s is not a decision, it is a coin flip with a confident tape, and it is exactly where a
                   measurement is worth taking.
    curves         each quantity along each dimension, as the ladders walked it, with the sampled cells scattered
                   around the line and — where the live bench measured the same quantity — its rows overlaid so
                   the residual is visible rather than asserted.
    columns        per column: how often it is offered, how often admissible, how often picked, and the reasons
                   it was refused, next to the panel of everything nothing has measured yet.

    python3 -m bonobo.tools.plot --open
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bonobo import paths  # noqa: E402

FUZZ = paths.data("bench/fuzz.jsonl")
COMBAT = paths.data("bench/combat.jsonl")
OUT = paths.data("bench/fuzz.html")


def load(path):
    """Every JSON object in a `.jsonl`, and nothing else. A half-written last line is skipped, not an error."""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def measured(rows):
    """The live rows, reduced to the pairs an offline quantity can be read against.

    A bench row carries what it predicted and what the clock said, side by side (`bench/fight.py`), so the
    residual is theirs and not ours: this only picks out the two quantities that mean the same thing on both
    sides — seconds until something reaches us, and health per second while it is reaching us.
    """
    out = []
    for row in rows:
        predicted = ((row.get("intent") or {}).get("predicted") or {})
        seen = row.get("measured") or {}
        arrivals = [v for v in (predicted.get("arrival_s") or []) if isinstance(v, (int, float))]
        first = [v for v in (seen.get("first_arrival_s") or {}).values() if isinstance(v, (int, float))]
        dims = {k: v for k, v in row.items() if isinstance(v, str) and k not in ("scenario", "when")}
        point = {"dims": dims,
                 "arrival_predicted": min(arrivals) if arrivals else None,
                 "arrival_measured": min(first) if first else None,
                 "pressure_predicted": predicted.get("pressure_hp_s"),
                 "pressure_measured": seen.get("dps_while_bleeding"),
                 "hp_lost": seen.get("hp_lost"),
                 "fired": row.get("fired") or [],
                 "held": ((row.get("intent") or {}).get("held"))}
        if any(point[k] is not None for k in ("arrival_measured", "pressure_measured")):
            out.append(point)
    return out


def payload(fuzz_rows, combat_rows):
    return {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fuzz": fuzz_rows,
            "measured": measured(combat_rows),
            "counts": {"cells": len({json.dumps(r.get("dims"), sort_keys=True) for r in fuzz_rows}),
                       "rows": len(fuzz_rows), "measured": len(combat_rows)}}


def render(data):
    """One file: the table as JSON inside it, the drawing as plain JS beside it, nothing fetched."""
    return TEMPLATE.replace("__PAYLOAD__", json.dumps(data).replace("</", "<\\/"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="draw the fuzz table (reads jsonl, writes one HTML file)")
    parser.add_argument("--fuzz", default=FUZZ)
    parser.add_argument("--combat", default=COMBAT, help="live rows to overlay; skipped when absent")
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--open", action="store_true", help="open it when it is written")
    args = parser.parse_args(argv)
    rows = load(args.fuzz)
    if not rows:
        print(f"no rows in {args.fuzz} — run: python3 -m bonobo.tools.fuzz")
        return 1
    html = render(payload(rows, load(args.combat)))
    paths.ensure(args.out)
    with open(args.out, "w") as fh:
        fh.write(html)
    print(f"{len(rows)} rows -> {args.out}")
    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(args.out))
    return 0


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Decision Table</title>
<style>
:root {
  color-scheme: light;
  --surface-0: #f4f4f1;
  --surface-1: #fcfcfb;
  --line: #dcdcd6;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #77766f;
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --series-3: #1baf7a;
  --series-4: #eda100;
  --series-5: #e87ba4;
  --series-6: #008300;
  --series-7: #4a3aa7;
  --series-8: #e34948;
  --other: #9a998f;
  --good: #1baf7a;
  --bad: #e34948;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0: #111110;
    --surface-1: #1a1a19;
    --line: #34342f;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #8f8e84;
    --series-1: #3987e5;
    --series-2: #d95926;
    --series-3: #199e70;
    --series-4: #c98500;
    --series-5: #d55181;
    --series-6: #008300;
    --series-7: #9085e9;
    --series-8: #e66767;
    --other: #6f6e66;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #111110;
  --surface-1: #1a1a19;
  --line: #34342f;
  --text-primary: #ffffff;
  --text-secondary: #c3c2b7;
  --text-muted: #8f8e84;
  --series-1: #3987e5;
  --series-2: #d95926;
  --series-3: #199e70;
  --series-4: #c98500;
  --series-5: #d55181;
  --series-6: #008300;
  --series-7: #9085e9;
  --series-8: #e66767;
  --other: #6f6e66;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
}
header { padding: 20px 16px 8px; }
h1 { font-size: 19px; margin: 0 0 4px; letter-spacing: -0.01em; }
.sub { color: var(--text-secondary); font-size: 13px; }
main { padding: 0 16px 64px; max-width: 1180px; margin: 0 auto; }
.tabs { display: flex; gap: 4px; flex-wrap: wrap; margin: 12px 0 16px; border-bottom: 1px solid var(--line); }
.tab {
  background: none; border: none; border-bottom: 2px solid transparent; cursor: pointer;
  color: var(--text-secondary); padding: 8px 12px; font: inherit; font-size: 13px;
}
.tab[aria-selected="true"] { color: var(--text-primary); border-bottom-color: var(--series-1); }
.panel { display: none; }
.panel.on { display: block; }
.controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 14px; }
label.ctl { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.04em; }
select, button.act {
  background: var(--surface-1); color: var(--text-primary); border: 1px solid var(--line);
  border-radius: 6px; padding: 5px 8px; font: inherit; font-size: 13px;
}
.card { background: var(--surface-1); border: 1px solid var(--line); border-radius: 10px; padding: 14px;
  margin-bottom: 16px; overflow-x: auto; }
.card h2 { font-size: 14px; margin: 0 0 2px; }
.card p.note { margin: 0 0 12px; color: var(--text-secondary); font-size: 12px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--text-muted); font-weight: 600; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.04em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.grid { display: grid; gap: 2px; }
.cell {
  border: none; border-radius: 4px; cursor: pointer; padding: 6px 4px; font: inherit; font-size: 11px;
  color: #fff; text-shadow: 0 1px 2px rgba(0,0,0,.45); min-height: 42px; line-height: 1.25;
  display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 2px;
}
.cell small { opacity: .85; font-size: 10px; }
.cell.empty { background: var(--surface-0); border: 1px dashed var(--line); color: var(--text-muted);
  text-shadow: none; cursor: default; }
.cell[aria-pressed="true"] { outline: 2px solid var(--text-primary); outline-offset: 1px; }
.axis-label { color: var(--text-muted); font-size: 11px; display: flex; align-items: center;
  justify-content: center; text-align: center; padding: 2px; }
.legend { display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0 0; font-size: 12px;
  color: var(--text-secondary); }
.legend span.key { display: inline-flex; align-items: center; gap: 5px; }
.swatch { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.tag { display: inline-block; padding: 0 6px; border-radius: 10px; font-size: 11px; border: 1px solid var(--line);
  color: var(--text-secondary); }
.tag.yes { color: var(--good); border-color: currentColor; }
.tag.no { color: var(--bad); border-color: currentColor; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
svg { display: block; width: 100%; height: auto; }
svg text { fill: var(--text-secondary); font-size: 10px; }
svg .grid-line { stroke: var(--line); stroke-width: 1; }
.hint { color: var(--text-muted); font-size: 12px; }
#tip {
  position: fixed; pointer-events: none; opacity: 0; transition: opacity .1s; z-index: 9;
  background: var(--surface-1); border: 1px solid var(--line); border-radius: 8px; padding: 6px 9px;
  font-size: 12px; box-shadow: 0 6px 20px rgba(0,0,0,.18); max-width: 280px;
}
@media (max-width: 640px) { .controls { gap: 8px; } main { padding: 0 16px 48px; } }
</style>
</head>
<body>
<div id="tip" role="status"></div>
<header>
  <h1>Decision table</h1>
  <div class="sub" id="summary"></div>
</header>
<main>
  <div class="tabs" role="tablist">
    <button class="tab" role="tab" data-panel="map" aria-selected="true">Decision map</button>
    <button class="tab" role="tab" data-panel="margins" aria-selected="false">Margins</button>
    <button class="tab" role="tab" data-panel="curves" aria-selected="false">Quantities</button>
    <button class="tab" role="tab" data-panel="columns" aria-selected="false">Columns</button>
  </div>

  <section class="panel on" id="panel-map">
    <div class="controls" id="map-controls"></div>
    <div class="card">
      <h2>What was picked, cell by cell</h2>
      <p class="note">Every square is one cell: two dimensions on the axes, the rest pinned above. The label is
        the pick; colour repeats it. Click a square for its columns.</p>
      <div id="map"></div>
      <div class="legend" id="map-legend"></div>
    </div>
    <div class="card" id="detail"><h2>No cell selected</h2>
      <p class="note">Pick a square, or a row from the fragile list, to see every column that was priced.</p></div>
  </section>

  <section class="panel" id="panel-margins">
    <div class="controls" id="margin-controls"></div>
    <div class="card">
      <h2>Best minus runner-up, in seconds</h2>
      <p class="note">How much of a decision each cell actually was. The left-hand bars are the cells where the
        planner is nearly indifferent — a different rounding would pick differently.</p>
      <div id="hist"></div>
    </div>
    <div class="card">
      <h2>The tightest cells</h2>
      <p class="note">Sorted by margin. These are the cells worth measuring in game.</p>
      <div id="fragile"></div>
    </div>
  </section>

  <section class="panel" id="panel-curves">
    <div class="controls" id="curve-controls"></div>
    <div class="card">
      <h2>Each quantity along each dimension</h2>
      <p class="note">The line is the ladder (one dimension moved, everything else at its default); the dots are
        sampled cells at that value. Where the live bench measured the same quantity, its rows are drawn as rings
        with a stem to what was predicted — the stem is the residual.</p>
      <div class="charts" id="curves"></div>
      <div class="legend" id="curve-legend"></div>
    </div>
  </section>

  <section class="panel" id="panel-columns">
    <div class="controls" id="column-controls"></div>
    <div class="card">
      <h2>Every column, and what became of it</h2>
      <p class="note">Offered, admissible, picked. A column that is never admissible is a column that does not
        exist; a column that is always picked is a branch nothing competes with.</p>
      <div id="columns"></div>
    </div>
    <div class="card">
      <h2>Why columns were refused</h2>
      <div id="whynot"></div>
    </div>
    <div class="card">
      <h2>Unmeasured</h2>
      <p class="note">Quantities no door could answer in a cell, and what the choosers said they were assuming.
        Nothing here is an error — it is the part of the table that is still faith.</p>
      <div id="unmeasured"></div>
    </div>
  </section>
</main>
<script>
const DATA = __PAYLOAD__;
const ROWS = DATA.fuzz || [];
const MEASURED = DATA.measured || [];
const SERIES = ["--series-1","--series-2","--series-3","--series-4","--series-5","--series-6","--series-7",
                "--series-8"];
const QUANTITIES = [
  ["arrival", "arrival (s)", "Δt — gates.takes_s"],
  ["pressure", "encounters / s", "p — gates.p"],
  ["hp_price", "seconds per hp", "κ — gates.marginal"],
  ["state_price", "seconds to finish", "V — gates.V"],
];
const el = (tag, attrs, kids) => {
  const node = document.createElement(tag);
  for (const k in (attrs || {})) {
    if (k === "text") node.textContent = attrs[k];
    else if (k === "html") node.innerHTML = attrs[k];
    else node.setAttribute(k, attrs[k]);
  }
  (kids || []).forEach(k => node.appendChild(k));
  return node;
};
const num = v => (v === null || v === undefined || isNaN(v)) ? "—" : (Math.abs(v) >= 100 ?
  Number(v).toFixed(0) : Number(v).toFixed(2));
const uniq = xs => Array.from(new Set(xs));

// -- the shape of the table ---------------------------------------------------------------------------------
const FAMILIES = uniq(ROWS.map(r => r.family));
const DIM_ORDER = [];
const DIM_VALUES = {};
ROWS.forEach(r => {
  for (const name in (r.dims || {})) {
    if (!DIM_VALUES[name]) { DIM_VALUES[name] = []; DIM_ORDER.push(name); }
    if (!DIM_VALUES[name].includes(r.dims[name])) DIM_VALUES[name].push(r.dims[name]);
  }
});
const state = {
  family: FAMILIES.includes("threat") ? "threat" : FAMILIES[0],
  x: DIM_ORDER[0], y: DIM_ORDER[1] || DIM_ORDER[0], pinned: {}, selected: null,
};
DIM_ORDER.forEach(d => { state.pinned[d] = DIM_VALUES[d][0]; });

const familyRows = () => ROWS.filter(r => r.family === state.family);
const picksOf = rows => {
  const tally = {};
  rows.forEach(r => { if (r.pick) tally[r.pick] = (tally[r.pick] || 0) + 1; });
  return Object.keys(tally).sort((a, b) => tally[b] - tally[a]);
};
let COLOR_OF = {};
function assignColors(rows) {
  COLOR_OF = {};
  picksOf(rows).slice(0, SERIES.length).forEach((name, i) => { COLOR_OF[name] = `var(${SERIES[i]})`; });
}
const colorFor = pick => COLOR_OF[pick] || "var(--other)";

// -- tooltip ------------------------------------------------------------------------------------------------
const tip = document.getElementById("tip");
function showTip(evt, html) {
  tip.innerHTML = html;
  tip.style.opacity = 1;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = evt.clientY - h - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.style.opacity = 0; };
function hoverable(node, html) {
  node.addEventListener("mousemove", e => showTip(e, html));
  node.addEventListener("mouseleave", hideTip);
}

// -- controls -----------------------------------------------------------------------------------------------
function selector(labelText, values, current, onChange, format) {
  const sel = el("select");
  values.forEach(v => {
    const opt = el("option", {value: String(v), text: format ? format(v) : String(v)});
    if (String(v) === String(current)) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.addEventListener("change", () => onChange(sel.value));
  return el("label", {class: "ctl"}, [el("span", {text: labelText}), sel]);
}

function familySelector(redraw) {
  return selector("family", FAMILIES, state.family, v => { state.family = v; redraw(); });
}

function buildMapControls() {
  const box = document.getElementById("map-controls");
  box.innerHTML = "";
  box.appendChild(familySelector(drawMap));
  box.appendChild(selector("x axis", DIM_ORDER, state.x, v => { state.x = v; drawMap(); }));
  box.appendChild(selector("y axis", DIM_ORDER, state.y, v => { state.y = v; drawMap(); }));
  DIM_ORDER.filter(d => d !== state.x && d !== state.y).forEach(d => {
    box.appendChild(selector(d, DIM_VALUES[d], state.pinned[d], v => { state.pinned[d] = v; drawMap(); }));
  });
}

// -- the decision map -----------------------------------------------------------------------------------------
function matches(row, ignore) {
  for (const d of DIM_ORDER) {
    if (ignore.includes(d)) continue;
    if (String((row.dims || {})[d]) !== String(state.pinned[d])) return false;
  }
  return true;
}

function drawMap() {
  buildMapControls();
  const rows = familyRows();
  assignColors(rows);
  const xs = DIM_VALUES[state.x], ys = DIM_VALUES[state.y];
  const here = rows.filter(r => matches(r, [state.x, state.y]));
  const at = {};
  here.forEach(r => { at[r.dims[state.x] + "|@|" + r.dims[state.y]] = r; });
  const map = document.getElementById("map");
  map.innerHTML = "";
  const grid = el("div", {class: "grid"});
  grid.style.gridTemplateColumns = `minmax(64px, .7fr) repeat(${xs.length}, minmax(64px, 1fr))`;
  grid.appendChild(el("div", {class: "axis-label", text: state.y + " \\ " + state.x}));
  xs.forEach(x => grid.appendChild(el("div", {class: "axis-label", text: x})));
  ys.forEach(y => {
    grid.appendChild(el("div", {class: "axis-label", text: y}));
    xs.forEach(x => {
      const row = at[x + "|@|" + y];
      if (!row) { grid.appendChild(el("div", {class: "cell empty", text: "—"})); return; }
      const cell = el("button", {class: "cell", "aria-pressed": String(state.selected === row)});
      cell.style.background = colorFor(row.pick);
      cell.appendChild(el("span", {text: row.pick || (row.error ? "error" : "none")}));
      cell.appendChild(el("small", {text: row.margin === null || row.margin === undefined ?
        "no runner-up" : num(row.margin) + "s"}));
      hoverable(cell, `<b>${row.pick || "—"}</b><br>${state.x}: ${x} · ${state.y}: ${y}` +
        `<br>margin ${num(row.margin)}s · ${row.columns_total} columns` +
        (row.error ? `<br><span style="color:var(--bad)">${row.error}</span>` : ""));
      cell.addEventListener("click", () => { state.selected = row; drawMap(); drawDetail(); });
      grid.appendChild(cell);
    });
  });
  map.appendChild(grid);
  const legend = document.getElementById("map-legend");
  legend.innerHTML = "";
  picksOf(here).forEach(p => {
    const key = el("span", {class: "key"});
    const sw = el("span", {class: "swatch"});
    sw.style.background = colorFor(p);
    key.appendChild(sw); key.appendChild(el("span", {text: p}));
    legend.appendChild(key);
  });
  if (!here.length) legend.appendChild(el("span", {class: "hint", text: "no cells at this pinning"}));
}

function drawDetail() {
  const box = document.getElementById("detail");
  const row = state.selected;
  box.innerHTML = "";
  if (!row) {
    box.appendChild(el("h2", {text: "No cell selected"}));
    return;
  }
  const dims = DIM_ORDER.map(d => `${d}=${row.dims[d]}`).join(" · ");
  box.appendChild(el("h2", {text: `${row.family}: ${row.pick || "nothing picked"}`}));
  box.appendChild(el("p", {class: "note", text: dims}));
  const q = el("table");
  q.appendChild(el("tr", {}, [el("th", {text: "quantity"}), el("th", {class: "num", text: "value"}),
    el("th", {text: "door"})]));
  QUANTITIES.forEach(([key, label, door]) => {
    const v = (row.quantities || {})[key];
    q.appendChild(el("tr", {}, [el("td", {text: label}),
      el("td", {class: "num", text: v === null || v === undefined ? "unmeasured" : num(v)}),
      el("td", {class: "hint", text: door})]));
  });
  box.appendChild(q);
  const cols = Object.entries(row.columns || {}).sort((a, b) =>
    (b[1].saves === null ? -1e9 : b[1].saves) - (a[1].saves === null ? -1e9 : a[1].saves));
  const t = el("table");
  t.appendChild(el("tr", {}, [el("th", {text: "column"}), el("th", {class: "num", text: "saves (s)"}),
    el("th", {class: "num", text: "cost (s)"}), el("th", {text: "admissible"}), el("th", {text: "why not"})]));
  cols.forEach(([name, col]) => {
    const tr = el("tr");
    const label = el("td");
    const sw = el("span", {class: "swatch"});
    sw.style.background = name === row.pick ? colorFor(name) : "transparent";
    label.appendChild(sw); label.appendChild(document.createTextNode(" " + name));
    tr.appendChild(label);
    tr.appendChild(el("td", {class: "num", text: col.saves === null ? "—" : num(col.saves)}));
    tr.appendChild(el("td", {class: "num", text: num(col.cost_s)}));
    tr.appendChild(el("td", {}, [el("span", {class: "tag " + (col.admissible ? "yes" : "no"),
      text: col.admissible ? "yes" : "no"})]));
    tr.appendChild(el("td", {class: "hint", text: col.why_not || col.error || ""}));
    t.appendChild(tr);
  });
  box.appendChild(t);
  if ((row.assumptions || []).length) {
    box.appendChild(el("p", {class: "note", text: "assuming: " + row.assumptions.join("; ")}));
  }
  if ((row.fault || []).length) {
    box.appendChild(el("p", {class: "note", text: "fault: " + JSON.stringify(row.fault)}));
  }
}

// -- margins ----------------------------------------------------------------------------------------------
function drawMargins() {
  const box = document.getElementById("margin-controls");
  box.innerHTML = "";
  box.appendChild(familySelector(drawMargins));
  const rows = familyRows().filter(r => typeof r.margin === "number");
  const host = document.getElementById("hist");
  host.innerHTML = "";
  if (!rows.length) { host.appendChild(el("p", {class: "hint", text: "no margins in this family"})); return; }
  const values = rows.map(r => r.margin).sort((a, b) => a - b);
  const lo = values[0], hi = values[values.length - 1];
  const bins = 24, width = Math.max(1e-9, (hi - lo) / bins);
  const counts = new Array(bins).fill(0);
  values.forEach(v => { counts[Math.min(bins - 1, Math.floor((v - lo) / width))] += 1; });
  const W = 720, H = 240, padL = 44, padR = 12, padT = 12, padB = 34;
  const top = Math.max.apply(null, counts) || 1;
  const bw = (W - padL - padR) / bins;
  const svg = el("div");
  let parts = [`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="histogram of decision margins">`];
  for (let i = 0; i <= 4; i++) {
    const y = padT + (H - padT - padB) * i / 4;
    parts.push(`<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}"/>`);
    parts.push(`<text x="${padL - 6}" y="${y + 3}" text-anchor="end">${Math.round(top * (1 - i / 4))}</text>`);
  }
  counts.forEach((c, i) => {
    const h = (H - padT - padB) * (c / top);
    const x = padL + i * bw, y = H - padB - h;
    parts.push(`<rect x="${x + 1}" y="${y}" width="${Math.max(1, bw - 2)}" height="${h}" rx="4"` +
      ` fill="var(--series-1)"><title>${c} cells, ${(lo + i * width).toFixed(2)}–` +
      `${(lo + (i + 1) * width).toFixed(2)}s</title></rect>`);
  });
  parts.push(`<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${H - padB}" y2="${H - padB}"/>`);
  parts.push(`<text x="${padL}" y="${H - 12}">${lo.toFixed(2)}s</text>`);
  parts.push(`<text x="${W - padR}" y="${H - 12}" text-anchor="end">${hi.toFixed(2)}s</text>`);
  parts.push(`<text x="${(W) / 2}" y="${H - 12}" text-anchor="middle">margin, best − runner-up</text>`);
  parts.push("</svg>");
  svg.innerHTML = parts.join("");
  host.appendChild(svg);

  const list = document.getElementById("fragile");
  list.innerHTML = "";
  const t = el("table");
  t.appendChild(el("tr", {}, [el("th", {class: "num", text: "margin (s)"}), el("th", {text: "pick"}),
    el("th", {text: "runner-up"}), el("th", {text: "cell"}), el("th", {text: ""})]));
  rows.slice().sort((a, b) => a.margin - b.margin).slice(0, 25).forEach(r => {
    const ranked = Object.entries(r.columns || {}).filter(c => c[1].saves !== null && c[1].admissible)
      .sort((a, b) => (b[1].saves - b[1].cost_s) - (a[1].saves - a[1].cost_s));
    const second = ranked[1] ? ranked[1][0] : "—";
    const tr = el("tr");
    tr.appendChild(el("td", {class: "num", text: num(r.margin)}));
    tr.appendChild(el("td", {text: r.pick || "—"}));
    tr.appendChild(el("td", {text: second}));
    tr.appendChild(el("td", {class: "hint",
      text: DIM_ORDER.map(d => r.dims[d]).join(" / ")}));
    const open = el("button", {class: "act", text: "columns"});
    open.addEventListener("click", () => {
      state.selected = r; state.family = r.family;
      DIM_ORDER.forEach(d => { state.pinned[d] = r.dims[d]; });
      show("map"); drawMap(); drawDetail();
    });
    tr.appendChild(el("td", {}, [open]));
    t.appendChild(tr);
  });
  list.appendChild(t);
}

// -- quantities along the dimensions -------------------------------------------------------------------------
function lineChart(title, subtitle, values, points, overlay) {
  const W = 440, H = 220, padL = 46, padR = 14, padT = 16, padB = 40;
  const labels = values.map(v => v.label);
  const all = [].concat(values.map(v => v.y), points.map(p => p.y),
    overlay.map(o => o.y), overlay.map(o => o.predicted)).filter(v => typeof v === "number");
  if (!all.length) {
    return `<div class="card"><h2>${title}</h2><p class="note">${subtitle}</p>` +
      `<p class="hint">unmeasured everywhere in this slice</p></div>`;
  }
  let lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
  if (hi === lo) { hi = lo + 1; }
  const x = i => padL + (W - padL - padR) * (labels.length < 2 ? 0.5 : i / (labels.length - 1));
  const y = v => H - padB - (H - padT - padB) * ((v - lo) / (hi - lo));
  let parts = [`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${title}">`];
  for (let i = 0; i <= 3; i++) {
    const yy = padT + (H - padT - padB) * i / 3;
    const v = hi - (hi - lo) * i / 3;
    parts.push(`<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}"/>`);
    parts.push(`<text x="${padL - 6}" y="${yy + 3}" text-anchor="end">${num(v)}</text>`);
  }
  labels.forEach((l, i) => {
    parts.push(`<text x="${x(i)}" y="${H - padB + 14}" text-anchor="middle">${l}</text>`);
  });
  const drawn = values.map((v, i) => [i, v.y]).filter(p => typeof p[1] === "number");
  if (drawn.length > 1) {
    parts.push(`<polyline fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round"` +
      ` points="${drawn.map(p => x(p[0]) + "," + y(p[1])).join(" ")}"/>`);
  }
  drawn.forEach(p => {
    parts.push(`<circle cx="${x(p[0])}" cy="${y(p[1])}" r="4.5" fill="var(--series-1)"` +
      ` stroke="var(--surface-1)" stroke-width="2"><title>ladder ${labels[p[0]]}: ${num(p[1])}</title></circle>`);
  });
  points.forEach(p => {
    if (typeof p.y !== "number") return;
    parts.push(`<circle cx="${x(p.i)}" cy="${y(p.y)}" r="3" fill="var(--series-3)" opacity="0.55">` +
      `<title>sampled ${labels[p.i]}: ${num(p.y)}</title></circle>`);
  });
  overlay.forEach(o => {
    if (typeof o.y !== "number") return;
    if (typeof o.predicted === "number") {
      parts.push(`<line x1="${x(o.i)}" x2="${x(o.i)}" y1="${y(o.predicted)}" y2="${y(o.y)}"` +
        ` stroke="var(--series-2)" stroke-width="2"/>`);
    }
    parts.push(`<circle cx="${x(o.i)}" cy="${y(o.y)}" r="5" fill="none" stroke="var(--series-2)"` +
      ` stroke-width="2"><title>measured ${labels[o.i]}: ${num(o.y)}` +
      (typeof o.predicted === "number" ? `, predicted ${num(o.predicted)}` : "") + `</title></circle>`);
  });
  parts.push("</svg>");
  return `<div class="card"><h2>${title}</h2><p class="note">${subtitle}</p>${parts.join("")}</div>`;
}

function measuredFor(quantity, dim, value) {
  if (quantity !== "arrival" && quantity !== "pressure") return [];
  const key = quantity === "arrival" ? "arrival_measured" : "pressure_measured";
  const said = quantity === "arrival" ? "arrival_predicted" : "pressure_predicted";
  return MEASURED.filter(m => String((m.dims || {})[dim]) === String(value))
    .map(m => ({y: m[key], predicted: m[said]}));
}

function drawCurves() {
  const box = document.getElementById("curve-controls");
  box.innerHTML = "";
  box.appendChild(familySelector(drawCurves));
  const rows = familyRows();
  const host = document.getElementById("curves");
  host.innerHTML = "";
  const chunks = [];
  QUANTITIES.forEach(([key, label, door]) => {
    DIM_ORDER.forEach(dim => {
      const ladder = rows.filter(r => r.slice === "ladder:" + dim);
      if (!ladder.length) return;
      const values = DIM_VALUES[dim].map(v => {
        const hit = ladder.find(r => String(r.dims[dim]) === String(v));
        return {label: String(v), y: hit ? (hit.quantities || {})[key] : null};
      });
      const points = [];
      const overlay = [];
      DIM_VALUES[dim].forEach((v, i) => {
        rows.filter(r => r.slice === "random" && String(r.dims[dim]) === String(v))
          .forEach(r => points.push({i: i, y: (r.quantities || {})[key]}));
        measuredFor(key, dim, v).forEach(m => overlay.push({i: i, y: m.y, predicted: m.predicted}));
      });
      const anything = values.some(v => typeof v.y === "number") || points.length;
      if (!anything) return;
      chunks.push(lineChart(`${label} — ${dim}`, door + (overlay.length ?
        ` · ${overlay.length} measured` : ""), values, points, overlay));
    });
  });
  host.innerHTML = chunks.length ? chunks.join("") :
    `<p class="hint">no ladder rows in this family — run the fuzzer without --no-ladders</p>`;
  const legend = document.getElementById("curve-legend");
  legend.innerHTML = "";
  [["--series-1", "ladder (one dimension moved)"], ["--series-3", "sampled cells"],
   ["--series-2", "measured, stem to predicted"]].forEach(([slot, text]) => {
    const key = el("span", {class: "key"});
    const sw = el("span", {class: "swatch"});
    sw.style.background = `var(${slot})`;
    key.appendChild(sw); key.appendChild(el("span", {text: text}));
    legend.appendChild(key);
  });
}

// -- columns ----------------------------------------------------------------------------------------------
function drawColumns() {
  const box = document.getElementById("column-controls");
  box.innerHTML = "";
  box.appendChild(familySelector(drawColumns));
  const rows = familyRows();
  const tally = {};
  const reasons = {};
  rows.forEach(r => {
    for (const name in (r.columns || {})) {
      const col = r.columns[name];
      const t = tally[name] = tally[name] || {offered: 0, admissible: 0, picked: 0, saves: [], cost: []};
      t.offered += 1;
      if (col.admissible) t.admissible += 1;
      if (r.pick === name) t.picked += 1;
      if (typeof col.saves === "number") t.saves.push(col.saves);
      if (typeof col.cost_s === "number") t.cost.push(col.cost_s);
      if (!col.admissible && col.why_not) {
        const short = String(col.why_not).replace(/[0-9]+(\.[0-9]+)?/g, "n");
        reasons[short] = (reasons[short] || 0) + 1;
      }
    }
  });
  const mean = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  const host = document.getElementById("columns");
  host.innerHTML = "";
  const t = el("table");
  t.appendChild(el("tr", {}, [el("th", {text: "column"}), el("th", {class: "num", text: "offered"}),
    el("th", {class: "num", text: "admissible"}), el("th", {class: "num", text: "picked"}),
    el("th", {class: "num", text: "mean saves (s)"}), el("th", {class: "num", text: "mean cost (s)"})]));
  Object.entries(tally).sort((a, b) => b[1].picked - a[1].picked || b[1].offered - a[1].offered)
    .forEach(([name, s]) => {
      const pct = (n) => s.offered ? (100 * n / s.offered).toFixed(0) + "%" : "—";
      t.appendChild(el("tr", {}, [el("td", {text: name}), el("td", {class: "num", text: String(s.offered)}),
        el("td", {class: "num", text: pct(s.admissible)}), el("td", {class: "num", text: pct(s.picked)}),
        el("td", {class: "num", text: num(mean(s.saves))}),
        el("td", {class: "num", text: num(mean(s.cost))})]));
    });
  host.appendChild(t);

  const why = document.getElementById("whynot");
  why.innerHTML = "";
  const entries = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
  if (!entries.length) { why.appendChild(el("p", {class: "hint", text: "nothing was refused in this family"})); }
  else {
    const w = el("table");
    w.appendChild(el("tr", {}, [el("th", {class: "num", text: "times"}), el("th", {text: "reason"})]));
    entries.forEach(([reason, n]) => {
      w.appendChild(el("tr", {}, [el("td", {class: "num", text: String(n)}), el("td", {text: reason})]));
    });
    why.appendChild(w);
  }

  const un = document.getElementById("unmeasured");
  un.innerHTML = "";
  const missing = el("table");
  missing.appendChild(el("tr", {}, [el("th", {text: "quantity"}), el("th", {class: "num", text: "unmeasured"}),
    el("th", {text: "door"})]));
  QUANTITIES.forEach(([key, label, door]) => {
    const blank = rows.filter(r => (r.quantities || {})[key] === null ||
      (r.quantities || {})[key] === undefined).length;
    missing.appendChild(el("tr", {}, [el("td", {text: label}),
      el("td", {class: "num", text: `${blank} / ${rows.length}`}), el("td", {class: "hint", text: door})]));
  });
  un.appendChild(missing);
  const said = {};
  rows.forEach(r => (r.assumptions || []).forEach(a => { said[a] = (said[a] || 0) + 1; }));
  const a = el("table");
  a.appendChild(el("tr", {}, [el("th", {class: "num", text: "cells"}), el("th", {text: "assumption"})]));
  Object.entries(said).sort((x, y) => y[1] - x[1]).forEach(([text, n]) => {
    a.appendChild(el("tr", {}, [el("td", {class: "num", text: String(n)}), el("td", {text: text})]));
  });
  un.appendChild(a);
}

// -- tabs -------------------------------------------------------------------------------------------------
const DRAW = {map: () => { drawMap(); drawDetail(); }, margins: drawMargins, curves: drawCurves,
              columns: drawColumns};
function show(name) {
  document.querySelectorAll(".tab").forEach(t =>
    t.setAttribute("aria-selected", String(t.dataset.panel === name)));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("on", p.id === "panel-" + name));
  DRAW[name]();
}
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => show(t.dataset.panel)));
document.getElementById("summary").textContent =
  `${DATA.counts.rows} rows over ${DATA.counts.cells} cells · ${DATA.counts.measured} live rows · ` +
  `${DATA.generated}`;
show("map");
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
