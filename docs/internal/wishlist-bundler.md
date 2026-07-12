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
**Remaining follow-on:** a CONVERGENT/COMBINED mode for the HIER bundler
(`run_hier_bundler` supports STRICT/BIDIRECTIONAL only — scope decision
needed; the depth-aware signature may split leaf-level merge groups
differently). Details: [`convergent_bundling.md`](convergent_bundling.md).

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
