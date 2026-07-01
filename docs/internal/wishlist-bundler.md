# Wishlist — Bundler

Deferred follow-ups for net bundling (`src/bundler.cpp`). Index:
[`wishlist.md`](wishlist.md).

## Multi-source (fan-in) topology support to make CONVERGENT bundling sound

**What:** `run_bundler CONVERGENT` groups nets by shared receiver only, so a
bundle can span several **different driver blocks** at different locations (a
many-to-one fan-in). Topology generation models a bundle by a single `src→dst`
pair, so such a bundle routes from ONE arbitrary driver and the others are
silently left unrouted — physically wrong. Give topology generation a
**multi-source / fan-in tree** shape (several source busterms merging toward the
shared sink, e.g. an MST/Steiner trunk each driver joins), and add the missing
**net-driver fidelity check** to `check_connectivity` (today it validates a
topology's internal self-consistency, not that every original net driver is
actually attached — which is why the gap slipped through). Then `CONVERGENT`
becomes genuinely useful for real fan-in patterns (multiple masters → one slave,
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
