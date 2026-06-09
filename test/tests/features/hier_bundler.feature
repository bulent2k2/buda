Feature: HierarchicalBundler — pipeline test vehicle
  Verify depth-level grouping, cell context, and multiple-occurrence detection
  on the three-block pipeline: src_i → proc_i (3×pipe_cell) → snk_i.

  4 buses of 8 bits each:
    s2p[8]    src_i/buf_i.out  → proc_i/pa_i.in    (cross-block)
    pa_pb[8]  proc_i/pa_i.out → proc_i/pb_i.in    (intra-proc)
    pb_pc[8]  proc_i/pb_i.out → proc_i/pc_i.in    (intra-proc)
    p2s[8]    proc_i/pc_i.out → snk_i/rcv_i.in    (cross-block)

  Background:
    Given a BDB with the pipeline hierarchy and nets

  # ── Depth-0 bundles ───────────────────────────────────────────────────────

  Scenario: depth-0 bundling finds only cross-block connections
    When run_hier_bundler is called with max_depth 0
    Then there are 2 hbundles
    And there are 2 hbundles at level 0
    And there are 0 hbundles at level 1

  Scenario: depth-0 bundles connect the right top-level blocks
    When run_hier_bundler is called with max_depth 0
    Then an hbundle with 8 nets exists at level 0 with reason containing "DRV:src_i"
    And  an hbundle with 8 nets exists at level 0 with reason containing "DRV:proc_i"

  # ── Depth-1 bundles ───────────────────────────────────────────────────────

  Scenario: depth-1 bundling creates four bundles total
    When run_hier_bundler is called with max_depth 1
    Then there are 6 hbundles
    And there are 2 hbundles at level 0
    And there are 4 hbundles at level 1

  Scenario: intra-proc bundles have cell_context set to proc_cell
    When run_hier_bundler is called with max_depth 1
    Then there are 2 hbundles with cell_context "proc_cell"
    And  there are 0 hbundles with cell_context "src_cell"

  Scenario: cross-block depth-1 bundles have no cell_context
    When run_hier_bundler is called with max_depth 1
    Then there are 2 hbundles at level 1 with empty cell_context

  Scenario: intra-proc bundles list proc_i as their instance
    When run_hier_bundler is called with max_depth 1
    Then every hbundle with cell_context "proc_cell" has instance "proc_i"

  # ── Depth linkage ─────────────────────────────────────────────────────────

  Scenario: depth-1 cross-block bundles are children of depth-0 bundles
    When run_hier_bundler is called with max_depth 1
    Then the depth-1 bundle for "s2p_0" has a depth-0 parent
    And  the depth-1 bundle for "p2s_0" has a depth-0 parent

  Scenario: intra-proc bundles have no cross-depth parent
    When run_hier_bundler is called with max_depth 1
    Then the depth-1 bundle for "pa_pb_0" has no parent bundle

  # ── Busterm ids ───────────────────────────────────────────────────────────

  Scenario: intra-proc bundles carry correct entry and exit busterm ids
    When run_hier_bundler is called with max_depth 1
    Then the bundle for "pa_pb_0" has entry_busterm "bt:proc_i/pa_i"
    And  the bundle for "pa_pb_0" has exit_busterm "bt:proc_i/pb_i"
