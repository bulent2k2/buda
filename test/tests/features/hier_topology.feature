Feature: generate_hier_topologies — pipeline test vehicle
  Verify topology candidate generation for the three bundle kinds:
  depth-0 cross-block, depth-1 cross-block, and depth-1 cell-level.

  Pipeline: src_i → proc_i (3×pipe_cell) → snk_i
  4 buses of 8 bits; run_hier_bundler depth 1 produces 6 HBundles.

  Background:
    Given a BDB with the pipeline hierarchy, nets, and busterms
    And run_hier_bundler has been called with max_depth 1

  # ── Candidate counts ──────────────────────────────────────────────────────

  Scenario: topology candidates are generated for every bundle
    When generate_hier_topologies is called
    Then every bundle has at least 1 candidate

  Scenario: all six bundles get candidates
    When generate_hier_topologies is called
    Then there are 6 bundles with candidates

  # ── Cell-local topology ───────────────────────────────────────────────────

  Scenario: cell-local bundle candidates lie within proc_cell bounds
    When generate_hier_topologies is called
    Then every candidate segment for "pa_pb_0" has x coords in range 0 420
    And  every candidate segment for "pa_pb_0" has y coords in range 0 200

  Scenario: cross-block candidates use absolute coordinates
    When generate_hier_topologies is called
    Then at least one candidate segment for "s2p_0" has x coord above 300

  # ── Template sharing ─────────────────────────────────────────────────────

  Scenario: pa_pb and pb_pc candidates are both non-empty (independent cell-local routing)
    When generate_hier_topologies is called
    Then the bundle for "pa_pb_0" has at least 1 candidate
    And  the bundle for "pb_pc_0" has at least 1 candidate
