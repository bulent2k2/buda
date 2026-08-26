BUDA (BUndled Design Assistant) is an open-source EDA interconnect planning system for hierarchical chip designs. It has five major components:
1. **Bundling**: Grouping nets and buses (flat and hierarchical bundle templates with multiple instances).
2. **Topology Generation**: Generating physical routing shapes on Hanan grids (L/Z/U-shapes, trunk&hybrid MST, relay completion) with support for custom refinement.
3. **Congestion Planning**: Global planning and layer assignment with healer lookahead coupling and bottom-up template support.
4. **Track Sharing (NUTS)**: 1.5-D abstract rectangle packing (`nuts`) and bit-level detailed track assignment (`detailed_nuts`) respecting pre-route blockages.
5. **Healer Stack**: Closed-loop model correction (`negotiate_congestion`) and measured search (`ripup_reroute`) to eliminate overlaps and opens.

BUDA also provides an interactive Floorplanner GUI (`bin/fp`, `bin/bfp`), SQLite physical database persistence (BDB), multi-format interchange (DEF/LEF, Verilog, GDSII), and a web application interface (`web/`, `src/web/`).

Here is the [complete conversation of origin](https://gemini.google.com/share/293d9e6c10a4) for BUDA.

A key quote: "This is an exciting challenge. Revitalizing a proven concept with modern algorithms and software architecture is a fantastic way to approach EDA (Electronic Design Automation) tool development. Given your background, you likely remember that 25 years ago, compute constraints dictated simple heuristics. Today, we can leverage graph neural networks (GNNs), massive parallelism, and modern optimization solvers.

Here is a high-level architectural proposal for your Interconnect Planning System..."

```
buda/
├── CMakeLists.txt              # CMake build configuration
├── bin/                        # Shell launcher wrappers (bb, buda, fp, bfp, u2b, viz, activate)
├── src/                        # C++20 engines (bdb, bundler, topology, congestion_planner, nuts, detailed_nuts, routing_grid)
│   ├── bindings.cpp            # Pybind11 pipeline module bindings
│   ├── bind_db.cpp             # Pybind11 database module bindings
│   ├── buda_cli.py             # CLI runner
│   ├── buda_viz.py             # Matplotlib visualizer
│   ├── buda_cmds/              # .buda script command handlers
│   ├── buda_session/           # Session state persistence & healer algorithms (ripup.py)
│   ├── viz_main/               # Interactive visualizer components
│   └── web/                    # Python Web server backend
├── tools/                      # Interactive Floorplanner GUI, design converters (bdb2buda, buda2bdb, unit2buda), GDS scripts
├── web/                        # Scala.js web client frontend
├── test/
│   └── tests/                  # Pytest suite & Gherkin feature specs (test/tests/features/)
├── docs/                       # Guides, CLI reference, BDB reference, and internal design notes
└── flow/                       # Example design flows and script testcases
```

## Quick start

```bash
source bin/activate                   # per shell: puts bin/ on PATH, sets PYTHONPATH
bb                                    # build the C++/pybind11 engine into build/
buda demo/comprehensive_demo.buda     # run a routing flow
fp flow/rnr/mix2_aligned.bdb.sql      # open the interactive Floorplanner GUI
u2b test_column_datapath_hvh          # convert a topology unit test to .buda + visualize
```

`bin/activate` must be **sourced**, not executed (a PATH change only affects the
sourcing shell). Without it, invoke the wrappers as `bin/bb`, `bin/buda …`, etc.
See [CLAUDE.md](CLAUDE.md) or [GEMINI.md](GEMINI.md) for full architectural and development guidance.

## License

This project is licensed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for the full text.


