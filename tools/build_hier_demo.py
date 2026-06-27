#!/usr/bin/env python3
"""
tools/build_hier_demo.py — Build a hierarchical demo BDB from scratch.

Defines three leaf cells from existing flat .buda scripts (dnuts1, dnuts2,
channel_stress), instantiates each **twice** inside a `top` cell, and adds a
handful of top-level **buses** (bit counts 4 … 16) that wire random subsets of
the leaf blocks across those six instances.

Usage:
  python3 tools/build_hier_demo.py [out.bdb] [--seed N] [--cells a.buda,b.buda,...]

Defaults: out = /tmp/hier_demo.bdb, seed = 1,
          cells = flow/dnuts1.buda, flow/dnuts2.buda, flow/channel_stress.buda

Resulting hierarchy:
  chip                       (cell "top")
  ├── i_<cellA>_0 / _1       (cell "<cellA>")  → its leaf blocks
  ├── i_<cellB>_0 / _1
  └── i_<cellC>_0 / _1
  plus top-level buses connecting random leaf blocks across instances.

Open it afterward with the Floorplanner (`./fp out.bdb`) or drive the hier flow.
The leaf cells contribute placement (blocks); their own internal nets are not
replicated — the demo's connectivity is the top-level buses.
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
    from a flat .buda script.  Returns (w, h, [block_names])."""
    parsed = buda2bdb.parse_script(buda_path)
    if not parsed.blocks:
        sys.exit(f"Error: no blocks in {buda_path}")
    w, h, ox, oy = buda2bdb._cell_size_and_origin(parsed)
    db.add_cell(cell, w, h)
    for name, (x1, y1, x2, y2) in parsed.blocks.items():
        child = f"{cell}__{name}"
        db.add_cell(child, x2 - x1, y2 - y1)
        db.add_inst_to_cell(cell, name, child, x1 - ox, y1 - oy)
    return w, h, list(parsed.blocks.keys())


def build(out_path, cell_files, seed=1, top_inst="chip", top_cell="top"):
    rng = random.Random(seed)
    # Leaf .buda files carry full pipeline/tech commands buda2bdb doesn't read;
    # silence its per-line "ignored command" warnings while defining cells.
    buda2bdb._warn = lambda *_a, **_k: None

    # Fresh BDB.
    if os.path.exists(out_path):
        os.remove(out_path)
    db = buda_db.BDB(out_path)

    # 1. Define each leaf cell from its .buda file.
    cells = []  # (cell_name, w, h, blocks)
    for path in cell_files:
        name = os.path.splitext(os.path.basename(path))[0]
        w, h, blocks = _define_leaf_cell(db, name, path)
        cells.append((name, w, h, blocks))
        print(f"  cell {name:16s} {w:6.0f} x {h:6.0f}  ({len(blocks)} blocks)")

    # 2. Lay out two instances of each cell in a single row; compute top size.
    placements = []  # (inst_name, cell_name, x, y, blocks)
    x_cursor, row_h = 0.0, 0.0
    for name, w, h, blocks in cells:
        for k in range(2):
            short = "chan" if name == "channel_stress" else name
            inst = f"i_{short}_{k}"
            placements.append((inst, name, x_cursor, 0.0, blocks))
            x_cursor += w + _GAP
            row_h = max(row_h, h)
    top_w, top_h = max(x_cursor - _GAP, 1.0), row_h

    # 3. Top cell + its six child instances + materialize the hierarchy.
    db.add_cell(top_cell, top_w, top_h)
    for inst, cell_name, x, y, _ in placements:
        db.add_inst_to_cell(top_cell, inst, cell_name, x, y)
    db.add_inst(top_inst, top_cell, "", 0.0, 0.0)
    print(f"  top  {top_cell:16s} {top_w:6.0f} x {top_h:6.0f}  "
          f"({len(placements)} instances)")

    # 4. Pool of leaf endpoints across all instances: "chip/<inst>/<block>".
    pool = [f"{top_inst}/{inst}/{blk}"
            for inst, _, _, _, blocks in placements for blk in blocks]

    # 5. Top-level buses, one per bit width 4 … 16 (even widths), each wiring a
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

    db.compute_all()
    print(f"\nWrote {out_path}: {len(placements)} instances, "
          f"{len(widths)} buses, {n_nets} nets.")
    return out_path


def main():
    argv = sys.argv[1:]
    out_path = "/tmp/hier_demo.bdb"
    seed = 1
    cell_files = [os.path.join(_ROOT, "flow", f)
                  for f in ("dnuts1.buda", "dnuts2.buda", "channel_stress.buda")]

    i = 0
    pos = []
    while i < len(argv):
        if argv[i] == "--seed" and i + 1 < len(argv):
            seed = int(argv[i + 1]); i += 2
        elif argv[i] == "--cells" and i + 1 < len(argv):
            cell_files = [c if os.path.isabs(c) else os.path.join(_ROOT, c)
                          for c in argv[i + 1].split(",")]
            i += 2
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
    build(out_path, cell_files, seed=seed)


if __name__ == "__main__":
    main()
