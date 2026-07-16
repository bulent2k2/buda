@future
# STATUS: spec ahead of implementation (all scenarios xfail — see
# test_hierarchy_depth_planning.py). This file imagines a FLAT hierarchy syntax
# (`add_block ... depth/parent`, `generate_topologies depth N`) that did NOT ship.
# The hierarchy-depth control that landed runs through the BDB hier flow instead:
# `derive_busterms [max_depth N]` rolls busterms up to a chosen level, then
# `run_hier_bundler [depth N]`, `generate_hier_topologies`, and `run_planner hier`
# consume them (see hier_bundler / hier_topology / hier_planner .feature, all
# LANDED, and hierarchy_depth is exercised end-to-end by hier_testcase.feature).
# Realigning these scenarios to the BDB hier flow is a documented follow-up.
Feature: Hierarchy-Depth Busterm Generation
  As a chip planner
  I want to control which hierarchy level of blocks are used as busterm endpoints
  So that I can plan routes at the appropriate level of abstraction, from coarse
  partition outlines down to individual functional blocks, mirroring the
  depth=0..N multi-pass Nehalem planning flow.

  # Background: Hierarchy-depth planning (Intel Galaxy, Ekici et al.)
  #
  #   In the Nehalem case study (Slides 17-23) a unit is planned at successive
  #   depths without needing physical terminals. The same netlist and hierarchy
  #   tree is used for every pass; only the depth parameter changes which blocks
  #   are visible as busterm endpoints:
  #
  #   Depth=0  →  only top-level partition outlines (e.g., IEXEC outline)
  #   Depth=1  →  direct children of the top unit (e.g., ICLUSTR, ICLUSTB)
  #   Depth=2  →  grandchildren (e.g., FUB1 … FUB6 inside ICLUST)
  #   Depth=N  →  all blocks at level N in the hierarchy tree
  #
  #   Blocks deeper than the target depth are "rolled up" into their ancestor
  #   at the target depth. Blocks above (shallower than) the target depth are
  #   invisible as busterms — their nets are not planned at that pass.
  #
  #   Multi-pass flow (Nehalem): plan ICLUST buses at depth=2 first, then
  #   plan IEXEC buses at depth=1 treating the already-placed ICLUST segments
  #   as keepout obstacles. This is the key mechanism for bottom-up hierarchical
  #   planning where lower-level results feed back as constraints to the
  #   higher level.
  #
  # API design (not yet implemented in BUDA C++):
  #
  #   add_block <name> [rect ...] [depth <N>] [parent <parent_name>]
  #
  #   generate_topologies [depth <N>]
  #     → only blocks at hierarchy level N participate as busterm endpoints
  #     → blocks deeper than N are rolled up to their depth-N ancestor
  #
  # Hierarchy tree for all scenarios (MemPool/Nehalem-inspired):
  #
  #   u_exec  (depth=0)  (0,0)–(4000,4000)
  #   ├── u_clust_a  (depth=1, parent=u_exec)  (0,0)–(2000,4000)
  #   │   ├── u_fub_a1  (depth=2, parent=u_clust_a)  (0,0)–(1000,4000)
  #   │   └── u_fub_a2  (depth=2, parent=u_clust_a)  (1000,0)–(2000,4000)
  #   └── u_clust_b  (depth=1, parent=u_exec)  (2000,0)–(4000,4000)
  #       ├── u_fub_b1  (depth=2, parent=u_clust_b)  (2000,0)–(3000,4000)
  #       └── u_fub_b2  (depth=2, parent=u_clust_b)  (3000,0)–(4000,4000)
  #
  # Nets (modeling a tcdm_req bus from MemPool FUBs to cluster interconnect):
  #   tcdm_req[8]  drv=u_fub_a1.tx  rcv=u_fub_b1.rx
  #   xbar_gnt[4]  drv=u_fub_a2.tx  rcv=u_fub_b2.rx
  #   clust_sync   drv=u_clust_a.tx rcv=u_clust_b.rx
  #
  # Diagram notation:
  #   +-------+   block boundary
  #   |  Nm   |   block name
  #   +-------+
  #   x        busterm on block face
  #   ===      horizontal trunk
  #   ||       vertical trunk
  #   ·        rolled-up block (invisible at this depth)

  # ---------------------------------------------------------------------------
  # Depth=0: only the top-level outline is a busterm.
  # All nets that cross the u_exec boundary are invisible at depth=0 because
  # both endpoints are inside u_exec. No topologies are generated.
  # ---------------------------------------------------------------------------

  Scenario: Depth=0 — top-level block is the only busterm; no intra-unit topologies
    # At depth=0, the only visible block is u_exec.
    # tcdm_req and xbar_gnt both connect sub-blocks INSIDE u_exec — they are
    # internal to the depth=0 view and produce no external busterm candidates.
    # clust_sync also connects two sub-blocks inside u_exec — same result.
    # Result: zero topology candidates for all nets at depth=0.
    #
    #   depth=0 view:
    #
    #   +──────────────────────────────────────+
    #   │              u_exec                  │
    #   │  (all sub-blocks rolled up inside)   │
    #   +──────────────────────────────────────+
    #
    Given a hierarchical floorplan with blocks:
      | Name       | x1   | y1 | x2   | y2   | depth | parent     |
      | u_exec     | 0    | 0  | 4000 | 4000 | 0     |            |
      | u_clust_a  | 0    | 0  | 2000 | 4000 | 1     | u_exec     |
      | u_clust_b  | 2000 | 0  | 4000 | 4000 | 1     | u_exec     |
      | u_fub_a1   | 0    | 0  | 1000 | 4000 | 2     | u_clust_a  |
      | u_fub_a2   | 1000 | 0  | 2000 | 4000 | 2     | u_clust_a  |
      | u_fub_b1   | 2000 | 0  | 3000 | 4000 | 2     | u_clust_b  |
      | u_fub_b2   | 3000 | 0  | 4000 | 4000 | 2     | u_clust_b  |
    And a net "tcdm_req[8]" from "u_fub_a1" to "u_fub_b1"
    And a net "clust_sync" from "u_clust_a" to "u_clust_b"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate topologies at depth 0
    Then 0 topology candidates are generated for "tcdm_req[8]"
    And 0 topology candidates are generated for "clust_sync"

  # ---------------------------------------------------------------------------
  # Depth=1: u_clust_a and u_clust_b are the busterms.
  # u_fub_* blocks are rolled up into their depth-1 ancestors.
  # tcdm_req: drv inside u_clust_a, rcv inside u_clust_b → candidates generated.
  # clust_sync: drv=u_clust_a, rcv=u_clust_b → same pair, same candidates.
  # ---------------------------------------------------------------------------

  Scenario: Depth=1 — cluster outlines are busterms; FUB-level blocks rolled up
    # At depth=1, u_clust_a and u_clust_b are visible.
    # u_fub_a1 (depth=2) is rolled up to its depth-1 ancestor u_clust_a.
    # u_fub_b1 (depth=2) is rolled up to u_clust_b.
    # tcdm_req[8] now looks like: drv=u_clust_a, rcv=u_clust_b.
    # An H trunk at y=2000 (mid of u_clust_a/b height) crosses the boundary x=2000.
    #
    #   depth=1 view:
    #
    #   y=4000  +──────────+──────────+
    #           │u_clust_a │u_clust_b │
    #           │ (fubs    │ (fubs    │
    #           │ rolled   │ rolled   │
    #   y=2000  │ up)      x══════════x  ← TRUNK_H@y2000 (H trunk at midpoint)
    #           │          │          │
    #   y=  0   +──────────+──────────+
    #           x=0        x=2000     x=4000
    #
    Given a hierarchical floorplan with blocks:
      | Name       | x1   | y1 | x2   | y2   | depth | parent     |
      | u_exec     | 0    | 0  | 4000 | 4000 | 0     |            |
      | u_clust_a  | 0    | 0  | 2000 | 4000 | 1     | u_exec     |
      | u_clust_b  | 2000 | 0  | 4000 | 4000 | 1     | u_exec     |
      | u_fub_a1   | 0    | 0  | 1000 | 4000 | 2     | u_clust_a  |
      | u_fub_a2   | 1000 | 0  | 2000 | 4000 | 2     | u_clust_a  |
      | u_fub_b1   | 2000 | 0  | 3000 | 4000 | 2     | u_clust_b  |
      | u_fub_b2   | 3000 | 0  | 4000 | 4000 | 2     | u_clust_b  |
    And a net "tcdm_req[8]" from "u_fub_a1" to "u_fub_b1"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate topologies at depth 1
    Then topology candidates are generated for "tcdm_req[8]"
    And the busterm endpoints for "tcdm_req[8]" resolve to "u_clust_a" and "u_clust_b"
    And a candidate of type "TRUNK_H@y2000" exists for "tcdm_req[8]"

  # ---------------------------------------------------------------------------
  # Depth=2: FUB-level blocks are busterms; original endpoints, no rollup.
  # tcdm_req: u_fub_a1 → u_fub_b1 (precise endpoints, not rolled up).
  # TRUNK_H@y2000 still exists but the busterm faces are at FUB boundaries.
  # An additional TRUNK_V candidate is now generated because u_fub_a1.x2=1000
  # introduces a new Hanan x-line.
  # ---------------------------------------------------------------------------

  Scenario: Depth=2 — FUB blocks are busterms; precise endpoints, new Hanan lines
    # At depth=2 all blocks participate.
    # tcdm_req: drv=u_fub_a1 (0..1000, 0..4000), rcv=u_fub_b1 (2000..3000, 0..4000).
    # Hanan x lines from all blocks: {0,1000,2000,3000,4000}.
    # New H trunk candidates appear at y=Hanan lines from FUB tops/bottoms.
    # No rollup — busterm faces are exact FUB block faces.
    #
    #   depth=2 view:
    #
    #   y=4000  +──────+──────+──────+──────+
    #           │fub_a1│fub_a2│fub_b1│fub_b2│
    #   y=2000  │      x══════x      │      │  ← TRUNK_H@y2000
    #           │      │      │      │      │
    #   y=  0   +──────+──────+──────+──────+
    #           x=0  x=1000 x=2000 x=3000 x=4000
    #
    Given a hierarchical floorplan with blocks:
      | Name       | x1   | y1 | x2   | y2   | depth | parent     |
      | u_exec     | 0    | 0  | 4000 | 4000 | 0     |            |
      | u_clust_a  | 0    | 0  | 2000 | 4000 | 1     | u_exec     |
      | u_clust_b  | 2000 | 0  | 4000 | 4000 | 1     | u_exec     |
      | u_fub_a1   | 0    | 0  | 1000 | 4000 | 2     | u_clust_a  |
      | u_fub_a2   | 1000 | 0  | 2000 | 4000 | 2     | u_clust_a  |
      | u_fub_b1   | 2000 | 0  | 3000 | 4000 | 2     | u_clust_b  |
      | u_fub_b2   | 3000 | 0  | 4000 | 4000 | 2     | u_clust_b  |
    And a net "tcdm_req[8]" from "u_fub_a1" to "u_fub_b1"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate topologies at depth 2
    Then topology candidates are generated for "tcdm_req[8]"
    And the busterm endpoints for "tcdm_req[8]" resolve to "u_fub_a1" and "u_fub_b1"
    And a candidate of type "TRUNK_H@y2000" exists for "tcdm_req[8]"
    And the H stub from "u_fub_a1" in TRUNK_H@y2000 touches "u_fub_a1"'s right face
    And the H stub from "u_fub_b1" in TRUNK_H@y2000 touches "u_fub_b1"'s left face

  # ---------------------------------------------------------------------------
  # Depth rollup correctness: a net whose driver is at depth=3 is rolled up to
  # its depth=1 ancestor when planning at depth=1. The topology uses the rolled-up
  # block's bounding box, not the original net endpoint.
  # ---------------------------------------------------------------------------

  Scenario: Net driver at depth=3 is rolled up to depth=1 ancestor for topology generation
    # Add u_rf (register file) as a depth=3 block inside u_fub_a1.
    # Net "rf_rd[16]" connects u_rf → u_fub_b2.
    # At depth=1, u_rf rolls up to u_clust_a; u_fub_b2 rolls up to u_clust_b.
    # Topology is generated between u_clust_a and u_clust_b — same as tcdm_req at depth=1.
    #
    Given a hierarchical floorplan with blocks:
      | Name       | x1   | y1  | x2   | y2   | depth | parent     |
      | u_exec     | 0    | 0   | 4000 | 4000 | 0     |            |
      | u_clust_a  | 0    | 0   | 2000 | 4000 | 1     | u_exec     |
      | u_clust_b  | 2000 | 0   | 4000 | 4000 | 1     | u_exec     |
      | u_fub_a1   | 0    | 0   | 1000 | 4000 | 2     | u_clust_a  |
      | u_fub_a2   | 1000 | 0   | 2000 | 4000 | 2     | u_clust_a  |
      | u_fub_b1   | 2000 | 0   | 3000 | 4000 | 2     | u_clust_b  |
      | u_fub_b2   | 3000 | 0   | 4000 | 4000 | 2     | u_clust_b  |
      | u_rf       | 0    | 100 | 200  | 400  | 3     | u_fub_a1   |
    And a net "rf_rd[16]" from "u_rf" to "u_fub_b2"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate topologies at depth 1
    Then the busterm endpoints for "rf_rd[16]" resolve to "u_clust_a" and "u_clust_b"

  # ---------------------------------------------------------------------------
  # Nets that are internal to a rolled-up block are invisible at target depth.
  # ---------------------------------------------------------------------------

  Scenario: Intra-cluster net is invisible when both endpoints roll up to same ancestor
    # xbar_gnt: drv=u_fub_a2, rcv=u_fub_a1 — both inside u_clust_a.
    # At depth=1, both roll up to u_clust_a → self-loop → no topology.
    # At depth=2, they are distinct blocks → topology is generated.
    #
    Given a hierarchical floorplan with blocks:
      | Name       | x1   | y1 | x2   | y2   | depth | parent     |
      | u_exec     | 0    | 0  | 4000 | 4000 | 0     |            |
      | u_clust_a  | 0    | 0  | 2000 | 4000 | 1     | u_exec     |
      | u_clust_b  | 2000 | 0  | 4000 | 4000 | 1     | u_exec     |
      | u_fub_a1   | 0    | 0  | 1000 | 4000 | 2     | u_clust_a  |
      | u_fub_a2   | 1000 | 0  | 2000 | 4000 | 2     | u_clust_a  |
      | u_fub_b1   | 2000 | 0  | 3000 | 4000 | 2     | u_clust_b  |
      | u_fub_b2   | 3000 | 0  | 4000 | 4000 | 2     | u_clust_b  |
    And a net "xbar_gnt[4]" from "u_fub_a2" to "u_fub_a1"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate topologies at depth 1
    Then 0 topology candidates are generated for "xbar_gnt[4]"
    When I generate topologies at depth 2
    Then topology candidates are generated for "xbar_gnt[4]"

  # ---------------------------------------------------------------------------
  # Multi-pass: plan lower-level buses first, then promote NUTS result to
  # keepout obstacles for the upper-level pass (Nehalem ICLUST → IEXEC flow).
  #
  # Pass 1 (depth=2): plan tcdm_req between u_fub_a1 and u_fub_b1.
  #   tcdm_req NUTS result: H trunk at y=2000, spanning x=1000 to x=2000.
  # Pass 2 (depth=1): plan clust_sync between u_clust_a and u_clust_b.
  #   The y=2000 band (where tcdm_req was placed) is now a keepout.
  #   clust_sync must avoid y=2000 and use an alternate trunk position.
  # ---------------------------------------------------------------------------

  Scenario: Multi-pass — depth=2 NUTS result promoted as obstacle for depth=1 pass
    # After pass 1 (depth=2), tcdm_req occupies y=2000 ± half_bus_width.
    # Pass 2 (depth=1) for clust_sync sees that keepout and cannot place
    # its H trunk at y=2000; it must use an alternate trunk row (e.g. y=1000
    # or y=3000 from the Hanan grid).
    #
    #   Pass 1 result (depth=2):
    #
    #   y=4000  +──────+──────+──────+──────+
    #           │fub_a1│fub_a2│fub_b1│fub_b2│
    #   y=2000  │      x══════x [tcdm_req]  │  ← placed at y=2000
    #           │      │      │      │      │
    #   y=  0   +──────+──────+──────+──────+
    #
    #   Pass 2 view (depth=1); y=2000 is now blocked:
    #
    #   y=4000  +──────────x══x──────────+  ← TRUNK_H@y3000 forced by keepout
    #           │u_clust_a │KKKK│u_clust_b│
    #   y=2000  │ (keepout zone y=1900-2100)
    #           │          │      │      │
    #   y=  0   +──────────+──────────+
    #
    Given a hierarchical floorplan with blocks:
      | Name       | x1   | y1 | x2   | y2   | depth | parent     |
      | u_exec     | 0    | 0  | 4000 | 4000 | 0     |            |
      | u_clust_a  | 0    | 0  | 2000 | 4000 | 1     | u_exec     |
      | u_clust_b  | 2000 | 0  | 4000 | 4000 | 1     | u_exec     |
      | u_fub_a1   | 0    | 0  | 1000 | 4000 | 2     | u_clust_a  |
      | u_fub_a2   | 1000 | 0  | 2000 | 4000 | 2     | u_clust_a  |
      | u_fub_b1   | 2000 | 0  | 3000 | 4000 | 2     | u_clust_b  |
      | u_fub_b2   | 3000 | 0  | 4000 | 4000 | 2     | u_clust_b  |
    And a net "tcdm_req[8]" from "u_fub_a1" to "u_fub_b1"
    And a net "clust_sync" from "u_clust_a" to "u_clust_b"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I run pass 1: generate and place "tcdm_req[8]" at depth 2
    And I promote the depth=2 NUTS result as a keepout for layer M4
    And I run pass 2: generate topologies for "clust_sync" at depth 1
    And I run the global planner for "clust_sync"
    Then "clust_sync" is not placed at y=2000
    And "clust_sync" avoids the keepout zone from the depth=2 pass

  # ---------------------------------------------------------------------------
  # Bundler depth: at depth=1 the bundler groups nets by their rolled-up
  # endpoints, not the original endpoints.
  # tcdm_req and rf_rd both roll up to u_clust_a → u_clust_b: they form
  # one bundle because they share the same depth-1 endpoint pair.
  # ---------------------------------------------------------------------------

  Scenario: Bundler groups nets by their rolled-up depth-1 endpoints
    # tcdm_req: u_fub_a1→u_fub_b1 → rolls up to u_clust_a→u_clust_b.
    # xbar_gnt:  u_fub_a2→u_fub_b2 → rolls up to u_clust_a→u_clust_b.
    # At depth=1 both nets share drv=u_clust_a, rcv=u_clust_b → one bundle.
    # At depth=2 they have distinct endpoints → two separate bundles.
    #
    Given a hierarchical floorplan with blocks:
      | Name       | x1   | y1 | x2   | y2   | depth | parent     |
      | u_exec     | 0    | 0  | 4000 | 4000 | 0     |            |
      | u_clust_a  | 0    | 0  | 2000 | 4000 | 1     | u_exec     |
      | u_clust_b  | 2000 | 0  | 4000 | 4000 | 1     | u_exec     |
      | u_fub_a1   | 0    | 0  | 1000 | 4000 | 2     | u_clust_a  |
      | u_fub_a2   | 1000 | 0  | 2000 | 4000 | 2     | u_clust_a  |
      | u_fub_b1   | 2000 | 0  | 3000 | 4000 | 2     | u_clust_b  |
      | u_fub_b2   | 3000 | 0  | 4000 | 4000 | 2     | u_clust_b  |
    And a net "tcdm_req[8]" from "u_fub_a1" to "u_fub_b1"
    And a net "xbar_gnt[4]" from "u_fub_a2" to "u_fub_b2"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I run the bundler at depth 1
    Then "tcdm_req[8]" and "xbar_gnt[4]" are in the same bundle
    When I run the bundler at depth 2
    Then "tcdm_req[8]" and "xbar_gnt[4]" are in different bundles

  # ---------------------------------------------------------------------------
  # .buda script syntax: the depth parameter on generate_topologies and
  # add_block should compose naturally.
  # ---------------------------------------------------------------------------

  Scenario: .buda script syntax — add_block with depth and parent, generate_topologies with depth
    # This scenario validates the intended .buda command syntax.
    # It does not assert on topology geometry — only that the CLI accepts the
    # commands without error and that the correct depth is in effect.
    #
    #   add_block u_exec     0    0  4000 4000  depth 0
    #   add_block u_clust_a  0    0  2000 4000  depth 1  parent u_exec
    #   add_block u_clust_b  2000 0  4000 4000  depth 1  parent u_exec
    #   add_block u_fub_a1   0    0  1000 4000  depth 2  parent u_clust_a
    #   add_block u_fub_b1   2000 0  3000 4000  depth 2  parent u_clust_b
    #   add_net   tcdm_req   u_fub_a1.tx  u_fub_b1.rx
    #   def_layer 4 M4 H TOP 1.0
    #   def_layer 5 M5 V TOP 1.0
    #   generate_topologies depth 1
    #
    Given a .buda script:
      """
      add_block u_exec     0    0  4000 4000  depth 0
      add_block u_clust_a  0    0  2000 4000  depth 1  parent u_exec
      add_block u_clust_b  2000 0  4000 4000  depth 1  parent u_exec
      add_block u_fub_a1   0    0  1000 4000  depth 2  parent u_clust_a
      add_block u_fub_b1   2000 0  3000 4000  depth 2  parent u_clust_b
      add_net   tcdm_req   u_fub_a1.tx  u_fub_b1.rx
      def_layer 4 M4 H TOP 1.0
      def_layer 5 M5 V TOP 1.0
      generate_topologies depth 1
      """
    When I execute the .buda script
    Then the script completes without error
    And topology candidates are generated for "tcdm_req"
    And the busterm endpoints for "tcdm_req" resolve to "u_clust_a" and "u_clust_b"
