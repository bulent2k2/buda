# BUDA Test Suite Runtime Analysis

This report provides a detailed breakdown of the unit test execution times for the BUDA project, based on the test run using `pytest --durations=0`.

## Executive Summary

- **Total Execution Time**: 117.21 seconds (1 minute 57 seconds)
- **Total Tests Run**: 451 tests (410 passed, 2 skipped, 35 xfailed, 4 xpassed)
- **Primary Bottlenecks**: Just **2 tests** account for **77%** of the total runtime:
  - `test_optimize_demo_tc1_overlap_storm` (45.27s)
  - `test_optimize_demo_tc2_fixed_io` (44.90s)
- **Remaining Tests**: The remaining 449 tests are extremely fast, averaging less than **0.06s** per test, with the vast majority taking less than 5 milliseconds.

---

## Slowest Tests (> 0.05s)

| Rank | Test Case / Path | Duration (s) | % of Total | Category / Cause |
|:---:|---|:---:|:---:|---|
| 1 | [test_optimize_demo_tc1_overlap_storm](file:///Users/ben/src/git/buda/agy/test/tests/test_floorplanner_commands.py#L287-L327) | 45.27s | 38.6% | PlacementOptimizer (Simulated Annealing: 50k iterations, 40 blocks, 5120 nets) |
| 2 | [test_optimize_demo_tc2_fixed_io](file:///Users/ben/src/git/buda/agy/test/tests/test_floorplanner_commands.py#L329-L378) | 44.90s | 38.3% | PlacementOptimizer (Simulated Annealing: 50k iterations, 40 blocks, 5120 nets) |
| 3 | [test_10_four_level_scale_one_bundle_per_bus](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L368-L382) | 1.85s | 1.6% | End-to-end multi-level hierarchical flow script execution |
| 4 | [test_floorplanner_commands_run_hbundle_flow_from_verilog](file:///Users/ben/src/git/buda/agy/test/tests/test_floorplanner_commands.py#L214-L236) | 1.02s | 0.9% | End-to-end floorplanner command execution with Verilog input |
| 5 | [test_channel_stress_packs_clean](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L255-L270) | 1.00s | 0.9% | End-to-end channel stress flow script execution |
| 6 | [test_09_local_global_compete_reservation_avoids_ripup](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L322-L343) | 0.96s | 0.8% | End-to-end flow script |
| 7 | [test_ripup2_targets_actual_blocker](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L351-L365) | 0.95s | 0.8% | End-to-end flow script |
| 8 | [test_planner5_span_scaled_penalty_drops_short_stub](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L345-L349) | 0.94s | 0.8% | End-to-end flow script |
| 9 | [test_planner3_window_capacity_avoids_double_booked_trunk](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L236-L252) | 0.93s | 0.8% | End-to-end flow script |
| 10 | [test_08_cross_level_detour_trunk_connectivity](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L221-L224) | 0.92s | 0.8% | End-to-end flow script |
| 11 | [test_four_blocks_3_bundles](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L202-L208) | 0.92s | 0.8% | End-to-end flow script |
| 12 | [test_ripup1_replans_earlier_bundle_to_free_capacity](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L307-L320) | 0.92s | 0.8% | End-to-end flow script |
| 13 | [test_two_rotated](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L63-L69) | 0.90s | 0.8% | End-to-end flow script |
| 14 | [test_sel_topos_typo](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L226-L234) | 0.90s | 0.8% | End-to-end flow script |
| 15 | [test_nuts_corner_touch_xlayer](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L90-L96) | 0.90s | 0.8% | End-to-end flow script |
| 16 | [test_nuts_corner_overlap_vertical_constraint](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L118-L125) | 0.90s | 0.8% | End-to-end flow script |
| 17 | [test_pull2_flow_stubs_stay_within_target_face](file:///Users/ben/src/git/buda/agy/test/tests/test_net_pull.py#L125-L136) | 0.89s | 0.8% | Net pull detailed test (spawns buda_cli subprocess) |
| 18 | [test_planner4_keepout_overflow_forces_detour](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L286-L298) | 0.89s | 0.8% | End-to-end flow script |
| 19 | [test_two](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L55-L61) | 0.89s | 0.8% | End-to-end flow script |
| 20 | [test_comprehensive_demo](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L71-L81) | 0.89s | 0.8% | End-to-end flow script |
| 21 | [test_pull1_flow_placement_clean_and_compact](file:///Users/ben/src/git/buda/agy/test/tests/test_net_pull.py#L121-L123) | 0.88s | 0.8% | Net pull detailed test (spawns buda_cli subprocess) |
| 22 | [test_nuts_relax_range_reg_pinned_u_detour](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L210-L219) | 0.88s | 0.8% | End-to-end flow script |
| 23 | [test_two_rotated_buses](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L194-L200) | 0.88s | 0.8% | End-to-end flow script |
| 24 | [test_nuts_corner_overlap_3layer](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py#L146-L153) | 0.87s | 0.7% | End-to-end flow script |

---

## Detailed Analysis & Bottlenecks

### 1. PlacementOptimizer Simulated Annealing (SA) Tests (~90.17s total)
The tests [test_optimize_demo_tc1_overlap_storm](file:///Users/ben/src/git/buda/agy/test/tests/test_floorplanner_commands.py#L287) and [test_optimize_demo_tc2_fixed_io](file:///Users/ben/src/git/buda/agy/test/tests/test_floorplanner_commands.py#L329) execute `run_sa` with `max_iter=50_000` to find legal placements for 40 blocks. This involves extensive C++ computing loops under python bindings. While it ensures layout legality, running `50k` iterations for two tests sequentially creates a massive bottleneck.

### 2. End-to-End Flow Script Tests (~20s total)
Tests starting with `test_` under [test_flow_scripts.py](file:///Users/ben/src/git/buda/agy/test/tests/test_flow_scripts.py) run by calling `subprocess.run` on [buda_cli.py](file:///Users/ben/src/git/buda/agy/src/buda_cli.py) over different `.buda` files. Each invocation incurs the Python interpreter startup overhead, imports CMake targets, reads BDB sqlite data, and executes multiple stages (Bundling, Topology, Planner, NUTS). Because there are around 20 flow script tests, their overhead accumulates to about 20 seconds.

---

## Optimization Recommendations

To bring the test execution time down from **1m 57s** to **under 20 seconds**, the following strategies are recommended:

### 1. Reduce SA Iterations in Tests
Check if the simulated annealing checks converge to a legal layout (`overlap < 1.0`) with fewer iterations:
- Reduce `max_iter` from `50_000` to `5_000` or `10_000`.
- Since C++ SA is extremely fast per iteration, dropping this factor will likely scale the runtime of these two tests linearly (e.g., from `45s` down to `4.5s` or `9s` each).

### 2. Introduce pytest Markers for Slow Tests
Mark the extremely slow optimization and integration tests as `slow` so that developers can exclude them during rapid local iterations:
```python
# In test_floorplanner_commands.py
import pytest

@pytest.mark.slow
def test_optimize_demo_tc1_overlap_storm(tmp_path):
    ...
```
Then run fast tests only using `pytest -m "not slow"`.

### 3. Reuse CLI Instances (If Applicable)
For end-to-end tests, instead of executing a separate subprocess for each `.buda` script, import and invoke `buda_cli`'s parser class directly in the Python test process. This removes the python startup and library-loading overhead entirely.
