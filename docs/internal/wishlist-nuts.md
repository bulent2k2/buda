# Wishlist — Abstract & Detailed NUTS

Deferred follow-ups for track assignment (`src/nuts.cpp`,
`src/detailed_nuts.cpp`). Index: [`wishlist.md`](wishlist.md).

## Band-level repack for spread-fit overlap clusters

**What:** After Gap A part 1 + TOP-layer load balancing, big2 is down to 9 NUTS
track overlaps. All 9 are **spread-fit** (the shared Hanan band has room for both
buses — sum of widths <= interval), i.e. pure placement clustering, not
over-capacity. They survive because `NUTSEngine::repair_overlaps`
(`src/nuts.cpp`) only relocates ONE victim per overlap into a gap its own
interval still has free; when a cluster of 3+ buses share a band (e.g. on big2 M7
bundle B79 collides with B65, B26 AND B45) no single-victim move separates them,
and a plateau-move relaxation of the strict-improvement guard was tried and did
nothing (the victims' intervals are already full given the others' positions).

The fix is a **band-level multi-segment repack**: gather the maximal set of
mutually-overlapping segments sharing a (layer, span-overlap, interval-overlap)
cluster and re-distribute all of them at once across the union of their slide
windows (they provably fit — spread-fit), instead of nudging one at a time. The
existing dense `try_repack` (`src/nuts.cpp:~1306`) already packs a member set to
low edges during initial placement; reuse/lift it into `repair_overlaps` as the
cluster resolver.

**Why deferred:** Single-victim repair + the planner load-balancing already took
big2 from 43 -> 9 overlaps; the residual needs a genuinely different (cluster)
algorithm. Out of scope for the current planner-fidelity branch, which is
planner-only.

**Where to start:** `src/nuts.cpp` `repair_overlaps` (~:529) and the dense
`try_repack` lambda (~:1306); `find_overlaps`/`segs_overlap` for cluster
discovery. Verify on `flow/big_data_test/big2/big2.buda`: the 9 residual overlaps
(M4×1, M6×4, M7×3, M2×1 — all spread-fit) should drop toward 0 with no new DNUTS
opens. NOTE: do NOT try to "balance" this away in the planner — evening the V
load (M5 9117 vs M7 5752) was measured to be counter-productive: it pushes load
toward M7 where the overlaps already sit and regressed DNUTS 60 -> 132 with the
overlap count unchanged. The residual is a packer problem, not a load problem.
See `docs/internal/planner_low_layer_over_cell.md`.

## Pre-existing failure: `test_tighten_does_not_trade_pull_for_overlaps` — ✅ RESOLVED (PR #69)

**What:** This `mid`-tier test (`test/tests/test_nuts_pull_repack.py`) asserts the
`tc3a_flat` NUTS solve leaves `<= 2` abstract M7 overlaps, but it produced **3**
(`B59×B74 ×2`, `B56×B79`) — red on `main`.

**Resolution:** Fixed by PR #69 (net_pull only pulls endpoint-setting stubs).
Removing the spurious multicast-trunk pulls — and, with the busterm-tap endpoint
check, the interior-stub pulls — changed `tc3a_flat`'s placement enough that it
now lands at `<= 2` abstract M7 overlaps, so the test is green. The root cause was
exactly the over-applied `net_pull` that drove interior/non-binding stubs to their
slide bounds, congesting the layer; it was never a `tighten_pulls` regression.

## Hard co-placement of joined segments (seg-to-seg junctions) — ✅ RESOLVED

**What:** NUTS kept joined perpendicular segments connected by a *soft*
post-hoc span-extension (`do_span_adjustments`) rather than a placement
constraint, so a CPU-dependent track flip could open a zero-margin corner
(`tc3a_flat` bundle 48) that only DetailedNUTS recovered.

**Resolution (landed in stages):** joins became first-class
(`Topology::seg_conns`, topo-truth Phase 4; persisted at schema v12), the
FP-determinism stopgap + cross-layer coverage invariant made a junction unable
to *open* at NUTS regardless of host, and Part B added junction-aware
placement: a single-junction landing segment *prefers* a track its placed
partner's span already covers (junction-anchored preference in `place_seg`),
and a junction closable only by a large partner stretch is surfaced as a
structured `NUTSResult::junction_infeasibilities` entry consumed by
`ripup_reroute` as a re-pin contender. Design + history in
[`seg_junction_coplacement.md`](seg_junction_coplacement.md); tests in
`test/tests/test_junction_coplacement.py`.
