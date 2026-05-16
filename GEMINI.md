# BUDA: Interconnect Planning System

BUDA is an open EDA tool designed to help plan interconnect on a microchip. It focuses on the early stages of physical design, providing automated bundling, topology generation, and congestion-aware planning.

## Core Mandates

### Engineering Standards
- **Industry Standards:** Aim for integration with industry standards such as **Open Access (OA)** for data models, **GDS** for layout, **SPICE** for circuit simulation, and **Verilog** for netlists.
- **Algorithm Quality:** Prioritize modern algorithms. For future improvements, enhance topology generation with **two or more trunks** and the **Minimum Steiner Tree (MST)** heuristic for large fan-out bundles/buses.
- **Verification:** Maintain high test coverage. Use the Gherkin-based features in `test/tests/features/` and Python tests in `test/tests/`.

### Development Lifecycle
1.  **Setup:** Define layers (`def_layer`), floorplan (`add_block`), and netlist (`add_net`/`add_bus`) in `.buda` scripts.
2.  **Bundling:** Group nets using `run_bundler`.
3.  **Topology Generation:** Enumerate routing candidates using `generate_topologies` or `generate_topologies_for_bundle`.
4.  **Global Planning:** Select optimal topologies and assign layers using `run_planner`.
5.  **Track Assignment:** Use `run_nuts` (Non-Uniform Track Sharing) for final 1.5-D track placement.

## Project Structure

- `buda_system_v2/`: Current active development version.
  - `src/`: C++ engines and Python bindings/CLI.
    - `bundler.cpp`/`.h`: Engine for grouping nets.
    - `topology.cpp`/`.h`: Engine for generating routing candidates.
    - `global_router.cpp`/`.h`: Congestion-aware planner.
    - `nuts.cpp`/`.h`: Track assignment solver.
    - `buda_cli.py`: Main entry point for executing `.buda` scripts.
    - `buda_viz.py`: Matplotlib-based visualization.
  - `flow/`: Example `.buda` scripts and test cases.
  - `docs/`: Design documentation.
- `buda_system/`: Legacy/alternative implementation.
- `test/`: Comprehensive test suite.

## Key References
- [Principles](CLAUDE.md)
- [BUDA Script Reference](docs/BUDA_SCRIPT_REFERENCE.md)
- [Topology Generation](docs/topology_generation.md)

## Future Roadmap
- [ ] **MST Heuristic:** Implement Minimum Steiner Tree for large fan-out bundles.
- [ ] **Multi-Trunk Topologies:** Support more complex Z/U shapes with multiple trunks.
- [ ] **Industry Integration:** Develop import/export for OA, GDS, SPICE, and Verilog.
