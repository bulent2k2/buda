# BUDA Test Suite Runtime Analysis

Per-test runtime breakdown for the BUDA pytest suite, and the three-tier
marker scheme that lets `bb` run a fast subset for the inner dev loop.

> Regenerate the numbers with:
> ```bash
> MPLBACKEND=Agg pytest -o addopts="" -p no:cacheprovider --durations=0 -q
> ```
> Absolute times are from the development machine (Apple Silicon); treat them
> as relative. Last measured: 2026-06-19.

## Tiers (markers)

Tests are split into three cumulative tiers via pytest markers (registered in
`pytest.ini`). The default run excludes `mid` and `slow`:

| Tier | Marker | What | Tests | Time | Run with |
|---|---|---|---:|---:|---|
| **fast** | *(none)* | unit / component tests | ~434 | **~8.5s** | `bb -t` / `pytest` |
| **mid** | `mid` | full-pipeline `.buda` integration (`test_flow_scripts.py`) | 19 | **~19s** | `bb -m` |
| **slow** | `slow` | SA/GA placement-optimizer storms | 2 | **~19s** | `bb -s` |

Cumulative wall-clock: fast ≈ 8.5s, fast+mid ≈ 27s, fast+mid+slow ≈ 47s.

- The **fast** tier is the default inner-loop check — it skips both the 19s of
  subprocess-spawning flow scripts and the 19s of optimizer convergence.
- The **mid** tier is marked at module level (`pytestmark = pytest.mark.mid`)
  in `test_flow_scripts.py`.
- The **slow** tier is the two `test_optimize_demo_*` tests in
  `test_floorplanner_commands.py`, each `@pytest.mark.slow`.

`pytest.ini` sets `addopts = -m "not slow and not mid"`; `bb -m` overrides it to
`-m "not slow"`, and `bb -s` clears it to run everything.

---

## Bottlenecks

### 1. PlacementOptimizer SA/GA storms (~19s, the `slow` tier)
`test_optimize_demo_tc1_overlap_storm` and `test_optimize_demo_tc2_fixed_io`
each run `run_sa` with `max_iter=12_000` to legalize 40 blocks / 80 buses ×
64 bits. ~9.5s apiece. A convergence sweep showed both seeds reach `overlap=0`
by ~8k iterations, so 12k keeps a ~1.5x margin; the prior 50k was ~41s apiece
(77% of the full-suite runtime). They are deselected from every run except
`bb -s`.

### 2. End-to-end flow scripts (~19s, the `mid` tier)
The 19 tests in `test_flow_scripts.py` each `subprocess.run` `buda_cli.py` on a
`.buda` file, paying Python startup + module import + a full
Bundling→Topology→Planner→NUTS pass (~1s each). They catch end-to-end
regressions the fast unit tests miss, so they live in the `mid` tier rather
than `slow`.

---

## Per-file totals (call time, full suite)

| File | Tests | Total |
|---|---:|---:|
| test_floorplanner_commands.py | 17 | ~20s |
| test_flow_scripts.py | 19 | 19.04s |
| test_net_pull.py | 2 | 1.89s |
| test_topo_explorer_focus_on_cycle.py | 1 | 0.96s |
| test_bdb.py | 65 | 0.85s |
| test_topo_explorer_pin_badge.py | 2 | 0.85s |
| test_topo_explorer_single_pin.py | 1 | 0.27s |
| test_hier_bundler.py | 20 | 0.20s |
| test_multi_level_trunk.py | 5 | 0.14s |
| test_nuts_dogleg.py | 10 | 0.10s |
| test_hier_planner.py | 10 | 0.10s |
| test_hier_topology.py | 8 | 0.08s |
| test_pull_preference.py | 5 | 0.05s |
| *(all other files)* | — | < 0.05s each |

The remaining ~300 tests (BDB row ops, hier bundler/topology/planner units,
NUTS dogleg, connectivity, keepout, multicast, …) are sub-5ms each and not
listed individually — see the `--durations=0` output above to enumerate them.

---

## Slowest individual tests (≥ 0.05s)

