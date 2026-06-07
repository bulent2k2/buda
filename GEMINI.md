# BUDA: Interconnect Planning System

BUDA is an open EDA tool designed to help plan interconnect on a microchip. It focuses on the early stages of physical design, providing automated bundling, topology generation, and congestion-aware planning.

## Core Mandates

### Engineering Standards
- **Industry Standards:** Aim for integration with industry standards such as **Open Access (OA)** for data models, **GDS** for layout, **SPICE** for circuit simulation, and **Verilog** for netlists.
- **Algorithm Quality:** Prioritize modern algorithms. For future improvements, enhance topology generation with **two or more trunks** and the **Minimum Steiner Tree (MST)** heuristic for large fan-out bundles/buses.
- **Verification:** Maintain high test coverage. Use the Gherkin-based features in `test/tests/features/` and Python tests in `test/tests/`.

### Development Lifecycle
1.  **Setup:** Define layers (`def_layer`), floorplan (`add_block`), keepout zones (`add_keepout`), and netlist (`add_net`/`add_bus`) in `.buda` scripts.
2.  **Bundling:** Group nets using `run_bundler`.
3.  **Topology Generation:** Enumerate routing candidates using `generate_topologies` or `generate_topologies_for_bundle`.
4.  **Global Planning:** Select optimal topologies and assign layers using `run_planner`.
5.  **Track Assignment:** Use `run_nuts` (Non-Uniform Track Sharing) for final 1.5-D track placement.

## Project Structure

- `src/`: C++ engines, physical database (BDB), and Python bindings/CLI.
  - `bundler.cpp`/`.h`: Engine for grouping nets.
  - `bundle_refiner.cpp`/`.h`: Engine for refining net bundling.
  - `topology.cpp`/`.h`: Engine for generating routing candidates.
  - `conn_topology.cpp`/`.h`: Rich connectivity generator and analysis.
  - `congestion_planner.cpp`/`.h`: Congestion-aware planner (formerly `global_router`).
  - `nuts.cpp`/`.h`: Track assignment solver.
  - `routing_grid.cpp`/`.h`: Physical track patterns and grid overrides.
  - `detailed_nuts.cpp`/`.h`: Snaps bus segments to concrete signal tracks.
  - `bdb.cpp`/`.h`: Buda Physical Design Database (SQLite-backed layout store).
  - `verify.cpp`/`.h`: Verification and design rule checking.
  - `buda_cli.py`: Main entry point for executing `.buda` scripts.
  - `buda_viz.py`: Matplotlib-based visualization.
- `flow/`: Example `.buda` scripts and test cases.
- `docs/`: Design documentation.
- `test/`: Comprehensive pytest/pytest-bdd test suite.

## Key References
- [Principles](CLAUDE.md)
- [User Guide](docs/USER_GUIDE.md)
- [BUDA Script Reference](docs/BUDA_SCRIPT_REFERENCE.md)
- [Topology Generation](docs/topology_generation.md)

## Future Roadmap
- [ ] **MST Heuristic:** Implement Minimum Steiner Tree for large fan-out bundles.
- [ ] **Multi-Trunk Topologies:** Support more complex Z/U shapes with multiple trunks.
- [ ] **Industry Integration:** Develop import/export for OA, GDS, SPICE, and Verilog.
