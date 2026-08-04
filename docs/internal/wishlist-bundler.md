# Wishlist — Bundler

Deferred follow-ups for net bundling (`src/bundler.cpp`). Index:
[`wishlist.md`](wishlist.md).

## Multi-source (fan-in) topology support to make CONVERGENT bundling sound ✅

**DONE (2026-07-11).** As-built: `_bundle_endpoints`
(`src/buda_session/hier.py`) derives generation endpoints from ALL of a
bundle's nets — a multi-driver CONVERGENT bundle roots at the shared sink
with every driver block as a leaf, and the existing multicast trunk+branch /
MST machinery (direction-agnostic root-to-N-leaves) routes the fan-in with
the arrows reversed; single-driver bundles keep the historical first-net
derivation byte-identically. The **net-driver fidelity check** landed as
`NET_DRIVER_OPEN` (`_net_driver_fidelity`, `src/buda_session/reports.py`):
every net endpoint block must appear in the topology's
`connected_block_names` contract (flat flow; skips empty-contract USER
candidates). The CONVERGENT warning is downgraded to a note. The realization is
**per-bit tapered** (`Topology::seg_bits` / `BusSegment.bit_list`): each
segment carries only the bits whose driver→sink path uses it, with planner
charging, NUTS widths, and DNUTS emission all member-bit-scoped — no net's
wire lands on another driver's block. Acceptance
tests inverted from the pipeline test's collapse assertions + a mixed
shared/distinct-driver case (with taper/width/via assertions); QoR showcase `demo/ariane136_l2` (12 drivers →
cpu_core, 1024 bits) generates the fan-in tree and passes `check_design`
clean. **COMBINED landed (2026-07-12):** `run_bundler COMBINED` = the join of
CONVERGENT and BIDIRECTIONAL (union-find chains), with `set_bundling`
per-prefix permission overrides and the `set_max_bundle_bits <N|auto>`
balanced bus-preserving split pass (auto = shortest-busterm-edge cap).
**HIER modes landed (merged 2026-07-14, PR #276):** `run_hier_bundler`
accepts all four strategies per bundling depth (same-level nets;
multi-driver differing-set groups become `FANIN:` bundles routed as
per-bit tapered trees in their frame — the taper re-derived per instance
at expansion from donor metadata — cell-local fan-in templates merge with
replicas, `set_bundling` overrides apply to both bundlers, cross-level
pairs merge under BIDIRECTIONAL and COMBINED alike).  The depth-aware
semantics ARE the scope decision: each net bundles once at its most
specific level, so fan-ins split across subtrees/depths stay separate
routing problems.  Review hardening sealed the emission seam: reasons are
never a driverless `REC:…` (pure CONVERGENT was stranding single-driver
cross-block buses), and pure-mode same-set groups keep the historical
ep0 emission + 2-pin pool (the all-drivers emission is fan-in /
general-path only), each pinned by a seam-targeted regression test.
**Remaining corners** (opens.md item 8) — both now ✅ CLOSED:
- ✅ **Cross-level fan-in grouping** — CONVERGENT/COMBINED group
  cross-level nets by their shared receiver set into one fan-in bundle
  (per-net `net_drivers`/`net_receivers` + a persisted `FANIN` reason);
  generation roots the tree at the shared sink with each deep driver as
  a per-bit tapered leaf, and a resumed session recovers the endpoints
  from the reason (`test_hier_cross_level_fanin.py`).
- ✅ **Hier `set_max_bundle_bits`** — the balanced split runs at
  `run_hier_bundler` on TEMPLATE bundles before per-instance expansion,
  so each part is its own template and the split propagates identically
  through the template↔replica linkage; every HBundle hier field is
  preserved per part, the AUTO cap resolves a cell-local leaf to a
  congruent instance's child footprint, and a fan-in part re-scopes its
  per-net endpoints + FANIN reason to the leaves its bits touch
  (`test_hier_max_bundle_bits.py`).
Details: [`convergent_bundling.md`](convergent_bundling.md).

**What (historical):** `run_bundler CONVERGENT` groups nets by shared receiver only, so a
bundle can span several **different driver blocks** at different locations (a
many-to-one fan-in). Topology generation modelled a bundle by a single `src→dst`
pair, so such a bundle routed from ONE arbitrary driver and the others were
silently left unrouted — physically wrong. The fix gave topology generation a
**multi-source / fan-in tree** shape (several source busterms merging toward the
shared sink), and added the missing
**net-driver fidelity check** to `check_design` (before, it validated a
topology's internal self-consistency, not that every original net driver is
actually attached — which is why the gap slipped through). `CONVERGENT` is now
genuinely useful for real fan-in patterns (multiple masters → one slave,
write data → memory) instead of a foot-gun.

(Note: `BIDIRECTIONAL` does **not** need this — it groups nets connecting the
**same** blocks in mixed directions, so the single block-to-block trunk already
routes every net. It is sound today.)

**Why deferred:** No faithful physical representation exists yet; `CONVERGENT`
only matches routing when it degenerates to `STRICT`. Shipped for now: the CLI
honours the `STRICT|CONVERGENT|BIDIRECTIONAL` argument (was silently ignored) and
prints a warning when `CONVERGENT` is selected, rather than misrouting silently.

**Where to start:** `src/topology.cpp` (single `src→dst` derivation per bundle;
reuse the `trunk_mst` / `compute_mst` machinery in `src/conn_topology.cpp`),
`src/verify.cpp` `check_topo` (add the driver-attachment check), and
`src/bundler.cpp` (`generate_signature`). Full investigation, evidence, and
verdict: [`convergent_bundling.md`](convergent_bundling.md). Pipeline test that
locks in the current behaviour: `test/tests/test_bundler_convergent_pipeline.py`.

**Test vehicles (assessed 2026-07-10):** the pipeline test is an
inversion-ready acceptance harness (its one-row assertion flips to
all-rows, its no-fidelity-check assertion flips to the new check firing),
and a corpus scan identified the realistic flow-test candidates — the
mempool trio (cores→banks crossbar, all-to-all groups), ariane_core's
writeback fan-in, large_scale_demo's 7-master NoC merge, and
`ariane136_l2` (already CONVERGENT, 12 drivers / 1024 bits) as the QoR
showcase. Details + incidental-collision exclusions:
[`convergent_bundling.md`](convergent_bundling.md) → *"Test-case
assessment"* and *"Corpus scan"*.

---

## Supply-driven bundle bit cap — MEASURED AND REJECTED (2026-08-04)

The scoped cap (`set_max_bundle_bits <N> for <prefix>`, PR #582) removes a
width-doomed bundle without taxing the design, but choosing `N` is manual.
The obvious next step is to derive it: run the flow, read the doomed-seat
census, cap each doomed bundle at the largest part its seat can actually
host, re-run — a feedback loop rather than a static knob.

Built as a tool (deliberately: no reason to touch the engine before the loop
proves out) and run on `chip3a_bottomup`, the corpus's worst vehicle for this
class (15 supply-doomed seats / 182 guaranteed-stranded bits):

| iteration | overlaps | unplaced | viol | abstract WL |
|---|---|---|---|---|
| 0 — baseline | 297 | 1658 | 90 | 2571623 |
| 1 — 10 derived caps | 321 | **1386** | 96 | 2768219 (+7.6%) |
| 2 — 13 caps | 359 | 1346 | 98 | 3196879 (+24.3%) |
| 3 — 15 caps | 368 | 1330 | 96 | 3229825 (+25.6%) |

**Three findings, in order of importance.**

1. **It does not converge.** Every iteration finds NEW doomed seats,
   including on buses it already capped — `top_bus94_w10` was capped at 6,
   and its part then needed 5 against a pool of 4; `top_bus20_w16` capped at
   9, its part needed 8 against 5.  Splitting moves a bundle's parts to
   different windows with different supply, so the fixpoint the loop is
   chasing recedes as it walks.  This is the same effect that made the
   hand-picked halving only clear 3 of 9 seats.

2. **The cost grows faster than the benefit.** Unplaced improves 1658 → 1386
   → 1346 → 1330: the first pass takes 272 of the 328 total, the next two
   take 40 and 16.  Overlaps rise monotonically (297 → 368) and wirelength
   ends **+25.6%**.  By the corpus's componentwise standard no iteration is
   an improvement — one metric moves the right way and two move the wrong
   way, worsening with each pass.

3. **It degenerates on a zero-supply seat.** Iteration 2 derived `cap 1` for
   a 16-bit bus whose seat pool was 0 — sixteen one-bit bundles.  Splitting
   cannot fix a window with no signal tracks at all, and a supply-derived cap
   has no way to tell that apart from a window that is merely too small: it
   just caps harder.  Any revival needs a `LAYER_STARVED`-style guard that
   refuses to cap a seat whose pool is zero (see
   [`wishlist-healer.md`](wishlist-healer.md) → *"Class-level TRACK
   negotiation"* for the verdict vocabulary).

**What survives.** The derivation itself is sound and better than guessing:
on `mix2_fast_on_aligned_sql` it reproduces the hand-picked cap
automatically, and on chip3a its first pass recovers 272 stranded bits where
hand-picked halving recovered 147.  The problem is not the estimate, it is
that repeated re-bundling is a poor instrument for a supply shortage — the
same conclusion the class-level TRACK negotiation reached from the other
direction, and the reason a 6-layer → 10-layer stack dissolved that open's
whole market.

**Two false starts worth recording**, both of which silently produced "no
seats to cap" and would mislead anyone rebuilding this:

- Deriving the cap from raw admission SUPPLY rather than FREE tracks: b61's
  M4 has 17 tracks for 16 bits and looks adequate, but 16 are occupied —
  which is precisely why the TOP re-seat heal already tried M4 and failed.
- Treating an empty span-clear track list as "fall back to the raw pool":
  b61's M2 reports supply 16 entirely from the midpoint fallback while ZERO
  tracks clear the span, so the loop concluded a layer could host the bus.

Both are the same lesson the `FREE_SIBLING` verdict carries: static supply is
not availability, and the midpoint fallback is not a seat.
