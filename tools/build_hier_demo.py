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

import random
import buda2bdb  # reuse the .buda parser + cell-size helper

_GAP = 200.0  # spacing between instances laid out in a row


def _define_leaf_cell(db, cell, buda_path):
    """Create `cell` (+ a synthetic child cell per block + cell_children rows)
    from a flat .buda script.  Returns (w, h, [block_names], [nets]) where each
    net is {name, drv, rcvs, dir} with `block.port` endpoints (see buda2bdb)."""
    parsed = buda2bdb.parse_script(buda_path)
    if not parsed.blocks:
        sys.exit(f"Error: no blocks in {buda_path}")
    w, h, ox, oy = buda2bdb._cell_size_and_origin(parsed)
    db.add_cell(cell, w, h)
    for name, (x1, y1, x2, y2) in parsed.blocks.items():
        child = f"{cell}__{name}"
        db.add_cell(child, x2 - x1, y2 - y1)
        db.add_inst_to_cell(cell, name, child, x1 - ox, y1 - oy)
    return w, h, list(parsed.blocks.keys()), parsed.nets


def _add_cell_internal_nets(db, top_inst, placements):
    """Replicate each cell's internal nets into every instance, with endpoints
    qualified by the instance path and globally-unique hierarchical net names
    (e.g. chip/i_dnuts1_0/n11_0) — the representation import_verilog produces and
    the hier bundler templates per cell type.  Returns the net count added."""
    n_added = 0
    for inst, _cell, _x, _y, blocks, nets in placements:
        P = f"{top_inst}/{inst}"
        block_set = set(blocks)
        for net in nets:
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
          cell_nets=True, busterms=True):
    rng = random.Random(seed)
    # Leaf .buda files carry full pipeline/tech commands buda2bdb doesn't read;
    # silence its per-line "ignored command" warnings while defining cells.
    buda2bdb._warn = lambda *_a, **_k: None

    # Fresh BDB.
    if os.path.exists(out_path):
        os.remove(out_path)
    db = buda_db.BDB(out_path)

    # 1. Define each leaf cell from its .buda file.
    cells = []  # (cell_name, w, h, blocks, nets)
    for path in cell_files:
        name = os.path.splitext(os.path.basename(path))[0]
        w, h, blocks, nets = _define_leaf_cell(db, name, path)
        cells.append((name, w, h, blocks, nets))
        print(f"  cell {name:16s} {w:6.0f} x {h:6.0f}  "
              f"({len(blocks)} blocks, {len(nets)} internal nets)")

    # 2. Lay out two instances of each cell in a single row; compute top size.
    placements = []  # (inst_name, cell_name, x, y, blocks, nets)
    x_cursor, row_h = 0.0, 0.0
    for name, w, h, blocks, nets in cells:
        for k in range(2):
            short = "chan" if name == "channel_stress" else name
            inst = f"i_{short}_{k}"
            placements.append((inst, name, x_cursor, 0.0, blocks, nets))
            x_cursor += w + _GAP
            row_h = max(row_h, h)
    top_w, top_h = max(x_cursor - _GAP, 1.0), row_h

    # 3. Top cell + its six child instances + materialize the hierarchy.
    db.add_cell(top_cell, top_w, top_h)
    for inst, cell_name, x, y, _b, _n in placements:
        db.add_inst_to_cell(top_cell, inst, cell_name, x, y)
    db.add_inst(top_inst, top_cell, "", 0.0, 0.0)
    print(f"  top  {top_cell:16s} {top_w:6.0f} x {top_h:6.0f}  "
          f"({len(placements)} instances)")

    # 4. Replicate each cell's internal buses into every instance so the hier
    #    flow plans them together (and templates the two instances per cell).
    n_cell_nets = _add_cell_internal_nets(db, top_inst, placements) if cell_nets else 0
    if cell_nets:
        print(f"  cell-internal nets: {n_cell_nets} (replicated across instances)")

    # 6. Pool of leaf endpoints across all instances: "chip/<inst>/<block>".
    pool = [f"{top_inst}/{inst}/{blk}"
            for inst, _, _, _, blocks, _n in placements for blk in blocks]

    # 7. Top-level buses, one per bit width 4 … 16 (even widths), each wiring a
    #    random subset of leaf blocks: one driver + 2-5 receivers.  At least one
    #    receiver is forced into a DIFFERENT depth-1 instance from the driver so
    #    every bus is a genuine cross-instance top-level net (common ancestor =
    #    the top), not an intra-instance one — regardless of seed.
    def _inst_of(ep):
        return ep.rsplit('/', 1)[0]                 # "chip/<inst>"

    widths = list(range(4, 17, 2))   # 4,6,8,10,12,14,16
    n_nets = 0
    for bi, w in enumerate(widths):
        k = rng.randint(3, 6)                       # total endpoints
        drv_base = rng.choice(pool)
        cross = [e for e in pool if _inst_of(e) != _inst_of(drv_base)]
        first_rcv = rng.choice(cross)               # guaranteed other instance
        chosen = {drv_base, first_rcv}
        extra_pool = [e for e in pool if e not in chosen]
        extra = rng.sample(extra_pool, min(max(k - 2, 0), len(extra_pool)))
        rcv_bases = [first_rcv] + extra
        bus = f"top_bus{bi}_w{w}"
        for b in range(w):
            db.add_net_pins(f"{bus}_{b}",
                            f"{drv_base}.tx",
                            [f"{r}.rx" for r in rcv_bases])
            n_nets += 1
        print(f"  bus  {bus:16s} [{w:2d}]  "
              f"{drv_base.split('/', 1)[1]} -> {len(rcv_bases)} rcv")

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
        elif argv[i] in ("-h", "--help"):
            print(__doc__); sys.exit(0)
        else:
            pos.append(argv[i]); i += 1
    if pos:
        out_path = pos[0]

    missing = [c for c in cell_files if not os.path.exists(c)]
    if missing:
        sys.exit(f"Error: cell file(s) not found: {missing}")

    print(f"Building hierarchical demo BDB (seed={seed}) …")
    build(out_path, cell_files, seed=seed, cell_nets=cell_nets, busterms=busterms)


if __name__ == "__main__":
    main()
