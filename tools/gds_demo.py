#!/usr/bin/env python3
# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""GDS import demo: generate a chip as GDSII, import it, route it, explore it.

    bin/bb                          # build once
    source bin/activate             # or PYTHONPATH=build:tools
    python3 tools/gds_demo.py       # -> interactive visualizer
    python3 tools/gds_demo.py --no-viz          # headless (prints summary)
    python3 tools/gds_demo.py --png out.png     # headless render to a PNG

What it does (docs/internal/gds_oa_interchange.md, Phases G0+G1 in action):
1. Writes a demo GDSII chip with tools/gds_build.py — a 2x2 AREF of CPU cores,
   an L2 cache slab, and an IO strip, all placed by SREF/AREF under a `chip`
   top structure (instance names carried as GDS properties).
2. `import_gds` reads it into a fresh BDB: structures -> cells, references ->
   the component hierarchy, top extent -> die.
3. Projects the imported components into the floorplan, declares four
   core->L2 buses + one L2->IO bus, and runs the FULL pipeline: bundler ->
   topologies -> congestion planner -> abstract NUTS -> detailed NUTS.
4. Opens the interactive visualizer (toggle [Detailed] and [Vias/Conns] to see
   per-bit wires and the per-bit via staircases at every bend).

Everything (GDS, BDB) is written under $TMPDIR/buda_gds_demo.
"""

import argparse
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
for _p in (os.path.join(_ROOT, "build"), _HERE, os.path.join(_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gds_build import GdsBuilder  # noqa: E402


def build_demo_gds(path: str) -> str:
    """A small SoC floorplan: 2x2 core array + L2 slab + IO strip."""
    b = GdsBuilder(dbu_um=0.001)
    # A CPU core: 250x250 outline with a little internal detail (an ALU block
    # and a register-file strip) so the hierarchy has depth.
    b.structure("alu").boundary(10, 0, [(0, 0), (100, 0), (100, 80), (0, 80)])
    b.structure("regfile").boundary(10, 0, [(0, 0), (220, 0), (220, 40), (0, 40)])
    b.structure("core") \
     .boundary(10, 0, [(0, 0), (250, 0), (250, 250), (0, 250)]) \
     .sref("alu", (20, 140), inst_name="alu0") \
     .sref("regfile", (15, 30), inst_name="rf")
    # Memory + IO macros.
    b.structure("l2").boundary(10, 0, [(0, 0), (260, 0), (260, 500), (0, 500)])
    b.structure("io").boundary(10, 0, [(0, 0), (940, 0), (940, 120), (0, 120)])
    # The chip: 2x2 core array (AREF) lower-left, L2 upper-right, IO strip on
    # top — deliberately STAGGERED so every bus bends (bends = layer
    # transitions = per-bit via staircases in the detailed view).
    b.structure("chip") \
     .aref("core", (60, 80), cols=2, rows=2,
           col_pitch=(330, 0), row_pitch=(0, 330)) \
     .sref("l2", (780, 520), inst_name="L2") \
     .sref("io", (60, 1140), inst_name="IO")
    return b.write(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-viz", action="store_true",
                    help="headless: run the flow and print a summary")
    ap.add_argument("--png", metavar="FILE",
                    help="headless: render the routed chip to a PNG")
    args = ap.parse_args(argv)
    headless = args.no_viz or args.png

    if headless:
        import matplotlib
        matplotlib.use("Agg")

    import buda_cli  # noqa: E402  (after backend selection)

    work = os.path.join(tempfile.gettempdir(), "buda_gds_demo")
    os.makedirs(work, exist_ok=True)
    gds = build_demo_gds(os.path.join(work, "chip.gds"))
    print(f"[gds_demo] wrote {gds} ({os.path.getsize(gds)} bytes)")

    s = buda_cli.BudaSession()
    s.no_viz = headless
    cmds = [
        f"source {os.path.join(_ROOT, 'flow', 'rnr', 'mix_tracks.buda')}",
        # Phase G3 layer map: metals M2..M7 -> GDS layers 32..37. Export
        # writes the routed wires to these pairs; re-import excludes them
        # from cell footprints.
        *[f"def_gds_layer {lid} {30 + lid} 0" for lid in range(2, 8)],
        "def_gds_layer labels 63",
        f"open_bdb {os.path.join(work, 'chip.bdb')}",
        f"import_gds {gds}",
        "add_blocks_from_bdb 0",            # imported roots -> floorplan blocks
        # Four core->L2 read buses and an L2->IO stream.
        "add_bus c0[8] core_0.p L2.p",
        "add_bus c1[8] core_1.p L2.p",
        "add_bus c2[8] core_2.p L2.p",
        "add_bus c3[8] core_3.p L2.p",
        "add_bus out[16] L2.p IO.p",
        "run_bundler",
        "generate_topologies",
        "run_planner 5",
        "run_nuts",
        "run_detailed_nuts",
    ]
    for c in cmds:
        s.do_command(c)

    n_over = s.nuts_result.num_overlaps if s.nuts_result else -1
    n_unpl = s.detailed_result.num_unplaced if s.detailed_result else -1
    print(f"[gds_demo] routed: {len(s.bundles)} buses, "
          f"{len(s.detailed_result.net_segments)} bit-wires, "
          f"{len(s.detailed_result.net_vias)} per-bit vias "
          f"(overlaps={n_over}, unplaced bits={n_unpl})")
    print(f"[gds_demo] BDB checkpoint: {os.path.join(work, 'chip.bdb')} "
          f"(route_snapshot stage: {s.bdb.route_snapshot().stage})")

    # Phase G4: export the routed chip back to GDSII — bit-wires + vias on
    # the mapped layers, one TEXT label per pin — and prove the round-trip
    # by re-importing it into a fresh BDB with the same layer map.
    out_gds = os.path.join(work, "chip_routed.gds")
    s.do_command(f"export_gds {out_gds}")
    import buda
    rt = buda.BDB(os.path.join(work, "chip_rt.bdb"))
    st = rt.import_gds(out_gds, [63],
                       [(30 + lid, 0) for lid in range(2, 8)])
    same = (sorted((c.name, c.cell, c.x1, c.y1, c.x2, c.y2)
                   for c in rt.all_components()) ==
            sorted((c.name, c.cell, c.x1, c.y1, c.x2, c.y2)
                   for c in s.bdb.all_components()))
    print(f"[gds_demo] round-trip: re-imported {out_gds} -> "
          f"{st.n_components} component(s), {st.n_nets} net(s), "
          f"{st.n_routing_shapes} routing shape(s) excluded; "
          f"placement identical: {same}")

    if args.png:
        import buda_viz
        viz = buda_viz.BudaVisualizer(
            s.fp, s.bundles, routing_grid=s.routing_grid, layer_stack=s.layers,
            net_endpoints=s._net_endpoints)
        viz.draw_blocks()
        viz.draw_nuts_tracks(s.nuts_result)
        viz.draw_detailed_tracks(s.detailed_result, s.routing_grid, s.layers)
        viz._toggle_detailed()
        viz.fig.savefig(args.png, dpi=120)
        print(f"[gds_demo] rendered {args.png}")
    elif not args.no_viz:
        s.do_command("visualize")           # interactive window
    return 0 if (n_over == 0 and n_unpl == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
