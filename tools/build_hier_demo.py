#!/usr/bin/env python3
"""
tools/build_hier_demo.py — Build a hierarchical demo BDB from scratch.

Defines three leaf cells from existing flat .buda scripts (dnuts1, dnuts2,
channel_stress), instantiates each **twice** inside a `top` cell, and adds a
handful of top-level **buses** (bit counts 4 … 16) that wire random subsets of
the leaf blocks across those six instances.

Usage:
  python3 tools/build_hier_demo.py [out.bdb] [--seed N] [--cells a.buda,b.buda,...]
                                   [--no-cell-nets] [--no-busterms]
                                   [--optimize sa|ga] [--param KEY=VALUE ...]
                                   [--bloat 20% | --bloat dx=50,dy=80]

  --optimize sa|ga   place the top cell's six instances in 2D (SA or GA) to
                     shorten the cross-instance top buses (default: row layout).
  --param KEY=VALUE  optimizer knob (repeatable). Values accept k/m suffixes
                     (iter=20k). Friendly keys: iter, wl, area, ovlp, seed, and
                     for GA pop/mutation/crossover, for SA t_init/t_min/alpha;
                     raw run_sa/run_ga arg names pass through.
  --bloat …          inflate each instance ONLY for optimization (by percent or
                     absolute dx/dy) so SA/GA leaves routing channels; the real-
                     sized instance is centered in its bloated slot.

Defaults: out = /tmp/hier_demo.bdb, seed = 1,
          cells = flow/dnuts1.buda, flow/dnuts2.buda, flow/channel_stress.buda

Resulting hierarchy:
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

Open it afterward with the Floorplanner (`./fp out.bdb`) or drive the hier flow
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
    sys.exit("Error: buda_db not found — run './bb' first, or set PYTHONPATH=build.")

import math
import random
import buda2bdb  # reuse the .buda parser + cell-size helper

_GAP = 200.0   # spacing between instances laid out in a row
_GRID = 10.0   # snap grid used by the optimizer / applied placements


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


# ── Optimizer option parsing (--optimize / --param / --bloat) ────────────────

_SA_KEYS = {"iter": "max_iter", "wl": "w_wl", "area": "w_area", "ovlp": "w_ovlp"}
_GA_KEYS = {"iter": "generations", "wl": "w_wl", "area": "w_area",
            "ovlp": "w_ovlp", "pop": "population", "mutation": "mutation_rate",
            "crossover": "crossover_rate"}


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


def _parse_param(s: str):
    if "=" not in s:
        sys.exit(f"Error: --param expects KEY=VALUE, got '{s}'")
    k, raw = s.split("=", 1)
    return k.strip(), _parse_value(raw)


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
    kw.setdefault("max_iter" if method == "sa" else "generations",
                  20000 if method == "sa" else 200)
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
    try:
        result = (opt.run_sa(**kw) if method == "sa" else opt.run_ga(**kw))
    except TypeError as exc:
        sys.exit(f"Error: invalid --param for {method}: {exc}")

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
    print(f"  optimize {method.upper()}: hpwl={result.hpwl:.0f} "
          f"area={result.area:.0f} overlap={result.overlap:.0f} "
          f"({result.iterations} iter){bl} → top {top_w:.0f} x {top_h:.0f}")
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


def build(out_path, cell_files, seed=1, top_inst="chip", top_cell="top",
          cell_nets=True, busterms=True, optimize=None, opt_params=None,
          bloat=None):
    rng = random.Random(seed)
    # Leaf .buda files carry full pipeline/tech commands buda2bdb doesn't read;
    # silence its per-line "ignored command" warnings while defining cells.
    buda2bdb._warn = lambda *_a, **_k: None

    # Fresh BDB.
    if os.path.exists(out_path):
        os.remove(out_path)
    db = buda_db.BDB(out_path)

    # 1. Define each leaf cell from its .buda file.
    cells = []                  # (name, w, h, blocks, nets, centers)
    cell_meta = {}              # name -> (w, h, centers)
    for path in cell_files:
        name = os.path.splitext(os.path.basename(path))[0]
        w, h, blocks, nets, centers = _define_leaf_cell(db, name, path)
        cells.append((name, w, h, blocks, nets, centers))
        cell_meta[name] = (w, h, centers)
        print(f"  cell {name:16s} {w:6.0f} x {h:6.0f}  "
              f"({len(blocks)} blocks, {len(nets)} internal nets)")

    # 2. Name two instances of each cell and lay them out in a starting row.
    placements = []             # mutable dicts: {inst, cell, x, y, blocks, nets}
    x_cursor, row_h = 0.0, 0.0
    for name, w, h, blocks, nets, _centers in cells:
        for k in range(2):
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
    widths = list(range(4, 17, 2))   # 4,6,8,10,12,14,16
    buses = []                  # (name, width, drv(inst,blk), [rcv(inst,blk)…])
    for bi, w in enumerate(widths):
        k = rng.randint(3, 6)
        drv = rng.choice(pool)
        cross = [e for e in pool if e[0] != drv[0]]
        first_rcv = rng.choice(cross)
        chosen = {drv, first_rcv}
        extra_pool = [e for e in pool if e not in chosen]
        extra = rng.sample(extra_pool, min(max(k - 2, 0), len(extra_pool)))
        buses.append((f"top_bus{bi}_w{w}", w, drv, [first_rcv] + extra))

    # 4. Optionally optimize the instance placement (mutates placements x/y).
    if optimize:
        top_w, top_h = _optimize_instances(placements, cell_meta, buses,
                                           optimize, opt_params or {}, bloat, seed)

    # 5. Top cell + its six child instances + materialize the hierarchy.
    db.add_cell(top_cell, top_w, top_h)
    for p in placements:
        db.add_inst_to_cell(top_cell, p["inst"], p["cell"], p["x"], p["y"])
    db.add_inst(top_inst, top_cell, "", 0.0, 0.0)
    print(f"  top  {top_cell:16s} {top_w:6.0f} x {top_h:6.0f}  "
          f"({len(placements)} instances)")

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

    # 8. Derive busterms so the BDB is ready for the hier bundler / topology gen.
    if busterms:
        buda_db.BustermGen(db).derive(2)

    db.compute_all()
    total_nets = n_cell_nets + n_nets
    print(f"\nWrote {out_path}: {len(placements)} instances, "
          f"{len(widths)} top buses, {n_cell_nets} cell-internal nets, "
          f"{total_nets} nets total.")
    if busterms:
        print("\nPlan all buses together with the hier flow "
              "(depth 2 reaches the cell-internal buses):")
        print(f"  PYTHONPATH=build python3 src/buda_cli.py <<'EOF'")
        print(f"  open_bdb {out_path}")
        # Layer technology: the BDB carries none, so define the TOP routing
        # layers the planner/NUTS use (M4 horizontal, M5 vertical) — otherwise
        # check_connectivity flags every segment as on an 'undefined' layer.
        print(f"  def_layer 4 M4 H TOP 44.44")
        print(f"  def_layer 5 M5 V TOP 50.00")
        # Populate the flat floorplan at every routing level so NUTS builds its
        # Hanan grid / keepouts from real block edges (chip=0, instances=1,
        # leaf blocks=2; 'skip' = only that exact depth).
        print(f"  add_blocks_from_bdb 0")
        print(f"  add_blocks_from_bdb 1 skip")
        print(f"  add_blocks_from_bdb 2 skip")
        print(f"  run_hier_bundler depth 2")
        print(f"  generate_hier_topologies")
        print(f"  run_planner hier")
        print(f"  run_nuts")
        print(f"  EOF")
    return out_path


def main():
    argv = sys.argv[1:]
    out_path = "/tmp/hier_demo.bdb"
    seed = 1
    cell_files = [os.path.join(_ROOT, "flow", f)
                  for f in ("dnuts1.buda", "dnuts2.buda", "channel_stress.buda")]

    cell_nets = True
    busterms = True
    optimize = None
    opt_params: dict = {}
    bloat = None
    i = 0
    pos = []
    while i < len(argv):
        if argv[i] == "--seed" and i + 1 < len(argv):
            seed = int(argv[i + 1]); i += 2
        elif argv[i] == "--cells" and i + 1 < len(argv):
            cell_files = [c if os.path.isabs(c) else os.path.join(_ROOT, c)
                          for c in argv[i + 1].split(",")]
            i += 2
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
    if (opt_params or bloat) and not optimize:
        print("buda2bdb: warning: --param/--bloat ignored without --optimize",
              file=sys.stderr)

    missing = [c for c in cell_files if not os.path.exists(c)]
    if missing:
        sys.exit(f"Error: cell file(s) not found: {missing}")

    print(f"Building hierarchical demo BDB (seed={seed}) …")
    build(out_path, cell_files, seed=seed, cell_nets=cell_nets,
          busterms=busterms, optimize=optimize, opt_params=opt_params,
          bloat=bloat)


if __name__ == "__main__":
    main()
