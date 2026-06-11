Feature: generate_hier_topologies — pipeline test vehicle
  Verify topology candidate generation for both bundle kinds:
  cross-block (leaf-precision endpoints, level = common-ancestor depth)
  and cell-level (cell-local coords).

  Pipeline: src_i → proc_i (3×pipe_cell) → snk_i
  4 buses of 8 bits; run_hier_bundler depth 1 produces 4 HBundles
  (one per bus — each net is bundled exactly once).

  Background:
    Given a BDB with the pipeline hierarchy, nets, and busterms
    And run_hier_bundler has been called with max_depth 1

  # ── Candidate counts ──────────────────────────────────────────────────────

  Scenario: topology candidates are generated for every bundle
    When generate_hier_topologies is called
    Then every bundle has at least 1 candidate

  Scenario: all four bundles get candidates
    When generate_hier_topologies is called
    Then there are 4 bundles with candidates

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
