@landed
Feature: Hierarchical test vehicle — pipeline subsystem
  Primary test vehicle for hierarchical bundle (HBundle) development.
  Models a three-block data-pipeline: src_i → proc_i (3×pipe stage) → snk_i.

  depth 0: src_i  (src_cell 200×200)  proc_i  (proc_cell 420×200)  snk_i  (snk_cell 200×200)
  depth 1: gen_i, buf_i (inside src_i)
           pa_i, pb_i, pc_i (all pipe_cell, inside proc_i)  ← multiple-occurrence pattern
           rcv_i, store_i (inside snk_i)

  Background:
    Given a BDB populated with the pipeline hierarchy

  # ── Component counts ──────────────────────────────────────────────────────

  Scenario: Database contains exactly 10 components
    Then the database contains 10 components

  Scenario: Three top-level blocks at depth 0
    Then there are 3 components at depth 0
    And component "src_i" has cell "src_cell" and depth 0
    And component "proc_i" has cell "proc_cell" and depth 0
    And component "snk_i" has cell "snk_cell" and depth 0

  Scenario: Seven leaf components at depth 1
    Then there are 7 components at depth 1
    And component "src_i/gen_i" has cell "gen_cell" and depth 1
    And component "src_i/buf_i" has cell "buf_cell" and depth 1
    And component "proc_i/pa_i" has cell "pipe_cell" and depth 1
    And component "proc_i/pb_i" has cell "pipe_cell" and depth 1
    And component "proc_i/pc_i" has cell "pipe_cell" and depth 1
    And component "snk_i/rcv_i" has cell "rcv_cell" and depth 1
    And component "snk_i/store_i" has cell "store_cell" and depth 1

  # ── Multiple-occurrence pattern ───────────────────────────────────────────

  Scenario: pipe_cell is instantiated exactly three times inside proc_i
    Then there are 3 components with cell "pipe_cell"
    And the "pipe_cell" instances are named "proc_i/pa_i, proc_i/pb_i, proc_i/pc_i"

  # ── Absolute physical coordinates ─────────────────────────────────────────

  Scenario: Leaf cells have correct absolute bounding boxes
    # src_i at (50,50); buf_i offset (100,60) within src_cell
    Then component "src_i/buf_i" has approx bbox 150 110 230 190
    # proc_i at (350,50); pa_i offset (20,60), pb_i offset (155,60), pc_i offset (290,60)
    And component "proc_i/pa_i" has approx bbox 370 110 480 190
    And component "proc_i/pb_i" has approx bbox 505 110 615 190
    And component "proc_i/pc_i" has approx bbox 640 110 750 190
    # snk_i at (870,50); rcv_i offset (20,60)
    And component "snk_i/rcv_i" has approx bbox 890 110 970 190

  # ── BustermGen: depth 0 ───────────────────────────────────────────────────

  Scenario: derive_busterms at depth 0 creates one busterm per top-level block
    When derive_busterms is called with max_depth 0
    Then the database contains 3 busterms
    And busterm "bt:src_i" exists with depth 0 and no parent
    And busterm "bt:proc_i" exists with depth 0 and no parent
    And busterm "bt:snk_i" exists with depth 0 and no parent

  # ── BustermGen: depth 1 ───────────────────────────────────────────────────

  Scenario: derive_busterms at depth 1 creates busterms for all 10 components
    When derive_busterms is called with max_depth 1
    Then the database contains 10 busterms
    And busterm "bt:src_i/gen_i" has parent "bt:src_i"
    And busterm "bt:src_i/buf_i" has parent "bt:src_i"
    And busterm "bt:proc_i/pa_i" has parent "bt:proc_i"
    And busterm "bt:proc_i/pb_i" has parent "bt:proc_i"
    And busterm "bt:proc_i/pc_i" has parent "bt:proc_i"
    And busterm "bt:snk_i/rcv_i" has parent "bt:snk_i"
    And busterm "bt:snk_i/store_i" has parent "bt:snk_i"

  # ── BustermGen: idempotency ───────────────────────────────────────────────

  Scenario: derive_busterms is idempotent — calling twice gives the same count
    When derive_busterms is called with max_depth 1
    And  derive_busterms is called with max_depth 1
    Then the database contains 10 busterms
