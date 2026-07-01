# Wishlist — Topology generation & connectivity

Deferred follow-ups for topology generation (`src/topology.cpp`) and the
connectivity model (`src/conn_topology.cpp`). Index: [`wishlist.md`](wishlist.md).

## True along-flex trunk DOF (Stage C of the flexible-root re-arch)

**Context.** The coverage-driven flexible trunk span (PR on `claude/topo-gen-b4`)
makes a trunk's endpoints span exactly from the lowest busterm it taps to the
topmost stub centerline — minimal, no dead wire — but only **under
`double_detour`**, and the minimisation is computed at GENERATION (stub centerline
+ near-face coverage of pass-through blocks).  A stub's slide still comes only from
its busterm face intersected with the *generated* spine extent; NUTS
`do_span_adjustments` contracts/extends a spine end **only where the extreme
connection sits at the endpoint** (it SETs there) — a stub at a mid/T-junction is
extend-only, and there is no along-direction pull.  So the generated span is the
binding one (we place stubs at centerlines specifically so the extreme stub keeps
a positive slide window), and the behaviour is gated off by default to avoid
disturbing candidate rankings (always-on far-face traversal inflated V-trunk WL
and flipped planner selections).

**Wish.** A first-class **along-flex DOF** so a trunk spine's endpoints are a
*range* resolved by pull, not a fixed generated coordinate:
- Add `along_lo`/`along_hi` *bounds* (+ an `along_pull`) to `ConnSeg`
  (`src/conn_topology.h`), computed in `compute_net_pull` (today perp-only,
  `src/conn_topology.cpp`).
- Teach NUTS `do_span_adjustments` / `tighten_pulls` (`src/nuts.cpp`) to contract a
  spine end toward the pull-optimal coordinate even at a mid-junction, never past a
  busterm-face anchor or a pass-through coverage requirement.

**Payoff.** The flexible-root span could then be **always-on** (not just
`double_detour`): trunks would generate tight, gain slide room from the DOF, and
contract to minimal honest wirelength — eliminating the ranking-inflation that
forced the `double_detour` gate, and letting the planner prefer the region-4
pass-through trunk on its merits. Also unlocks always-on generation of the
"region-4" pass-through trunk (e.g. `TRUNK_V@x5772` in
`flow/big_data_test/big2/b4_bus_077.buda`) instead of only under `double_detour`.
