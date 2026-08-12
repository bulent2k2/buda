# BUDA Internal Design Notes & Architecture Index

This directory contains deep-dive architectural documents, cross-subsystem interaction notes, algorithmic design specs, and empirical analysis reports for developers working on the BUDA codebase.

## Subsystem Interaction & Healers
- [big-picture-2026-07-22.md](big-picture-2026-07-22.md) — Cross-subsystem interaction map: how topology generation, congestion planning, and healers (`negotiate_congestion` + `ripup_reroute`) interlock.
- [healer_effectiveness_2026-07.md](healer_effectiveness_2026-07.md) — Quantitative effectiveness analysis of PathFinder model correction and measured ripup-reroute search.
- [single_source_topo_truth.md](single_source_topo_truth.md) — Single source of truth model for topology representation, candidate UIDs, and connectivity annotations.

## Hierarchical & Bottom-Up Planning
- [hier_bottom_up_planning.md](hier_bottom_up_planning.md) — Bottom-up template planning (`set_bottom_up`), rotation-class clone templates (`<cell>90`), and track-phase alignment (`align_bottom_up`).
- [bdb_hier_options.md](bdb_hier_options.md) — Hierarchical options, cell instantiations, and BDB database representation.

## Database & Physical Interchange
- [bdb_test_data.md](bdb_test_data.md) — Diffable `*.bdb.sql` database text fixtures, SQLite serialization, and the `bdb_input` test fixture.
- [gds_oa_interchange.md](gds_oa_interchange.md) — GDSII layout import/export (Phases G0–G4: TEXT label net extraction, layer mapping, deterministic GDS headers) and OpenAccess bridge spec.

## Solver & Algorithm Internals
- [topology_tree_gen_design.md](topology_tree_gen_design.md) & [multi_trunk_datapath.md](multi_trunk_datapath.md) — Multi-trunk Hanan tree generation and datapath routing algorithms.
- [nuts_dnuts_refactor.md](nuts_dnuts_refactor.md) & [detailed_nuts_engine_options.md](detailed_nuts_engine_options.md) — Abstract NUTS rectangle packing and Detailed NUTS bit-level track snapping.
- [planner_signal_track_capacity.md](planner_signal_track_capacity.md) & [ksegs_default_audit.md](ksegs_default_audit.md) — Congestion planner capacity calculations and `kSegsRel` cost tuning.

## Test Infrastructure & Tooling
- [feature_coverage_plan.md](feature_coverage_plan.md) — Gherkin/pytest-bdd feature specification layer and tag vocabulary (`@landed`, `@future`, `@doc`, `@orphaned`).
- [macos_app_bundles.md](macos_app_bundles.md) — Native macOS `.app` bundle launchers (`bin/Buda.app` and `bin/Floorplanner.app`).
- [packaging.md](packaging.md) — `pip install .` / `pip install -e .` (`pyproject.toml` + scikit-build-core): why the wheel folds `build`/`src`/`tools` into one `buda_runtime` directory, the `BUDA_ARCH=none` and editable-install residuals, and what is deliberately not here (no wheel matrix).
- [test/suite_analysis.md](test/suite_analysis.md) & [test/runtime_analysis.md](test/runtime_analysis.md) — Test suite tiering (`fast`, `mid`, `slow`) and performance benchmarks.
