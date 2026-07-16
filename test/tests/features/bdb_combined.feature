@landed
Feature: BDB combined DEF+Verilog import
  Verify that import_def_lef followed by import_verilog merges physical
  placement and logical hierarchy onto the same component rows.
  Leaf cells are 100x100 μm; parent cells sized with 50 μm inner margins
  and 50 μm channels between children.

  Layout (μm):
    x:  0    100         600   750          1100  1200               1850   2000
         |    |----ai----|     |----bi----|        |------ci--------|      |
         |    | a1i1 a1i2 a2i | b1i a1i1 |        | c1i c2i a1i1 a1i2 |  |
  children at y=150, parents at y=100-300, die 2000x500.

  Background:
    Given a combined BDB for hier_test1 with DEF placement and Verilog hierarchy

  # ── component count ────────────────────────────────────────────────────────

  Scenario: all 12 components are present after combined import
    Then the database contains 12 components

  # ── die and module bounding boxes ─────────────────────────────────────────

  Scenario: die dimensions are read from the DEF header
    Then the die is 2000.0 by 500.0 microns

  Scenario: top-level instance bounding boxes are DEF origin plus LEF size
    Then component "ai" spans from (100, 100) to (600, 300)
    And component "bi" spans from (750, 100) to (1100, 300)
    And component "ci" spans from (1200, 100) to (1850, 300)

  # ── leaf-cell absolute placements ─────────────────────────────────────────

  Scenario: second-level instances have their absolute DEF placements preserved
    Then component "ai/a1i1" spans from (150, 150) to (250, 250)
    And component "ai/a1i2" spans from (300, 150) to (400, 250)
    And component "ai/a2i" spans from (450, 150) to (550, 250)
    And component "bi/b1i" spans from (800, 150) to (900, 250)
    And component "bi/a1i1" spans from (950, 150) to (1050, 250)
    And component "ci/c1i" spans from (1250, 150) to (1350, 250)
    And component "ci/c2i" spans from (1400, 150) to (1500, 250)
    And component "ci/a1i1" spans from (1550, 150) to (1650, 250)
    And component "ci/a1i2" spans from (1700, 150) to (1800, 250)

  # ── hierarchy layered on top of placement ─────────────────────────────────

  Scenario: Verilog hierarchy is intact after DEF placement is merged
    Then component "ai" has cell "a" and depth 0
    And component "ai/a1i1" has cell "a1" and depth 1
    And "ai/a1i1" is a child of "ai"
    And "ai/a1i2" is a child of "ai"
    And "bi/a1i1" is a child of "bi"
    And "ci/a1i1" is a child of "ci"
    And "ci/a1i2" is a child of "ci"
    And component "ai" has no parent
    And component "bi" has no parent
    And component "ci" has no parent

  # ── spatial query ──────────────────────────────────────────────────────────

  Scenario: comps_in_rect returns only components that overlap the query window
    Then comps_in_rect (120, 120, 270, 270) returns exactly "ai, ai/a1i1"

  # ── nets and pins ──────────────────────────────────────────────────────────

  Scenario: Verilog-derived nets are present alongside placement data
    Then net "ab_bus" exists
    And net "ab_bus" has 4 pins
    And net "ai/w1" exists
    And net "ai/w1" has 2 pins
    And net "ab_bus" connects component "ai" at pin "data_in"
    And net "ab_bus" connects component "bi" at pin "data_in"
