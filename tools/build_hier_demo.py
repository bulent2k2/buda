#!/usr/bin/env python3
"""
tools/build_hier_demo.py — Build a hierarchical demo BDB from scratch.

Defines three leaf cells from existing flat .buda scripts (dnuts1, dnuts2,
channel_stress), instantiates each a configurable number of times (default
**twice**) inside a `top` cell, and adds a configurable number of top-level
**buses** (default 7, bit counts cycling 4 … 16) that wire random subsets of the
leaf blocks across those instances.

Usage:
  python3 tools/build_hier_demo.py [out.bdb] [--seed N] [--cells a,b,...]
                                   [--path DIR] [--instances N] [--buses N]
                                   [--no-cell-nets] [--no-busterms]
                                   [--optimize sa|ga] [--param KEY=VALUE ...]
                                   [--bloat 20% | --bloat dx=50,dy=80]

  --cells a,b,...    comma-separated leaf cells. The `.buda` extension is
                     inferred when omitted (`--cells dnuts1,dnuts2`); a bare name
                     is looked up in the --path directory (default: flow/), an
                     absolute path is used as-is, and a directory-qualified entry
                     (`flow/two.buda`) stays relative to the repo root.
  --path DIR         directory holding the leaf .buda files for bare --cells
                     names (default: the repo's flow/ directory).
  --instances SPEC   instances per cell (default 2). SPEC is one int for every
                     cell (`--instances 3`), a positional list in --cells order
                     (`--instances 1,4,2`), or named counts
                     (`--instances dnuts1=3,channel_stress=1`; unlisted cells
                     default to 2). Each count must be >= 1.
  --buses N          number of *base* top-level cross-instance buses (default 7;
                     bit widths cycle the palette [4,6,8,10,12,14,16]). Extra
                     buses are appended automatically so every instance is wired
                     to at least three top buses.
  --layout row|stacked
                     instance placement. `row` (default) lays every instance in
                     one row at y=0. `stacked` gives each cell type its own
                     COLUMN in --cells order (left to right) and stacks that
                     cell's instances vertically — so all its instances share
                     one x, and with --grid their vertical pitch is a multiple
                     of the track LCM, making them congruent AND phase-aligned
                     as placed (check_template_tracks reports ALIGNED with no
                     align_bottom_up).  Incompatible with --optimize.
  --channel V        minimum gap between stacked instances and between columns
                     (default 200).  The realized vertical gap is whatever
                     --grid rounds the pitch up to, so it is >= V.
  --grid Q           snap the vertical stack pitch UP to a multiple of Q
                     (default 0 = off; --layout stacked only).  Q is the LCM of
                     the track pitches of the layers the cells may use — the
                     caller knows the technology, this tool does not.
  --column-align bottom|top|center
                     where a column sits in the die (--layout stacked only).
                     `bottom` (default) starts every column at y=0; `top` makes
                     every column's top edge flush with the tallest; `center`
                     splits a column's slack equally above and below, which is
                     what makes the layout mirror-symmetric about the die
                     centreline.  Columns move as RIGID blocks, so intra-column
                     pitches — and therefore --grid alignment — are untouched;
                     moving individual instances would break them.
  --mirror-upper     flip every instance whose centre is above the die
                     centreline upside down (--layout stacked only), so the
                     upper half is the exact mirror image of the lower half,
                     contents included.  Combine with --column-align center.
  --optimize sa|ga   place the top cell's instances in 2D (SA or GA) to
                     shorten the cross-instance top buses (default: row layout).
  --param KEY=VALUE  optimizer knob (repeatable). Values accept k/m suffixes
                     (iter=20k). Friendly keys: iter, wl, area, ovlp, seed, and
                     for GA pop/mutation/crossover, for SA t_init/t_min/alpha;
                     raw run_sa/run_ga arg names pass through.
                     time=5s|2m|1h  → run to a soft wall-clock budget instead of
                                      a fixed iter count (calibrated per-iter).
                     patience=N     → stop early after N non-improving checks
                                      (default 10 for timed runs).
  --bloat …          inflate each instance ONLY for optimization (by percent or
                     absolute dx/dy) so SA/GA leaves routing channels; the real-
                     sized instance is centered in its bloated slot.

Defaults: out = /tmp/hier_demo.bdb, seed = 1, path = flow/,
          cells = dnuts1, dnuts2, channel_stress  (→ flow/<name>.buda)

Resulting hierarchy (with --instances 2):
  chip                       (cell "top")
  ├── i_<cellA>_0 / _1       (cell "<cellA>")  → its leaf blocks
  ├── i_<cellB>_0 / _1
  └── i_<cellC>_0 / _1
  plus top-level buses connecting random leaf blocks across instances.

Each leaf cell contributes both its placement (blocks) AND its internal buses,
replicated into every instance with instance-qualified names (e.g.
chip/i_dnuts1_0/n11_0) — the representation import_verilog produces.  The hier
flow then templates the two instances of each cell and plans the cell-internal
and top-level buses together.  Busterms are derived so the BDB is ready for
run_hier_bundler.

  --no-cell-nets   only emit the top-level buses (lean ~70-net demo)
  --no-busterms    skip busterm derivation

Open it afterward with the Floorplanner (`bin/fp out.bdb`) or drive the hier flow
(the build prints the exact run_hier_bundler / run_planner hier commands).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in [os.path.join(_ROOT, "build"), _HERE]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import buda_db
except ModuleNotFoundError:
    sys.exit("Error: buda_db not found — run 'bin/bb' first, or set PYTHONPATH=build.")

import math
import random
import signal
import time

import buda2bdb  # reuse the .buda parser + cell-size helper


def _sigpipe_shell_default():
    """CLI-ONLY: die silently on a closed pipe, like a shell tool.

    `... | head -N` closes the pipe early; Python's default behaviour raises
    BrokenPipeError at the next flush and kills the build mid-write, so the
    CLI restores the shell default and the atomic-rename output path never
    sees a half-written BDB.

    This MUST run from main(), never at import.  Signal dispositions are
    process-global: when this module set SIG_DFL at import, every pytest-xdist
    worker inherited it while COLLECTING test_build_hier_demo.py — and any
    later test in that worker whose machinery takes an EPIPE died silently
    with the whole worker ("[gwN] node down: Not properly terminated").
    CPython ignores SIGPIPE at startup precisely so a broken pipe is an
    exception, not a death; the qor sweep's crash-recovery test SIGKILLs a
    pool worker, the executor's queue-feeder races a write into the dying
    worker's pipe, and with SIG_DFL that EPIPE killed the innocent xdist
    worker about every other whole-suite run.  Repro + forensics in the fix
    commit; regression test: test_build_hier_demo.py::
    test_import_leaves_sigpipe_disposition_alone."""
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass

_GAP = 200.0   # spacing between instances laid out in a row
_GRID = 10.0   # snap grid used by the optimizer / applied placements

# Bit widths cycled across the top-level buses (one per bus, wrapping when
# --buses exceeds the palette length).  Seven entries reproduce the historical
# default set [4,6,8,10,12,14,16] one-for-one.
_WIDTH_PALETTE = [4, 6, 8, 10, 12, 14, 16]


def _define_leaf_cell(db, cell, buda_path):
    """Create `cell` (+ a synthetic child cell per block + cell_children rows)
    from a flat .buda script.  Returns (w, h, [block_names], [nets], centers)
    where each net is {name, drv, rcvs, dir} with `block.port` endpoints and
    `centers` maps block -> its center in cell-local coords."""
    parsed = buda2bdb.parse_script(buda_path)
    if not parsed.blocks:
        sys.exit(f"Error: no blocks in {buda_path}")
    w, h, ox, oy = buda2bdb._cell_size_and_origin(parsed)
    db.add_cell(cell, w, h)
    centers = {}
    for name, (x1, y1, x2, y2) in parsed.blocks.items():
        child = f"{cell}__{name}"
        db.add_cell(child, x2 - x1, y2 - y1)
        db.add_inst_to_cell(cell, name, child, x1 - ox, y1 - oy)
        centers[name] = ((x1 + x2) / 2 - ox, (y1 + y2) / 2 - oy)
    return w, h, list(parsed.blocks.keys()), parsed.nets, centers


# Pin directions that mark a net's driver endpoint (bdb2buda's convention).
_DRIVER_DIRS = {"OUTPUT", "DRIVER"}


def _define_bdb_cell(db, cell, bdb_path):
    """Create `cell` from an EXISTING hierarchical BDB (`.bdb` / `.bdb.sql`) —
    the enhancement that lets a whole routed-design fixture (mix2, a prior
    build of this very tool, an imported design) be instantiated as a reusable
    cell inside a bigger chip.

    The source design is flattened ONE structural level deep: every LEAF
    component becomes a child block of the new cell, named by its
    root-relative path with '/' folded to '__' (so multiply-instantiated
    sub-blocks stay unique: chip/i_dnuts1_0/u0 -> i_dnuts1_0__u0), and every
    net with >=2 leaf-pin endpoints is carried over with the same folded
    names.  Interface pins on intermediate ancestors are skipped — they are
    the SAME logical connection propagated upward (add_net_pins recreates
    them in the target hierarchy).  Driver selection, the 'unknown'/'inout'
    direction classes, and the '.p' port fallback mirror bdb2buda's export
    walk.  Same return shape as _define_leaf_cell."""
    import bdb_serialize   # accepts *.bdb.sql transparently (temp binary)
    try:
        src = buda_db.BDB(bdb_serialize.materialize_if_sql(bdb_path))
    except Exception as exc:
        sys.exit(f"Error: could not read BDB cell {bdb_path}: {exc}")
    comps = {c.id: c for c in src.all_components()}
    roots = [c for c in comps.values() if c.parent_id == -1]
    if len(roots) != 1:
        sys.exit(f"Error: {bdb_path} must contain exactly one top component "
                 f"to import as a cell (found {len(roots)})")
    root = roots[0]
    prefix = root.name + "/"

    def _flat(name):
        n = name[len(prefix):] if name.startswith(prefix) else name
        return n.replace("/", "__")

    leaves = sorted((c for c in comps.values()
                     if c.is_leaf and c.id != root.id),
                    key=lambda c: c.name)
    if not leaves:
        sys.exit(f"Error: no leaf components in {bdb_path}")
    if root.x2 > root.x1 and root.y2 > root.y1 and root.x1 >= 0 and root.y1 >= 0:
        w, h = root.x2 - root.x1, root.y2 - root.y1
        ox, oy = root.x1, root.y1
    else:
        # The canonical UNPLACED root (-1..-1 bbox, the DEF+Verilog merge
        # state) has no meaningful corner: derive origin AND size from the
        # placed leaves' own extent, as bdb2buda's export path does (review
        # #529 — the die-size fallback alone left ox/oy at -1, shifting
        # every leaf by +1).
        ox = min(c.x1 for c in leaves)
        oy = min(c.y1 for c in leaves)
        w = max(c.x2 for c in leaves) - ox
        h = max(c.y2 for c in leaves) - oy
    # '/' -> '__' folding is not injective when a source hierarchy segment
    # itself contains '__' (a/b__c vs a__b/c) — a silent collision would
    # overwrite one child's definition and merge the nets (review #529).
    folded = {}
    for c in leaves:
        bn = _flat(c.name)
        if bn in folded:
            sys.exit(f"Error: {bdb_path}: folded block name collision — "
                     f"'{folded[bn]}' and '{c.name}' both fold to '{bn}'")
        folded[bn] = c.name
    db.add_cell(cell, w, h)
    centers, blocks, leaf_ids = {}, [], set()
    for c in leaves:
        bn = _flat(c.name)
        child = f"{cell}__{bn}"
        db.add_cell(child, c.x2 - c.x1, c.y2 - c.y1)
        db.add_inst_to_cell(cell, bn, child, c.x1 - ox, c.y1 - oy)
        centers[bn] = ((c.x1 + c.x2) / 2 - ox, (c.y1 + c.y2) / 2 - oy)
        blocks.append(bn)
        leaf_ids.add(c.id)

    pins_by_net = {}
    for p in src.all_pins():
        if p.comp_id in leaf_ids:
            pins_by_net.setdefault(p.net_id, []).append(p)
    net_name = {n.id: n.name for n in src.all_nets()}

    def _ep(p):
        c = comps[p.comp_id]
        pn = p.pin_name
        if not pn or pn == c.name or pn == c.name.rsplit("/", 1)[-1]:
            pn = "p"                      # bdb2buda's bare-name port fallback
        return f"{_flat(c.name)}.{pn}"

    nets = []
    seen_net_names = set()
    for nid in sorted(pins_by_net):
        pins = pins_by_net[nid]
        if len(pins) < 2:
            continue                      # boundary-only net — nothing to route
        drivers = [p for p in pins if p.dir in _DRIVER_DIRS]
        drv = drivers[0] if drivers else pins[0]
        rcvs = [p for p in pins if p is not drv]
        dirs = {p.dir for p in pins}
        if dirs <= {"INOUT"}:
            d = "inout"
        elif dirs <= {"UNKNOWN", ""}:
            d = "unknown"
        else:
            d = ""
        if d == "":
            # A directed net must not keep a receiver pin on its own driver
            # block — the flat CLI rejects the same-block endpoint and the
            # hier bundler degenerates on it; inout/unknown keep every
            # endpoint, exactly as bdb2buda's export filters (review #529).
            rcvs = [p for p in rcvs if p.comp_id != drv.comp_id]
            if not rcvs:
                continue
        nm = _flat(net_name.get(nid, f"net_{nid}"))
        if nm in seen_net_names:
            sys.exit(f"Error: {bdb_path}: folded net name collision on '{nm}'")
        seen_net_names.add(nm)
        nets.append({"name": nm, "drv": _ep(drv),
                     "rcvs": [_ep(p) for p in rcvs], "dir": d})
    return w, h, blocks, nets, centers


# ── Optimizer option parsing (--optimize / --param / --bloat) ────────────────

# Keys whose value is a runtime budget (parsed as seconds, not a k/m count).
_TIME_KEYS = {"time", "runtime", "budget"}
_COMMON_KEYS = {"time": "time_budget_s", "runtime": "time_budget_s",
                "budget": "time_budget_s", "patience": "patience"}
_SA_KEYS = {"iter": "max_iter", "wl": "w_wl", "area": "w_area", "ovlp": "w_ovlp",
            **_COMMON_KEYS}
_GA_KEYS = {"iter": "generations", "wl": "w_wl", "area": "w_area",
            "ovlp": "w_ovlp", "pop": "population", "mutation": "mutation_rate",
            "crossover": "crossover_rate", **_COMMON_KEYS}


def _parse_value(v: str):
    """Parse a --param value: '20k'→20000, '1m'→1000000, ints, else floats."""
    v = v.strip()
    mult = 1
    if v and v[-1] in "kK":
        mult, v = 1000, v[:-1]
    elif v and v[-1] in "mM":
        mult, v = 1_000_000, v[:-1]
    try:
        return int(round(float(v) * mult)) if mult != 1 else int(v)
    except ValueError:
        return float(v)


def _parse_time(v: str) -> float:
    """Parse a runtime budget to seconds: '5s'→5, '2m'→120, '1h'→3600, '30'→30."""
    v = v.strip().lower()
    mult = 1.0
    if v.endswith("s"):
        v = v[:-1]
    elif v.endswith("m"):
        mult, v = 60.0, v[:-1]
    elif v.endswith("h"):
        mult, v = 3600.0, v[:-1]
    return float(v) * mult


def _parse_param(s: str):
    if "=" not in s:
        sys.exit(f"Error: --param expects KEY=VALUE, got '{s}'")
    k, raw = s.split("=", 1)
    k = k.strip()
    # Time budgets use s/m/h suffixes ('2m' = 2 minutes, not 2 million).
    return k, (_parse_time(raw) if k.lower() in _TIME_KEYS else _parse_value(raw))


def _parse_instances(s: str):
    """Parse the --instances value into an int, positional list, or named dict.

    '2'                       → 2            (every cell)
    '2,3,1'                   → [2, 3, 1]    (positional, in --cells order)
    'dnuts1=3,channel_stress=1'
                              → {'dnuts1':3, 'channel_stress':1}  (named counts,
                                validated against FULL cell names; rest default 2)
    """
    s = s.strip()
    if "=" in s:
        d = {}
        for part in s.split(","):
            if not part.strip():
                continue
            name, val = part.split("=", 1)
            d[name.strip()] = int(val)
        return d
    parts = [p for p in s.split(",") if p.strip() != ""]
    if len(parts) == 1:
        return int(parts[0])
    return [int(p) for p in parts]


def _parse_bloat(s: str) -> dict:
    """'20%' → {'pct':20}; '50' → {'dx':50,'dy':50}; 'dx=50,dy=80' → both."""
    s = s.strip()
    if s.endswith("%"):
        return {"pct": float(s[:-1])}
    if "=" in s:
        d = {}
        for part in s.split(","):
            key, val = part.split("=", 1)
            d[key.strip()] = float(val)
        dx = d.get("dx", d.get("dy", 0.0))
        dy = d.get("dy", d.get("dx", 0.0))
        return {"dx": dx, "dy": dy}
    val = float(s)
    return {"dx": val, "dy": val}


def _bloated_size(w: float, h: float, bloat: dict | None):
    """Inflated (w, h) used ONLY for optimization, to leave routing channels."""
    if not bloat:
        return w, h
    if "pct" in bloat:
        f = 1.0 + bloat["pct"] / 100.0
        return w * f, h * f
    return w + bloat.get("dx", 0.0), h + bloat.get("dy", 0.0)


def _opt_kwargs(method: str, params: dict, default_seed: int) -> dict:
    """Map friendly --param keys to run_sa/run_ga kwargs (raw names pass through)."""
    keymap = _SA_KEYS if method == "sa" else _GA_KEYS
    kw = {keymap.get(k, k): v for k, v in params.items()}
    kw.setdefault("seed", default_seed)
    iter_arg = "max_iter" if method == "sa" else "generations"
    if "time_budget_s" in kw:
        # Time drives the run; the iteration count is only a hard ceiling (0 =
        # none) and convergence early-stop is on by default for timed runs.
        kw.setdefault(iter_arg, 0)
        kw.setdefault("patience", 10)
    else:
        kw.setdefault(iter_arg, 20000 if method == "sa" else 200)
    return kw


def _snap(v: float) -> float:
    return round(v / _GRID) * _GRID


def _optimize_instances(placements, cell_meta, buses, method, params, bloat, seed):
    """Place the top cell's instances in 2D with SA/GA to shorten the top buses.
    Mutates each placement's x/y (real-sized instance centered in its bloated
    footprint) and returns the new (top_w, top_h)."""
    import buda  # PlacementOptimizer lives in the routing module

    inst_to_cell = {p["inst"]: p["cell"] for p in placements}
    # Square canvas roomy enough for the (bloated) instances to pack in 2D.
    total, maxdim = 0.0, 1.0
    for p in placements:
        w, h, _c = cell_meta[p["cell"]]
        bw, bh = _bloated_size(w, h, bloat)
        total += bw * bh
        maxdim = max(maxdim, bw, bh)
    side = math.ceil(max(maxdim, math.sqrt(2.0 * total)) / _GRID) * _GRID

    opt = buda.PlacementOptimizer(side, side, _GRID)
    for p in placements:
        w, h, _c = cell_meta[p["cell"]]
        bw, bh = _bloated_size(w, h, bloat)
        opt.add_block_ex(p["inst"], bw, bh, p["x"], p["y"])

    def _pin(endpoint):
        # The real instance is later centered in its bloated slot, so the final
        # pin sits at (bloated_origin + centering_offset + local_center).  Add the
        # same offset here so SA/GA optimizes the ACTUAL final pin locations.
        inst, blk = endpoint
        cell = inst_to_cell[inst]
        w, h, centers = cell_meta[cell]
        bw, bh = _bloated_size(w, h, bloat)
        lcx, lcy = centers[blk]
        return (inst, (lcx + (bw - w) / 2.0, lcy + (bh - h) / 2.0))

    for _name, _w, drv, rcvs in buses:           # one net per bus
        opt.add_net([_pin(drv)] + [_pin(r) for r in rcvs])

    kw = _opt_kwargs(method, params, seed)

    # Live progress: print each new 10% milestone on one line as the optimizer
    # reports it.  (A timed run with convergence early-stop may finish before
    # 100% — that's expected; the runtime/iters below tell the full story.)
    print(f"  optimize {method.upper()}:", end="", flush=True)
    _pct = {"last": 0}

    def _progress(cur, tot):
        if tot <= 0:
            return
        pct = min(100, 100 * cur // tot)
        while _pct["last"] + 10 <= pct:
            _pct["last"] += 10
            print(f" {_pct['last']}%", end="", flush=True)

    kw["progress_fn"] = _progress
    t0 = time.perf_counter()
    try:
        result = (opt.run_sa(**kw) if method == "sa" else opt.run_ga(**kw))
    except TypeError as exc:
        print()  # close the progress line before erroring out
        sys.exit(f"Error: invalid --param for {method}: {exc}")
    runtime = time.perf_counter() - t0
    # A fixed-count run always completes; round the bar out to 100% (the last
    # callback rarely lands exactly on the final iter/gen).  A timed run — or one
    # with convergence early-stop — may legitimately finish sooner, so leave its
    # honest progress as-is.
    full_run = "time_budget_s" not in kw and not kw.get("patience")
    if full_run:
        while _pct["last"] < 100:
            _pct["last"] += 10
            print(f" {_pct['last']}%", end="", flush=True)
    print(f"  ({runtime:.1f}s)")

    placed = {pb.name: pb for pb in result.placements}
    # Center each real-sized instance inside its bloated slot → channels on all
    # sides; then shift the whole set to the origin and snap to grid.
    raw = {}
    xs, ys = [], []
    for p in placements:
        w, h, _c = cell_meta[p["cell"]]
        bw, bh = _bloated_size(w, h, bloat)
        pb = placed[p["inst"]]
        rx, ry = pb.x + (bw - w) / 2.0, pb.y + (bh - h) / 2.0
        raw[p["inst"]] = (rx, ry)
        xs += [rx, rx + w]
        ys += [ry, ry + h]
    minx, miny = min(xs), min(ys)
    for p in placements:
        rx, ry = raw[p["inst"]]
        p["x"], p["y"] = _snap(rx - minx), _snap(ry - miny)
    top_w = max(p["x"] + cell_meta[p["cell"]][0] for p in placements)
    top_h = max(p["y"] + cell_meta[p["cell"]][1] for p in placements)
    bl = "" if not bloat else (f" bloat={bloat.get('pct')}%" if "pct" in bloat
                               else f" bloat dx={bloat['dx']:.0f},dy={bloat['dy']:.0f}")
    budget = kw.get("time_budget_s", 0.0)
    bud = f" budget={budget:g}s" if budget else ""
    print(f"    hpwl={result.hpwl:.0f} area={result.area:.0f} "
          f"overlap={result.overlap:.0f} "
          f"({result.iterations} iter in {runtime:.1f}s{bud}){bl} "
          f"→ top {top_w:.0f} x {top_h:.0f}")
    return top_w, top_h


def _define_bdb_cell_nested(db, cell, bdb_path):
    """Import an existing hierarchical BDB as a cell PRESERVING its internal
    hierarchy — the 3+-level chip variant: where _define_bdb_cell flattens
    the source one level (leaves become direct children), this reconstructs
    the source's CELL TREE from its component tree and nests it, so
    instantiating the imported cell materializes the source's own instances
    one level deeper (chip -> mix2 -> i_dnuts1_0 -> u0 at depth 3).

    Cell definitions are derived per source cell TYPE from a REPRESENTATIVE
    instance (the name-sorted first) — sound for the congruent-replica BDBs
    this tool and import_verilog produce; a size mismatch between instances
    of one type is a loud error.  Non-root cell types are namespaced
    '<cell>__<srccell>' so they cannot collide with other --cells entries'
    cells.  Nets keep root-relative HIERARCHICAL paths ('/' preserved, no
    '__' folding): the build replicates them per instance as
    chip/i_<cell>_k/<path>, and add_net_pins re-propagates interface pins
    through the deep tree.  Return shape matches _define_leaf_cell, with
    `blocks` = the root-relative LEAF paths (the top-bus endpoint pool) and
    `centers` in root-local coords (the SA pin model)."""
    import bdb_serialize
    try:
        src = buda_db.BDB(bdb_serialize.materialize_if_sql(bdb_path))
    except Exception as exc:
        sys.exit(f"Error: could not read BDB cell {bdb_path}: {exc}")
    comps = {c.id: c for c in src.all_components()}
    roots = [c for c in comps.values() if c.parent_id == -1]
    if len(roots) != 1:
        sys.exit(f"Error: {bdb_path} must contain exactly one top component "
                 f"to import as a cell (found {len(roots)})")
    root = roots[0]
    kids = {}
    for c in comps.values():
        kids.setdefault(c.parent_id, []).append(c)
    for v in kids.values():
        v.sort(key=lambda c: c.name)
    # The canonical UNPLACED root (-1..-1 bbox, the DEF+Verilog merge state)
    # has no meaningful corner or size: rebase on the placed depth-1
    # children's extent, as the flat importer does (review #538).  The
    # rebased values feed the cell size, the child offsets, and the leaf
    # centers below via root_x1/y1/w/h.
    if (root.x2 > root.x1 and root.y2 > root.y1
            and root.x1 >= 0 and root.y1 >= 0):
        root_x1, root_y1 = root.x1, root.y1
        root_w, root_h = root.x2 - root.x1, root.y2 - root.y1
    else:
        top_kids = kids.get(root.id, [])
        if not top_kids:
            sys.exit(f"Error: no components under the root in {bdb_path}")
        root_x1 = min(c.x1 for c in top_kids)
        root_y1 = min(c.y1 for c in top_kids)
        root_w = max(c.x2 for c in top_kids) - root_x1
        root_h = max(c.y2 for c in top_kids) - root_y1

    def rel(name):
        pre = root.name + "/"
        return name[len(pre):] if name.startswith(pre) else name

    def tcell(src_cell):                       # namespaced target cell name
        return cell if src_cell == root.cell else f"{cell}__{src_cell}"

    # One representative instance per source cell type, deepest types first
    # so add_inst_to_cell always references an already-defined child cell.
    rep = {}
    for c in sorted(comps.values(), key=lambda c: (c.depth, c.name)):
        if c.id == root.id:
            continue
        if c.cell not in rep:
            rep[c.cell] = c
        else:
            r = rep[c.cell]
            if (abs((c.x2 - c.x1) - (r.x2 - r.x1)) > 1e-6 or
                    abs((c.y2 - c.y1) - (r.y2 - r.y1)) > 1e-6):
                sys.exit(f"Error: {bdb_path}: instances of cell '{c.cell}' "
                         f"differ in size ({r.name} vs {c.name}) — nested "
                         f"import needs congruent replicas")
    for src_cell, r in sorted(rep.items(),
                              key=lambda kv: -kv[1].depth):
        db.add_cell(tcell(src_cell), r.x2 - r.x1, r.y2 - r.y1)
        for ch in kids.get(r.id, []):
            db.add_inst_to_cell(tcell(src_cell), ch.name.rsplit("/", 1)[-1],
                                tcell(ch.cell), ch.x1 - r.x1, ch.y1 - r.y1)
    # The imported cell itself = the renamed root cell.
    w, h = root_w, root_h
    db.add_cell(cell, w, h)
    for ch in kids.get(root.id, []):
        db.add_inst_to_cell(cell, ch.name.rsplit("/", 1)[-1],
                            tcell(ch.cell), ch.x1 - root_x1, ch.y1 - root_y1)

    # Leaf-ness derived STRUCTURALLY (no children) rather than from the
    # stored is_leaf flag — an add_comp-built source can leave the flag
    # stale on mid-level components (review #538 test hazard).
    leaves = sorted((c for c in comps.values()
                     if c.id != root.id and not kids.get(c.id)),
                    key=lambda c: c.name)
    blocks, centers, leaf_ids = [], {}, set()
    for c in leaves:
        bn = rel(c.name)                        # hierarchical, '/' preserved
        blocks.append(bn)
        centers[bn] = ((c.x1 + c.x2) / 2 - root_x1,
                       (c.y1 + c.y2) / 2 - root_y1)
        leaf_ids.add(c.id)

    pins_by_net = {}
    for p in src.all_pins():
        if p.comp_id in leaf_ids:
            pins_by_net.setdefault(p.net_id, []).append(p)
    net_name = {n.id: n.name for n in src.all_nets()}

    def _ep(p):
        c = comps[p.comp_id]
        pn = p.pin_name
        if not pn or pn == c.name or pn == c.name.rsplit("/", 1)[-1]:
            pn = "p"
        return f"{rel(c.name)}.{pn}"

    nets = []
    for nid in sorted(pins_by_net):
        pins = pins_by_net[nid]
        if len(pins) < 2:
            continue
        drivers = [p for p in pins if p.dir in _DRIVER_DIRS]
        drv = drivers[0] if drivers else pins[0]
        rcvs = [p for p in pins if p is not drv]
        dirs = {p.dir for p in pins}
        if dirs <= {"INOUT"}:
            d = "inout"
        elif dirs <= {"UNKNOWN", ""}:
            d = "unknown"
        else:
            d = ""
        if d == "":
            rcvs = [p for p in rcvs if p.comp_id != drv.comp_id]
            if not rcvs:
                continue
        nets.append({"name": rel(net_name.get(nid, f"net_{nid}")),
                     "drv": _ep(drv), "rcvs": [_ep(p) for p in rcvs],
                     "dir": d})
    return w, h, blocks, nets, centers


def _align_occurrences(placements, cell_meta):
    """Snap same-cell instances onto SHARED coordinates so their (congruent)
    block edges COINCIDE in the flat Hanan grid — the hierarchical
    occurrence-alignment study knob: every leaf-block edge of an instance is
    its cell-local edge + the instance origin, so giving a cell's instances a
    common y (row) collapses their ~2*n_blocks distinct y-Hanan-lines to ONE
    set, shrinking the planner's cut/band grid.

    Per cell class: try snapping every instance's y to the class MEDIAN y
    (minimal total movement for an odd count); any instance whose snap would
    overlap another instance's REAL rect reverts (kept where SA put it).
    Then the same for x — an instance already y-aligned usually stays put,
    while stacked pairs share their column edges instead.  Deterministic;
    prints per-class what aligned."""
    def rects(skip=None, override=None):
        out = []
        for p in placements:
            if p is skip:
                continue
            w, h, _c = cell_meta[p["cell"]]
            x, y = p["x"], p["y"]
            if override and p in override:
                x, y = override[p["inst"]]
            out.append((x, y, x + w, y + h))
        return out

    def overlaps(x, y, w, h, others):
        for (ox1, oy1, ox2, oy2) in others:
            if x < ox2 and x + w > ox1 and y < oy2 and y + h > oy1:
                return True
        return False

    by_cell = {}
    for p in placements:
        by_cell.setdefault(p["cell"], []).append(p)
    for cell, insts in sorted(by_cell.items()):
        if len(insts) < 2:
            continue
        w, h, _c = cell_meta[cell]
        for axis in ("y", "x"):
            vals = sorted(pp[axis] for pp in insts)
            target = _snap(vals[len(vals) // 2])          # median, grid-snapped
            moved = 0
            for pp in sorted(insts, key=lambda q: abs(q[axis] - target)):
                if pp[axis] == target:
                    continue
                nx = target if axis == "x" else pp["x"]
                ny = target if axis == "y" else pp["y"]
                others = [(q["x"], q["y"],
                           q["x"] + cell_meta[q["cell"]][0],
                           q["y"] + cell_meta[q["cell"]][1])
                          for q in placements if q is not pp]
                if overlaps(nx, ny, w, h, others):
                    continue                              # keep SA's position
                pp["x"], pp["y"] = nx, ny
                moved += 1
            aligned = sum(1 for pp in insts if pp[axis] == target)
            print(f"  align {cell:12s} {axis}={target:.0f}: "
                  f"{aligned}/{len(insts)} instance(s) share the "
                  f"{'row' if axis == 'y' else 'column'} ({moved} moved)")
    # Re-derive the top-cell extent from the (possibly moved) instances.
    top_w = max(p["x"] + cell_meta[p["cell"]][0] for p in placements)
    top_h = max(p["y"] + cell_meta[p["cell"]][1] for p in placements)
    return top_w, top_h


def _add_cell_internal_nets(db, top_inst, placements):
    """Replicate each cell's internal nets into every instance, with endpoints
    qualified by the instance path and globally-unique hierarchical net names
    (e.g. chip/i_dnuts1_0/n11_0) — the representation import_verilog produces and
    the hier bundler templates per cell type.  Returns the net count added."""
    n_added = 0
    for p in placements:
        P = f"{top_inst}/{p['inst']}"
        block_set = set(p["blocks"])
        for net in p["nets"]:
            eps = [net["drv"]] + net["rcvs"]
            # Defensive: skip a net referencing a block not in this cell.
            if any(ep.rsplit('.', 1)[0] not in block_set for ep in eps):
                continue
            nm = f"{P}/{net['name']}"
            drv = f"{P}/{net['drv']}"
            rcvs = [f"{P}/{r}" for r in net["rcvs"]]
            if net["dir"] == "unknown":
                db.add_net_pins_undirected(nm, [drv] + rcvs)
            elif net["dir"] == "inout":
                db.add_net_pins_inout(nm, [drv] + rcvs)
            else:
                db.add_net_pins(nm, drv, rcvs)
            n_added += 1
    return n_added


# ── Instance-count + top-bus connectivity helpers ────────────────────────────

_DEFAULT_INSTANCES = 2          # per-cell instance count when unspecified
_MIN_BUSES_PER_INST = 3         # every instance must be touched by ≥ this many


def _normalize_instances(n_instances, cell_names):
    """Resolve the --instances spec to a {cell_name: count} map.

    Accepts an int (broadcast to every cell), a positional list/tuple aligned
    to `cell_names`, or a dict keyed by cell name (unlisted cells default to
    _DEFAULT_INSTANCES).  Every count must be ≥ 1."""
    if isinstance(n_instances, dict):
        unknown = set(n_instances) - set(cell_names)
        if unknown:
            sys.exit(f"Error: --instances names unknown cell(s): {sorted(unknown)} "
                     f"(known: {cell_names})")
        counts = {name: int(n_instances.get(name, _DEFAULT_INSTANCES))
                  for name in cell_names}
    elif isinstance(n_instances, (list, tuple)):
        if len(n_instances) != len(cell_names):
            sys.exit(f"Error: --instances has {len(n_instances)} values but there "
                     f"are {len(cell_names)} cells {cell_names}")
        counts = {name: int(c) for name, c in zip(cell_names, n_instances)}
    else:
        counts = {name: int(n_instances) for name in cell_names}
    for name, c in counts.items():
        if c < 1:
            sys.exit(f"Error: --instances count for '{name}' must be >= 1 (got {c})")
    return counts


def _make_top_bus(bi, w, pool, rng, drv=None):
    """Build one cross-instance top bus (name, width, drv, [rcv…]).  When `drv`
    is None it is chosen at random; the draw order matches the historical loop
    so the first N buses are byte-identical to before."""
    k = rng.randint(3, 6)
    if drv is None:
        drv = rng.choice(pool)
    # Prefer a receiver in a DIFFERENT instance; with a single instance fall
    # back to any other block (no cross exists).
    cross = [e for e in pool if e[0] != drv[0]] or [e for e in pool if e != drv]
    first_rcv = rng.choice(cross)
    chosen = {drv, first_rcv}
    extra_pool = [e for e in pool if e not in chosen]
    extra = rng.sample(extra_pool, min(max(k - 2, 0), len(extra_pool)))
    return (f"top_bus{bi}_w{w}", w, drv, [first_rcv] + extra)


def _instance_touch_counts(buses, inst_names):
    """For each instance, how many distinct top buses have an endpoint in it."""
    touch = {inst: 0 for inst in inst_names}
    for (_name, _w, drv, rcvs) in buses:
        for inst in {e[0] for e in [drv] + rcvs}:
            touch[inst] = touch.get(inst, 0) + 1
    return touch


def _ensure_min_buses_per_instance(buses, placements, pool, rng,
                                   min_per_inst=_MIN_BUSES_PER_INST):
    """Append extra top buses until every instance is touched by ≥ min_per_inst
    of them.  Each repair bus is DRIVEN from an under-covered instance (so its
    count is guaranteed to rise); its receivers also help cover others."""
    inst_names = [p["inst"] for p in placements]
    by_inst = {}
    for (inst, blk) in pool:
        by_inst.setdefault(inst, []).append((inst, blk))
    touch = _instance_touch_counts(buses, inst_names)
    bi = len(buses)
    for inst in inst_names:
        while touch[inst] < min_per_inst:
            w = _WIDTH_PALETTE[bi % len(_WIDTH_PALETTE)]
            bus = _make_top_bus(bi, w, pool, rng, drv=rng.choice(by_inst[inst]))
            buses.append(bus)
            for i2 in {e[0] for e in [bus[2]] + bus[3]}:
                touch[i2] = touch.get(i2, 0) + 1
            bi += 1
    return buses


def build(out_path, cell_files, seed=1, top_inst="chip", top_cell="top",
          cell_nets=True, busterms=True, optimize=None, opt_params=None,
          bloat=None, n_instances=2, n_buses=7, align_occurrences=False,
          nest_bdb_cells=False, layout="row", channel=_GAP, grid=0.0,
          column_align="bottom", mirror_upper=False):
    rng = random.Random(seed)
    # Leaf .buda files carry full pipeline/tech commands buda2bdb doesn't read;
    # silence its per-line "ignored command" warnings while defining cells.
    buda2bdb._warn = lambda *_a, **_k: None

    # Fresh BDB, written to a .part file and moved into place only once the
    # build RUNS TO COMPLETION.  A build that dies partway — the classic being
    # SIGPIPE from `... | head -N`, since this tool prints one line per bus —
    # otherwise leaves a plausible-looking BDB missing nets, pins and every
    # busterm.  Serialized into a fixture that reads as a valid design and
    # routes clean because most of the work is absent, it is a silent
    # correctness trap; costing one rename to remove it is cheap.
    part = out_path + ".part"
    for stale in (out_path, part):
        if os.path.exists(stale):
            os.remove(stale)
    db = buda_db.BDB(part)

    # 1. Define each cell — from a flat .buda script, or (the chip-scale
    #    enhancement) from an existing hierarchical BDB flattened one level.
    cells = []                  # (name, w, h, blocks, nets, centers)
    cell_meta = {}              # name -> (w, h, centers)
    for entry in cell_files:
        name, path = entry if isinstance(entry, tuple) \
            else (_cell_default_name(entry), entry)
        if path.lower().endswith(_BDB_EXTS):
            define = (_define_bdb_cell_nested if nest_bdb_cells
                      else _define_bdb_cell)
        else:
            define = _define_leaf_cell
        w, h, blocks, nets, centers = define(db, name, path)
        cells.append((name, w, h, blocks, nets, centers))
        cell_meta[name] = (w, h, centers)
        src = (" [bdb nested]" if define is _define_bdb_cell_nested
               else " [bdb]" if define is _define_bdb_cell else "")
        print(f"  cell {name:16s} {w:6.0f} x {h:6.0f}  "
              f"({len(blocks)} blocks, {len(nets)} internal nets){src}")

    # 2. Name the instances of each cell (count per cell) and place them.
    inst_counts = _normalize_instances(n_instances, [c[0] for c in cells])
    placements = []             # mutable dicts: {inst, cell, x, y, blocks, nets}
    if layout == "stacked":
        # One COLUMN per cell type, in --cells order (left to right), with that
        # cell's instances stacked vertically.  Every instance of a cell shares
        # one x, so their VERTICAL track phase is identical by construction; the
        # vertical pitch is snapped UP to a multiple of --grid so the horizontal
        # tracks agree too.  The result is congruent AND phase-aligned without
        # align_bottom_up: check_template_tracks reports ALIGNED as placed.
        # (--grid is the LCM of the pitches of the layers the cells may use —
        # the caller knows the technology, this tool does not.)
        x_cursor, top_h = 0.0, 0.0
        for name, w, h, blocks, nets, _centers in cells:
            n = inst_counts[name]
            pitch = h + channel
            if grid > 0:
                pitch = math.ceil(pitch / grid) * grid
            short = "chan" if name == "channel_stress" else name
            for k in range(n):
                placements.append({"inst": f"i_{short}_{k}", "cell": name,
                                   "x": x_cursor, "y": k * pitch,
                                   "blocks": blocks, "nets": nets})
            top_h = max(top_h, (n - 1) * pitch + h)
            x_cursor += w + channel
            if n > 1:
                print(f"  stack {name:14s} {n} x  pitch {pitch:8.0f} "
                      f"(cell {h:.0f} + channel {pitch - h:.0f})"
                      + (f", on a {grid:g} grid" if grid > 0 else ""))
        top_w = max(x_cursor - channel, 1.0)
        if column_align != "bottom":
            # Shift each column as a RIGID block: `top` makes every column's
            # top edge flush with the tallest, `center` splits a column's slack
            # equally above and below (which is what makes the whole layout
            # mirror-symmetric about the die centreline).  A whole-column shift
            # leaves every intra-column delta-y untouched, so the on-grid phase
            # survives — shifting individual instances would not, since the
            # slack is not generally a multiple of --grid.
            for name, _w, h, _b, _n, _c in cells:
                col = [p for p in placements if p["cell"] == name]
                col_top = max(p["y"] for p in col) + h
                slack = top_h - col_top
                dy = slack if column_align == "top" else slack / 2.0
                if dy <= 0:
                    continue
                for p in col:
                    p["y"] += dy
                where = ("top edge flush at %.0f" % top_h
                         if column_align == "top"
                         else "centred: %.0f margin above and below" % dy)
                print(f"  align {name:14s} column shifted up {dy:.0f} ({where}"
                      + (f"; delta-y unchanged, still {grid:g}-aligned)"
                         if grid > 0 else ")"))
    else:
        x_cursor, row_h = 0.0, 0.0
        for name, w, h, blocks, nets, _centers in cells:
            for k in range(inst_counts[name]):
                short = "chan" if name == "channel_stress" else name
                placements.append({"inst": f"i_{short}_{k}", "cell": name,
                                   "x": x_cursor, "y": 0.0,
                                   "blocks": blocks, "nets": nets})
                x_cursor += w + _GAP
                row_h = max(row_h, h)
        top_w, top_h = max(x_cursor - _GAP, 1.0), row_h

    # 3. Pick the top-bus connectivity (seeded, position-independent) so the
    #    optimizer can use it before instances are placed.  At least one receiver
    #    is forced into a DIFFERENT instance from the driver, so every bus is a
    #    genuine cross-instance top-level net regardless of seed.
    pool = [(p["inst"], blk) for p in placements for blk in p["blocks"]]
    # One bit width per bus, cycling the palette so n_buses=7 reproduces the
    # historical [4,6,8,10,12,14,16] set (and order) exactly.
    buses = []                  # (name, width, drv(inst,blk), [rcv(inst,blk)…])
    n_random_buses = 0
    if len(pool) < 2:
        # A top bus needs a driver + at least one receiver; with fewer than two
        # leaf-block endpoints in the whole design (e.g. a one-block cell with a
        # single instance) none can be formed — skip them rather than crash.
        if n_buses:
            print(f"  (no top buses: only {len(pool)} leaf-block endpoint(s); "
                  f"need ≥2 for a driver + receiver)")
    else:
        for bi in range(n_buses):
            w = _WIDTH_PALETTE[bi % len(_WIDTH_PALETTE)]
            buses.append(_make_top_bus(bi, w, pool, rng))
        # Guarantee every instance is wired to ≥3 top buses (appends repair
        # buses beyond n_buses when the random set leaves one under-covered).
        n_random_buses = len(buses)
        _ensure_min_buses_per_instance(buses, placements, pool, rng)

    # 4. Optionally optimize the instance placement (mutates placements x/y).
    if optimize:
        top_w, top_h = _optimize_instances(placements, cell_meta, buses,
                                           optimize, opt_params or {}, bloat, seed)

    # 4b. Optional hierarchical occurrence alignment: snap same-cell instances
    #     onto shared rows/columns so their congruent block edges coincide in
    #     the flat Hanan grid (fewer unique lines -> smaller planner cut/band
    #     grid).  Runs AFTER the optimizer so it is a minimal-movement snap of
    #     the optimized placement, overlap-checked per instance.
    if align_occurrences:
        top_w, top_h = _align_occurrences(placements, cell_meta)

    # 5. Top cell + its six child instances + materialize the hierarchy.
    db.add_cell(top_cell, top_w, top_h)
    for p in placements:
        db.add_inst_to_cell(top_cell, p["inst"], p["cell"], p["x"], p["y"])
    db.add_inst(top_inst, top_cell, "", 0.0, 0.0)
    print(f"  top  {top_cell:16s} {top_w:6.0f} x {top_h:6.0f}  "
          f"({len(placements)} instances)")

    # 5b. Optional mirror: flip every instance whose centre is above the die
    #     centreline about its own horizontal axis, so the upper half becomes
    #     the exact mirror image of the lower half — contents included, not
    #     just outlines.  Runs after materialization (flip_comp rewrites the
    #     whole subtree) and before busterm derivation, which reads geometry.
    if mirror_upper:
        yc = top_h / 2.0
        # An instance whose interior CROSSES the centreline cannot map onto its
        # own reflection unless its contents happen to be self-symmetric, and
        # cell contents generally are not.  Flipping it does not help either —
        # it would still be asymmetric about yc.  So refuse rather than emit a
        # layout that silently misses the contents-included guarantee (Codex
        # #568; the classic trigger is an ODD instance count in a centred
        # column, which puts the middle occurrence exactly on the centreline).
        straddle = [p["inst"] for p in placements
                    if p["y"] < yc < p["y"] + cell_meta[p["cell"]][1]]
        if straddle:
            sys.exit(
                f"Error: --mirror-upper: {len(straddle)} instance(s) straddle "
                f"the die centreline y={yc:g} and cannot mirror onto "
                f"themselves: {', '.join(straddle)}.\n"
                f"       Use an EVEN instance count per column (an odd count "
                f"in a centred column puts the middle occurrence on the "
                f"centreline), or change --channel/--grid so no instance "
                f"crosses it.")
        flipped = []
        for p in placements:
            h = cell_meta[p["cell"]][1]
            if p["y"] + h / 2.0 > yc:
                db.flip_comp(f"{top_inst}/{p['inst']}", False)
                flipped.append(p["inst"])
        print(f"  mirror {len(flipped)} instance(s) above y={yc:.0f} flipped "
              f"upside down: {', '.join(flipped)}")

    # 6. Replicate each cell's internal buses into every instance so the hier
    #    flow plans them together (and templates the two instances per cell).
    n_cell_nets = _add_cell_internal_nets(db, top_inst, placements) if cell_nets else 0
    if cell_nets:
        print(f"  cell-internal nets: {n_cell_nets} (replicated across instances)")

    # 7. Emit the top-level buses from the saved connectivity.
    n_nets = 0
    for name, w, drv, rcvs in buses:
        di, dblk = drv
        drv_path = f"{top_inst}/{di}/{dblk}.tx"
        rcv_paths = [f"{top_inst}/{ri}/{rblk}.rx" for (ri, rblk) in rcvs]
        for b in range(w):
            db.add_net_pins(f"{name}_{b}", drv_path, rcv_paths)
            n_nets += 1
        print(f"  bus  {name:16s} [{w:2d}]  {di}/{dblk} -> {len(rcvs)} rcv")

    # 8. Derive busterms so the BDB is ready for the hier bundler / topology
    #    gen — to the ACTUAL max component depth: a nested BDB cell
    #    (--nest-bdb-cells) materializes depth-3+ leaves, and a depth-2
    #    derivation would leave the deepest cell-internal buses out of
    #    hierarchical bundling (review #538).
    max_depth = max((c.depth for c in db.all_components()), default=2)
    if busterms:
        buda_db.BustermGen(db).derive(max_depth)

    db.compute_all()
    db.close() if hasattr(db, "close") else None
    del db                      # flush SQLite before the rename
    os.replace(part, out_path)
    total_nets = n_cell_nets + n_nets
    n_repair = len(buses) - n_random_buses
    repair_note = f" ({n_repair} for ≥3/instance coverage)" if n_repair else ""
    print(f"\nWrote {out_path}: {len(placements)} instances, "
          f"{len(buses)} top buses{repair_note}, {n_cell_nets} cell-internal "
          f"nets, {total_nets} nets total.")
    if busterms:
        print("\nPlan all buses together with the hier flow "
              f"(depth {max_depth} reaches the cell-internal buses):")
        print(f"  PYTHONPATH=build python3 src/buda_cli.py <<'EOF'")
        print(f"  open_bdb {out_path}")
        # Layer technology: the BDB carries none, so define the TOP routing
        # layers the planner/NUTS use (M4 horizontal, M5 vertical) — otherwise
        # check_design flags every segment as on an 'undefined' layer.
        print(f"  def_layer 4 M4 H TOP 44.44")
        print(f"  def_layer 5 M5 V TOP 50.00")
        # Populate the flat floorplan at every routing level so NUTS builds its
        # Hanan grid / keepouts from real block edges (chip=0, instances=1,
        # leaf blocks=2; 'skip' = only that exact depth).
        print(f"  add_blocks_from_bdb 0")
        for d in range(1, max_depth + 1):
            print(f"  add_blocks_from_bdb {d} skip")
        print(f"  run_hier_bundler depth {max_depth}")
        print(f"  generate_hier_topologies")
        print(f"  run_planner hier")
        print(f"  run_nuts")
        print(f"  EOF")
    return out_path


_DEFAULT_CELLS = ["dnuts1", "dnuts2", "channel_stress"]
_DEFAULT_CELL_DIR = os.path.join(_ROOT, "flow")


_BDB_EXTS = (".bdb", ".bdb.sql", ".sql")


def _cell_default_name(path: str) -> str:
    """Cell name from a source path: basename minus .buda/.bdb/.bdb.sql/.sql."""
    base = os.path.basename(path)
    low = base.lower()
    for ext in (".bdb.sql", ".bdb", ".sql", ".buda"):
        if low.endswith(ext):
            return base[:-len(ext)]
    return os.path.splitext(base)[0]


def _resolve_cell(entry: str, base_dir: str):
    """Resolve one --cells entry to a (cell_name, source_path) pair.

    - `NAME=PATH` names the cell explicitly (`big2=flow/.../tc3b_flat_x5.buda`);
      otherwise the name is the basename minus its extension;
    - a `.bdb` / `.bdb.sql` / `.sql` entry is an EXISTING hierarchical BDB,
      imported as a cell (see _define_bdb_cell); anything else is a flat
      `.buda` script (the extension is inferred when missing);
    - an absolute path is used as-is; a dir-qualified entry (`flow/two.buda`)
      is taken relative to the repo root (backward compatible); a bare name is
      looked up in `base_dir` (the `--path` directory)."""
    e = entry.strip()
    name = None
    if "=" in e:
        name, e = (s.strip() for s in e.split("=", 1))
    if not e.lower().endswith(_BDB_EXTS + (".buda",)):
        e += ".buda"
    if os.path.isabs(e):
        path = e
    elif os.path.dirname(e):               # dir-qualified → relative to repo root
        path = os.path.join(_ROOT, e)
    else:
        path = os.path.join(base_dir, e)   # bare cell name → the --path directory
    return (name or _cell_default_name(path), path)


def _resolve_cells(entries, path_arg):
    """Resolve --cells entries against the optional --path directory (default:
    the repo's flow/ directory).  Returns [(cell_name, source_path), …]."""
    base_dir = os.path.abspath(path_arg) if path_arg else _DEFAULT_CELL_DIR
    resolved = [_resolve_cell(e, base_dir) for e in entries]
    seen = set()
    for name, _p in resolved:
        if name in seen:
            sys.exit(f"Error: duplicate cell name '{name}' in --cells "
                     f"(use NAME=PATH to disambiguate)")
        seen.add(name)
    return resolved


def main():
    _sigpipe_shell_default()          # CLI-only; see the docstring
    argv = sys.argv[1:]
    out_path = "/tmp/hier_demo.bdb"
    seed = 1
    cell_entries = list(_DEFAULT_CELLS)
    path_arg = None

    cell_nets = True
    busterms = True
    optimize = None
    opt_params: dict = {}
    bloat = None
    n_instances = 2
    n_buses = 7
    align_occurrences = False
    nest_bdb_cells = False
    layout = "row"
    channel = _GAP
    grid = 0.0
    column_align = "bottom"
    mirror_upper = False
    i = 0
    pos = []
    while i < len(argv):
        if argv[i] == "--seed" and i + 1 < len(argv):
            seed = int(argv[i + 1]); i += 2
        elif argv[i] == "--cells" and i + 1 < len(argv):
            cell_entries = [c for c in argv[i + 1].split(",") if c.strip()]
            i += 2
        elif argv[i] in ("--path", "-path") and i + 1 < len(argv):
            path_arg = argv[i + 1]; i += 2
        elif argv[i] in ("--instances", "-instances") and i + 1 < len(argv):
            n_instances = _parse_instances(argv[i + 1]); i += 2
        elif argv[i] in ("--buses", "-buses") and i + 1 < len(argv):
            n_buses = int(argv[i + 1]); i += 2
        elif argv[i] in ("--align-occurrences", "-align-occurrences"):
            align_occurrences = True; i += 1
        elif argv[i] in ("--nest-bdb-cells", "-nest-bdb-cells"):
            nest_bdb_cells = True; i += 1
        elif argv[i] in ("--layout", "-layout") and i + 1 < len(argv):
            layout = argv[i + 1].lower()
            if layout not in ("row", "stacked"):
                sys.exit("Error: --layout must be 'row' or 'stacked'")
            i += 2
        elif argv[i] in ("--channel", "-channel") and i + 1 < len(argv):
            channel = float(argv[i + 1]); i += 2
        elif argv[i] in ("--grid", "-grid") and i + 1 < len(argv):
            grid = float(argv[i + 1]); i += 2
        elif argv[i] in ("--column-align", "-column-align") and i + 1 < len(argv):
            column_align = argv[i + 1].lower()
            if column_align not in ("bottom", "top", "center"):
                sys.exit("Error: --column-align must be bottom|top|center")
            i += 2
        elif argv[i] in ("--mirror-upper", "-mirror-upper"):
            mirror_upper = True; i += 1
        elif argv[i] == "--no-cell-nets":
            cell_nets = False; i += 1
        elif argv[i] == "--no-busterms":
            busterms = False; i += 1
        elif argv[i] in ("--optimize", "-optimize") and i + 1 < len(argv):
            optimize = argv[i + 1].lower()
            if optimize not in ("sa", "ga"):
                sys.exit("Error: --optimize must be 'sa' or 'ga'")
            i += 2
        elif argv[i] in ("--param", "-param") and i + 1 < len(argv):
            k, v = _parse_param(argv[i + 1]); opt_params[k] = v; i += 2
        elif argv[i] in ("--bloat", "-bloat") and i + 1 < len(argv):
            bloat = _parse_bloat(argv[i + 1]); i += 2
        elif argv[i] in ("-h", "--help"):
            print(__doc__); sys.exit(0)
        else:
            pos.append(argv[i]); i += 1
    if pos:
        out_path = pos[0]
    if layout == "stacked" and optimize:
        sys.exit("Error: --layout stacked places instances explicitly on grid; "
                 "--optimize would move them off it (drop one or the other)")
    if column_align != "bottom" and layout != "stacked":
        sys.exit("Error: --column-align applies to --layout stacked (there is "
                 "one row to align in the row layout)")
    if mirror_upper and layout != "stacked":
        sys.exit("Error: --mirror-upper applies to --layout stacked")
    if grid > 0 and layout != "stacked":
        sys.exit("Error: --grid applies to --layout stacked (the row layout "
                 "puts every instance on y=0)")
    if channel < 0:
        sys.exit("Error: --channel must be >= 0")
    if (opt_params or bloat) and not optimize:
        print("buda2bdb: warning: --param/--bloat ignored without --optimize",
              file=sys.stderr)
    if n_buses < 0:
        sys.exit("Error: --buses must be >= 0")
    if path_arg and not os.path.isdir(os.path.abspath(path_arg)):
        sys.exit(f"Error: --path directory not found: {path_arg}")

    if not cell_entries:
        sys.exit("Error: --cells must name at least one cell")
    cell_files = _resolve_cells(cell_entries, path_arg)
    missing = [p for _n, p in cell_files if not os.path.exists(p)]
    if missing:
        sys.exit(f"Error: cell file(s) not found: {missing}")

    print(f"Building hierarchical demo BDB (seed={seed}) …")
    build(out_path, cell_files, seed=seed, cell_nets=cell_nets,
          busterms=busterms, optimize=optimize, opt_params=opt_params,
          bloat=bloat, n_instances=n_instances, n_buses=n_buses,
          align_occurrences=align_occurrences, nest_bdb_cells=nest_bdb_cells,
          layout=layout, channel=channel, grid=grid,
          column_align=column_align, mirror_upper=mirror_upper)


if __name__ == "__main__":
    main()