| Duration | Tier | Test |
|---:|---|---|
| ~9.5s | slow | test_floorplanner_commands.py::test_optimize_demo_tc2_fixed_io |
| ~9.5s | slow | test_floorplanner_commands.py::test_optimize_demo_tc1_overlap_storm |
| 1.79s | mid | test_flow_scripts.py::test_10_four_level_scale_one_bundle_per_bus |
| 1.11s | mid | test_flow_scripts.py::test_channel_stress_packs_clean |
| 1.04s | mid | test_flow_scripts.py::test_two_rotated_buses |
| 1.02s | mid | test_flow_scripts.py::test_planner3_window_capacity_avoids_double_booked_trunk |
| 1.01s | mid | test_flow_scripts.py::test_08_cross_level_detour_trunk_connectivity |
| 1.00s | mid | test_flow_scripts.py::test_two |
| 0.98s | fast | test_net_pull.py::test_pull2_flow_stubs_stay_within_target_face |
| 0.98s | fast | test_floorplanner_commands.py::test_floorplanner_commands_run_hbundle_flow_from_verilog |
| 0.97s | mid | test_flow_scripts.py::test_nuts_relax_range_reg_pinned_u_detour |
| 0.96s | fast | test_topo_explorer_focus_on_cycle.py::test_focus_pinned_topo_on_open_and_after_cycling |
| 0.96s | mid | test_flow_scripts.py::test_planner4_keepout_overflow_forces_detour |
| 0.95s | mid | test_flow_scripts.py::test_sel_topos_typo |
| 0.94s | mid | test_flow_scripts.py::test_two_rotated |
| 0.93s | mid | test_flow_scripts.py::test_comprehensive_demo |
| 0.92s | mid | test_flow_scripts.py::test_ripup1_replans_earlier_bundle_to_free_capacity |
| 0.92s | mid | test_flow_scripts.py::test_nuts_corner_overlap_vertical_constraint |
| 0.92s | mid | test_flow_scripts.py::test_nuts_corner_overlap_3layer |
| 0.92s | mid | test_flow_scripts.py::test_09_local_global_compete_reservation_avoids_ripup |
| 0.91s | fast | test_net_pull.py::test_pull1_flow_placement_clean_and_compact |
| 0.91s | mid | test_flow_scripts.py::test_ripup2_targets_actual_blocker |
| 0.91s | mid | test_flow_scripts.py::test_planner5_span_scaled_penalty_drops_short_stub |
| 0.91s | mid | test_flow_scripts.py::test_nuts_corner_touch_xlayer |
| 0.91s | mid | test_flow_scripts.py::test_four_blocks_3_bundles |
| 0.43s | fast | test_topo_explorer_pin_badge.py::test_script_pin_without_planner_is_pinned_only |
| 0.42s | fast | test_topo_explorer_pin_badge.py::test_script_pin_with_planner_is_planner_selected_pinned |
| 0.27s | fast | test_topo_explorer_single_pin.py::test_explorer_pins_at_most_one_topology_per_bundle |
| 0.06s | fast | test_multi_level_trunk.py::test_bitrunk_uses_a_feedthru_block_as_a_relay_junction |
| 0.05s | fast | test_multi_level_trunk.py::test_bitrunk_vhv__root_v_trunk_feeds_two_branch_h_trunks |

A handful of `fast`-tier tests (net_pull, topo_explorer, the hbundle flow in
floorplanner_commands) sit just under 1s because they too spawn a subprocess or
build a small UI. They are individually cheap enough to keep in the default run.

---

## Future optimization ideas

- **~~Lower SA iterations in the slow tests.~~** *(Done 2026-06-19.)* Dropped
  `max_iter` from 50k to 12k in both `test_optimize_demo_*` tests after a
  convergence sweep showed both seeds reach `overlap=0` by ~8k iterations. Cut
  the slow tier from ~78s to ~19s while keeping a ~1.5x margin.
- **In-process flow tests.** The `mid` tier pays Python startup + import per
  test via `subprocess.run`. Importing `buda_cli` and invoking its parser
  in-process would remove most of the ~1s/test overhead — at the cost of losing
  true process-isolation of the CLI entry point.
