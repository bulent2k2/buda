@landed
Feature: BDB Verilog import
  Verify that import_verilog correctly elaborates a shared-cell hierarchy:
  top module hier_test1 has ai/bi/ci (cells a/b/c); a has a1i1,a1i2,a2i;
  b has b1i,a1i1; c has c1i,c2i,a1i1,a1i2 — cell a1 is shared across all three.

  Background:
    Given a BDB populated from the hier_test1 Verilog

  # ── component ingestion ────────────────────────────────────────────────────

  Scenario: total component count
    Then the database contains 12 components

  Scenario: top-level instances are at depth 0 with correct cell types
    Then component "ai" has cell "a" and depth 0
    And  component "bi" has cell "b" and depth 0
    And  component "ci" has cell "c" and depth 0

  Scenario: second-level instances are at depth 1 with correct cell types
    Then component "ai/a1i1" has cell "a1" and depth 1
    And component "ai/a1i2" has cell "a1" and depth 1
    And component "ai/a2i" has cell "a2" and depth 1
    And component "bi/b1i" has cell "b1" and depth 1
    And component "bi/a1i1" has cell "a1" and depth 1
    And component "ci/c1i" has cell "c1" and depth 1
    And component "ci/c2i" has cell "c2" and depth 1
    And component "ci/a1i1" has cell "a1" and depth 1
    And component "ci/a1i2" has cell "a1" and depth 1

  Scenario: all depth-1 instances are children of their depth-0 parent
    Then "ai/a1i1" is a child of "ai"
    And "ai/a1i2" is a child of "ai"
    And "ai/a2i" is a child of "ai"
    And "bi/b1i" is a child of "bi"
    And "bi/a1i1" is a child of "bi"
    And "ci/c1i" is a child of "ci"
    And "ci/c2i" is a child of "ci"
    And "ci/a1i1" is a child of "ci"
    And "ci/a1i2" is a child of "ci"

  Scenario: top-level instances have no parent
    Then component "ai" has no parent
    And  component "bi" has no parent
    And  component "ci" has no parent

  # ── shared-cell reuse ──────────────────────────────────────────────────────

  Scenario: cell a1 is instantiated 5 times across three sub-modules
    Then 5 components have cell "a1"

  Scenario: the five a1 instances are at the expected hierarchy paths
    Then a component named "ai/a1i1" exists
    And  a component named "ai/a1i2" exists
    And  a component named "bi/a1i1" exists
    And  a component named "ci/a1i1" exists
    And  a component named "ci/a1i2" exists

  # ── leaf classification ────────────────────────────────────────────────────

  Scenario: modules with sub-instances are classified as non-leaf
    Then component "ai" is not a leaf
    And  component "bi" is not a leaf
    And  component "ci" is not a leaf

  Scenario: modules without sub-instances are also non-leaf when defined in the Verilog
    Then component "ai/a1i1" is not a leaf
    And component "ai/a2i" is not a leaf
    And component "bi/b1i" is not a leaf

  # ── net elaboration ────────────────────────────────────────────────────────

  Scenario: top-level wire ab_bus is elaborated as a net
    Then net "ab_bus" exists

  Scenario: internal wire w1 is scoped under ai
    Then net "ai/w1" exists

  Scenario: only 2 nets are created for this design
    Then the database contains 2 nets

  # ── pin recording ─────────────────────────────────────────────────────────

  Scenario: ab_bus has exactly 4 pins — boundary connections propagated through port bindings
    Then net "ab_bus" has 4 pins

  Scenario: ab_bus connects ai and bi at their data_in ports
    Then net "ab_bus" connects component "ai" at pin "data_in"
    And  net "ab_bus" connects component "bi" at pin "data_in"

  Scenario: ab_bus is propagated into sub-module a via the data_in port binding
    Then net "ab_bus" connects component "ai/a2i" at pin "x"

  Scenario: ab_bus is propagated into sub-module b via the data_in port binding
    Then net "ab_bus" connects component "bi/a1i1" at pin "q"

  Scenario: internal wire ai/w1 connects the two a1 instances inside ai
    Then net "ai/w1" has 2 pins
    And  net "ai/w1" connects component "ai/a1i1" at pin "q"
    And  net "ai/w1" connects component "ai/a1i2" at pin "d"
