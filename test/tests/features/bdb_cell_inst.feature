Feature: BDB cell definition and add_inst
  add_cell defines a cell with a canonical size.
  add_inst places an instance of a defined cell using coordinates
  relative to the parent's origin; the engine resolves absolute
  positions automatically and marks parents non-leaf.

  # ── add_cell / all_cells ──────────────────────────────────────────────────

  Scenario: add_cell stores name and dimensions
    Given an empty in-memory BDB
    When I define cell "cpu" with size 500 by 400
    Then cell "cpu" has width 500 and height 400

  Scenario: add_cell is idempotent — second call overwrites size
    Given an empty in-memory BDB
    When I define cell "cpu" with size 500 by 400
    And I define cell "cpu" with size 600 by 450
    Then cell "cpu" has width 600 and height 450

  Scenario: all_cells returns all defined cells
    Given an empty in-memory BDB
    When I define cell "a" with size 100 by 100
    And I define cell "b" with size 200 by 150
    Then the cell table contains 2 cells

  # ── add_inst: root instance ───────────────────────────────────────────────

  Scenario: add_inst places a root instance at absolute coordinates
    Given an empty in-memory BDB
    When I define cell "cpu" with size 500 by 400
    And I place instance "u_cpu" of cell "cpu" under "-" at (0, 0)
    Then component "u_cpu" spans from (0, 0) to (500, 400)
    And component "u_cpu" has depth 0
    And component "u_cpu" has no parent

  # ── add_inst: child instance with relative coords ─────────────────────────

  Scenario: add_inst child coordinates are relative to parent origin
    Given an empty in-memory BDB
    When I define cell "cpu" with size 500 by 400
    And I define cell "core" with size 150 by 150
    And I place instance "u_cpu" of cell "cpu" under "-" at (0, 0)
    And I place instance "u_cpu/core0" of cell "core" under "u_cpu" at (50, 50)
    Then component "u_cpu/core0" spans from (50, 50) to (200, 200)

  Scenario: add_inst child offset added to parent origin when parent is not at zero
    Given an empty in-memory BDB
    When I define cell "cpu" with size 500 by 400
    And I define cell "core" with size 150 by 150
    And I place instance "u_cpu" of cell "cpu" under "-" at (100, 200)
    And I place instance "u_cpu/core0" of cell "core" under "u_cpu" at (50, 50)
    Then component "u_cpu/core0" spans from (150, 250) to (300, 400)

  Scenario: three-level hierarchy resolves absolute positions correctly
    Given an empty in-memory BDB
    When I define cell "top" with size 600 by 500
    And I define cell "mid" with size 200 by 200
    And I define cell "leaf" with size 60 by 60
    And I place instance "u_top" of cell "top" under "-" at (0, 0)
    And I place instance "u_top/u_mid" of cell "mid" under "u_top" at (100, 100)
    And I place instance "u_top/u_mid/u_leaf" of cell "leaf" under "u_top/u_mid" at (10, 10)
    Then component "u_top/u_mid" spans from (100, 100) to (300, 300)
    And component "u_top/u_mid/u_leaf" spans from (110, 110) to (170, 170)

  # ── parent gets marked non-leaf ───────────────────────────────────────────

  Scenario: parent is marked non-leaf after add_inst
    Given an empty in-memory BDB
    When I define cell "cpu" with size 500 by 400
    And I define cell "core" with size 150 by 150
    And I place instance "u_cpu" of cell "cpu" under "-" at (0, 0)
    And I place instance "u_cpu/core0" of cell "core" under "u_cpu" at (50, 50)
    Then component "u_cpu" is not a leaf

  Scenario: depth is computed from parent
    Given an empty in-memory BDB
    When I define cell "top" with size 600 by 500
    And I define cell "mid" with size 200 by 200
    And I define cell "leaf" with size 60 by 60
    And I place instance "u_top" of cell "top" under "-" at (0, 0)
    And I place instance "u_top/u_mid" of cell "mid" under "u_top" at (100, 100)
    And I place instance "u_top/u_mid/u_leaf" of cell "leaf" under "u_top/u_mid" at (10, 10)
    Then component "u_top" has depth 0
    And component "u_top/u_mid" has depth 1
    And component "u_top/u_mid/u_leaf" has depth 2

  # ── import_def_lef populates cell table ───────────────────────────────────

  Scenario: import_def_lef populates cell table from LEF MACRO sizes
    Given a combined BDB for hier_test1 with DEF placement and Verilog hierarchy
    Then cell "a1" has width 100 and height 100
    And cell "a" has width 500 and height 200
    And cell "b" has width 350 and height 200

  # ── resize_cell updates cell table ────────────────────────────────────────

  Scenario: resize_cell keeps cell table in sync
    Given an empty in-memory BDB
    When I define cell "a1" with size 100 by 100
    And I define cell "top" with size 400 by 400
    And I place instance "u_top" of cell "top" under "-" at (0, 0)
    And I place instance "u_top/i1" of cell "a1" under "u_top" at (10, 10)
    And I resize cell "a1" to 120 by 120
    Then cell "a1" has width 120 and height 120
    And component "u_top/i1" spans from (10, 10) to (130, 130)
