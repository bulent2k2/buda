@future
# Spec AHEAD of code (not bound — see features/README.md). Each scenario describes
# an intended behavior that is NOT yet implemented, with a pointer to the design
# doc. When one ships, move its scenario to the arc's @landed feature, flip the tag,
# and bind or name its coverage. Cross-referenced from docs/internal/opens.md.
Feature: Roadmap — documented open items & future directions
  As a BUDA maintainer
  I want the ranked open items and future directions captured as readable specs,
  So that the intended behavior is written down before the code, and the spec
  layer leads the work instead of trailing it.

  # ── Bundler follow-on corners (opens #8, wishlist-bundler.md) ──────────────

  Scenario: cross-level fan-in grouping
    # see docs/internal/wishlist-bundler.md "Remaining corners" + opens.md #8
    # Today cross-level nets keep STRICT/BIDIRECTIONAL grouping because their single
    # drv_spec_path metadata cannot describe a multi-driver group.
    Given cross-level nets that share a sink but have different drivers
    When run_hier_bundler groups them with a per-net endpoint record (like same-level net_drivers/net_receivers)
    Then they form one cross-level fan-in bundle instead of falling back to STRICT

  Scenario: hier set_max_bundle_bits propagates through template<->replica linkage
    # see docs/internal/wishlist-bundler.md + opens.md #8
    # The balanced split pass is flat-only today.
    Given a bottom-up cell template whose bundle exceeds the bit bound
    When set_max_bundle_bits splits it in the hier flow
    Then every instance splits identically so templates and replicas stay congruent

  # ── NUTS packing gap (opens #4, wishlist-nuts.md / future/nuts_packing_gaps.md §4) ──

  Scenario: a non-TOP pin-access stub is not span-stretched onto its endpoint leaf
    # see docs/future/nuts_packing_gaps.md §4 + opens.md #4
    # Fix locus is NUTS-side (do_span_adjustments), NOT a planner cost term.
    Given a non-TOP pin-access stub whose endpoint leaf is a keepout
    When NUTS adjusts spans
    Then the stub is clamped at the leaf face (not stretched onto it), so the pin-access bits are not culled

  # ── Topology along-flex (opens #6, wishlist-topo.md — BLOCKED) ─────────────

  Scenario: true along-flex trunk degree of freedom (Stage C)
    # see docs/internal/wishlist-topo.md "True along-flex trunk DOF" + opens.md #6
    # Stage A landed (ConnSeg along_flex/along_pull); the always-on flip is blocked
    # by upstream regressions (far-face traversal inflates V-trunk WL and flips
    # planner selections) — that investigation gates any NUTS-side work.
    Given a trunk with along-axis slack
    When the along-flex DOF is enabled end-to-end
    Then the trunk slides along its axis without inflating wirelength or flipping planner selections

  # ── Feedthru follow-ups (trunk_mst_and_feedthru_plan.md §2.9) ──────────────

  Scenario: straight / I-shape feedthru split
    # see docs/internal/trunk_mst_and_feedthru_plan.md §2.9
    # Today only the multicast TRUNK_H/TRUNK_V builders split at a fed-through busterm.
    Given a point-to-point net passing through a busterm it opted into via set_feedthru
    When generation builds an I_H / I_V (or L / Z) candidate
    Then the spine splits at the busterm faces, as the multicast trunks already do

  Scenario: feedthru_penalty ranks a feedthru candidate below an equivalent stub
    # see docs/internal/trunk_mst_and_feedthru_plan.md §2.9 (the knob does not exist yet)
    Given a feedthru candidate and an equivalent non-feedthru stub candidate
    When the planner scores them with feedthru_penalty modeling the internal-routing cost
    Then the feedthru candidate ranks below the equivalent stub

  # ── Planner rip-up extensions (docs/future/planner_ripup_extensions.md) ────

  Scenario: multi-victim (k-victim) rip-up
    # see docs/future/planner_ripup_extensions.md §1
    Given a band that single-victim rip-up cannot free
    When the planner searches a bounded k=2 victim set
    Then it frees the band by moving two committed bundles together when no single victim suffices

  Scenario: planner-stage negotiated congestion (PathFinder history costs)
    # see docs/future/planner_ripup_extensions.md §3 (distinct from the NUTS-stage
    # negotiate_congestion command, which already shipped)
    Given the reserved run_planner <iterations> argument
    When the planner iterates with history costs kHist on repeat-offender bands
    Then congestion is negotiated across iterations PathFinder-style

  # ── OA interchange (opens #7 — GATED on the Si2 OA SDK) ────────────────────

  Scenario: OpenAccess import/export round-trip
    # see docs/internal/gds_oa_interchange.md + opens.md #7
    # Everything BDB-side is done; the OA half is gated on the proprietary OA C++
    # libraries, behind a BUDA_WITH_OA CMake flag in a separate oa_bridge.cpp.
    Given an OpenAccess design database (oaDesign/oaBlock/oaInst/oaNet)
    When I run import_oa then export_oa against the same BDB API the GDS path uses
    Then the design round-trips through OA so BUDA sits inside an OA-based flow

  # ── bigHalf rr flip (opens #10 — a decision, then a two-line edit) ─────────

  Scenario: bigHalf.buda ships with ripup_reroute enabled
    # see docs/internal/opens.md #10 + flow/big_data_test/ReadMe_bigHalf.md
    # The clean 0/0 endpoint is already CI-guarded by
    # test_bighalf_rr_reaches_clean_endpoint; flipping is a judgment call on the
    # affordability bar (~12.4s flow wall vs the ~4.5s no-rr config), not code.
    Given the RR arc took bigHalf's clean endpoint to ~12.4s flow wall
    When the two commented ripup_reroute lines are re-enabled
    Then the checked-in flow reaches 0 overlaps / 0 opens
