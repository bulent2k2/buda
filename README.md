Buda is an open-source system to help plan interconnect for hierarchical chip designs. It has four major components:
1. bundling of nets into buses,
2. generating a set of optimal bus topologies,
3. planning for congestion and layer assignment,
4. track assignment that supports non-uniform width segments, wires and buses.

Buda system also provides a floorplan editor and a DB for persistence.

Here is the [complete conversation of origin](https://gemini.google.com/share/293d9e6c10a4) for Buda.

A key quote: "This is an exciting challenge. Revitalizing a proven concept with modern algorithms and software architecture is a fantastic way to approach EDA (Electronic Design Automation) tool development. Given your background, you likely remember that 25 years ago, compute constraints dictated simple heuristics. Today, we can leverage graph neural networks (GNNs), massive parallelism, and modern optimization solvers.

Here is a high-level architectural proposal for your Interconnect Planning System..."

```
buda/
├── CMakeLists.txt              # The Build Configuration
├── src/
│   ├── bindings.cpp            # The Python Wrapper Code (The Bridge)
│   ├── bundler.h / .cpp        # Engine A
│   ├── topology.h / .cpp       # Engine B
│   ├── layering.h / .cpp       # Engine C
│   ├── planner.h / .cpp        # Engine 4a
│   ├── nuts.h / .cpp           # Engine 4b
│   ├── buda_cli.py             # CLI Tool
│   └── buda_viz.py             # Visualization
├── tests/
│   ├── features/               # Gherkin .feature files
│   ├── test_bundler.py         # Python BDD tests
│   └── ...
└── extern/
    └── pybind11/               # (Submodule) The binding library
```

## Quick start

```bash
source bin/activate                   # per shell: puts bin/ on PATH, sets PYTHONPATH
bb                                    # build the C++/pybind11 engine into build/
buda demo/comprehensive_demo.buda     # run a routing flow
u2b test_column_datapath_hvh          # convert a topology unit test to .buda + visualize
```

`bin/activate` must be **sourced**, not executed (a PATH change only affects the
sourcing shell). Without it, invoke the wrappers as `bin/bb`, `bin/buda …`, etc.
See [CLAUDE.md](CLAUDE.md) for the full build / run / test guide.

## License

This project is licensed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for the full text.

