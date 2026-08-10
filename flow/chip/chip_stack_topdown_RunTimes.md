# before runtime improvements on 2026.08.08

(base) ben@Bulents-MacBook-Pro cc % bin/buda -j4 flow/chip/chip_stack_topdown.buda
bin/buda -j4 flow/chip/chip_stack_topdown.buda
[threads] 4 of 8 logical CPUs (--threads)
  open_bdb chip_stack.bdb.sql          0.71s  open_bdb: materialized /Users/ben/src/git/buda/cc/flow/chip/chip_st…
  reserve_top_layers 2                 0.11s  [LayerCaps] reserving the top 2 layer(s) (M10, M11) for level 3: 14…
  derive_busterms 2                    0.48s  derive_busterms: 495 busterms written (depth 0..2).
  add_blocks_from_bdb 0                0.01s  [BDB] Added 1 blocks at depth 0 (mode=deepest)
  add_blocks_from_bdb 1 skip           0.00s  [BDB] Added 6 blocks at depth 1 (mode=skip)
  add_blocks_from_bdb 2 skip           0.02s  [BDB] Added 488 blocks at depth 2 (mode=skip)
  set_max_bundle_bits auto             0.00s  [Bundler] bundle bit bound: auto (busterm edge) (applies at the nex…
  run_hier_bundler depth 2             1.34s  HierBundler: 660 hbundles (D1: 100, D2: 560)
  generate_hier_topologies            36.86s  generate_hier_topologies: 660 bundles, 16427 total candidates
  run_planner hier 5 signal_tracks    53.38s  run_planner hier: 660 wrappers after expansion
! run_nuts                            21.52s  [2 warn] [NUTS] 1794 segments placed. Track overlaps: 30, Interval …
! run_detailed_nuts                   13.35s  [82 warn] [DetailedNUTS] 31743 net segments placed, 459 bits unplac…
  negotiate_congestion                24.74s  [negotiate] done: metric 451 (ovl 30)->311 (ovl 22) after 1 accepte…
  report_wl                            0.54s  [report_wirelength] total detailed WL = 39315491 over 660 bundle(s)…
  check_design                         1.20s  Total: 325 violation(s) in 29 group(s) across 24 bundle(s). Use --v…
[buda_viz] heatmap: labelling 40 worst of 228 overflow cells (colour shows the rest).

# after the parallel refine sweep + batched screening (2026-08-10)

Branch `claude/chip-stack-speedup` — `refine_selection` full trials on the
C++ sweep pool (full-trial mode + adaptive chunking) and batched parallel
fixed-context screening for both refine and ripup chunk builds.  Measured on
a 4-core container, `--threads 4`, on the `*_heal` variants (the flows above
with `ripup_reroute` + `refine_selection` + a second healer round appended);
endpoints byte-identical to a main-worktree baseline in every run:

| command            | bottomup base | bottomup new | topdown base | topdown new |
|--------------------|--------------:|-------------:|-------------:|------------:|
| negotiate #1       |        76.3s  |       76.3s  |       12.9s  |      14.0s  |
| ripup #1           |        67.9s  |       44.2s  |       91.8s  |      88.5s  |
| refine #1          |       433.1s  |      170.2s  |      151.5s  |     140.1s  |
| negotiate #2       |         7.1s  |        8.1s  |        1.9s  |      1.9s   |
| ripup #2           |       493.0s  |      312.3s  |       99.5s  |      91.4s  |
| refine #2          |       251.5s  |       58.1s  |      210.0s  |     150.9s  |
| **total**          |     **1386s** |     **705s** |     **626s** |    **548s** |

The profiling that drove it: refine was 58% of the two flows' combined
runtime, and INSIDE refine the sequential fixed-context screen (10 696 +
5 129 candidates at ~20 ms each) out-costed the trial solves.  Wider pools
(8-core) scale further.  Ripup's stage-b residual is its already-parallel
stall-sweep volume — further cuts there are trial-volume levers, not
parallelism.

# heal-to-clean experiment matrix (2026-08-10)

Baseline `*_heal` endpoints were budget-exhausted, not stalled: every ripup
iteration up to the default max_iter=10 committed an improving move, and
refine hit its 30-move budget in every call.  All runs 4-core, `--threads 4`.

**topdown** (baseline 583s, 12 opens / 5 ovl — bundle 893):

| scheme | endpoint | time | final detailed-relevant WL |
|---|---|---:|---:|
| x1: `ripup 30` + `refine 60` ×2 | **CLEAN** | 3572s | 2,320,490 |
| x2: interleaved `negotiate; ripup 12; refine` ×3 | **CLEAN** | 1975s | 2,284,720 |

x2 adopted: ripup's cheap screened-scan commits dry up after ~10-12
iterations (x1: psweep 2638s of the 2838s ripup); interleaved
negotiate/refine reshape contention and refill the cheap pipeline.

**bottomup** (baseline 705s, 30 opens / 23 ovl — bundles 86/246/521):

| scheme | endpoint | time |
|---|---|---:|
| x1: `ripup 30` + `refine 60` ×2 | 12 opens (ovl 11) — bundle 86 | 1795s |
| x2: x1 + third round `negotiate 10 press; ripup 20; refine 60` | 12 opens (ovl 10) | 2108s |
| x3: x1 + `set_max_bundle_bits 8 for top_bus69` | 8 opens (ovl 25) — bundle 246 | 1140s |
| x4: x3 + `set_max_bundle_bits 4 for top_bus16` | **CLEAN** | 2022s |

x4 adopted.  Bundles 86 (`top_bus69_w16`) and 246 (`top_bus16_w8`) are the
same shape: 5-endpoint cross-instance fan-outs whose seg strand is a dynamic
junction conflict (seg cannot reach its partner within the slide window,
closed by partner stretch) — refused by all 78 candidate re-pins AND by a
pressed unpinned negotiate replan.  The scoped bundle-bit cap (the
documented width-doomed-seat recipe) splits each into independently seated
parts; healers finish the rest.  Press (x2) measured ineffective here —
the stall is not price-sensitive.
