@landed
Feature: BDB mutation operations
  Verify move_comp, resize_cell, and add_comp work correctly
  against the combined hier_test1 fixture (DEF placement + Verilog hierarchy).

  Background:
    Given a combined BDB for hier_test1 with DEF placement and Verilog hierarchy

  # ── move_comp ──────────────────────────────────────────────────────────────

  Scenario: move_comp shifts origin and preserves cell size
    When I move component "ai/a1i1" to (200, 200)
    Then component "ai/a1i1" spans from (200, 200) to (300, 300)

  Scenario: move_comp does not affect other components
    When I move component "ai/a1i1" to (200, 200)
    Then component "ai/a1i2" spans from (300, 150) to (400, 250)

  # ── resize_cell ────────────────────────────────────────────────────────────

  Scenario: resize_cell updates every instance of that cell type
    When I resize cell "a1" to 120 by 120
    Then component "ai/a1i1" spans from (150, 150) to (270, 270)
    And component "ai/a1i2" spans from (300, 150) to (420, 270)
    And component "bi/a1i1" spans from (950, 150) to (1070, 270)
    And component "ci/a1i1" spans from (1550, 150) to (1670, 270)
    And component "ci/a1i2" spans from (1700, 150) to (1820, 270)

  Scenario: resize_cell does not affect instances of other cell types
    When I resize cell "a1" to 120 by 120
    Then component "ai/a2i" spans from (450, 150) to (550, 250)

  # ── add_comp ───────────────────────────────────────────────────────────────

  Scenario: add_comp inserts a new root instance
    When I add component "di" of cell "d" under parent "-" at (1900, 100)-(2000, 300)
    Then a component named "di" exists
    And component "di" has cell "d" and depth 0
    And component "di" has no parent

  Scenario: add_comp inserts a child instance under an existing parent
    When I add component "ai/a3i" of cell "a3" under parent "ai" at (550, 150)-(650, 250)
    Then a component named "ai/a3i" exists
    And "ai/a3i" is a child of "ai"
    And component "ai/a3i" has cell "a3" and depth 1

  Scenario: add_comp component count increases by one
    When I add component "di" of cell "d" under parent "-" at (1900, 100)-(2000, 300)
    Then the database contains 13 components
