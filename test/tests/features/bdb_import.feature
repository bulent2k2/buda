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

  # `ab_bus` is declared `wire [3:0]` and drives `input [3:0] data_in` on two
  # instances, so it is FOUR nets and each of those ports is four pins.  These
  # scenarios used to say one net named `ab_bus` with four pins on a pin named
  # `data_in`: that was the reader collapsing a bus, written down as a spec
  # (opens_interchange item 2).  What they were built to check — that a net is
  # scoped by hierarchy and propagated through port bindings — is unchanged and
  # is still exactly what they check.

  Scenario: a top-level bus is elaborated as one net per bit
    Then net "ab_bus[0]" exists
    And  net "ab_bus[3]" exists

  Scenario: internal wire w1 is scoped under ai
    Then net "ai/w1[0]" exists

  Scenario: one net per connected bit is created for this design
    Then the database contains 5 nets

  # ── pin recording ─────────────────────────────────────────────────────────

  Scenario: bit 0 carries 4 pins — boundary connections propagated through port bindings
    Then net "ab_bus[0]" has 4 pins

  Scenario: a bit connected only through the two data_in ports carries 2 pins
    Then net "ab_bus[1]" has 2 pins

  Scenario: ab_bus connects ai and bi at their data_in ports, bit for bit
    Then net "ab_bus[0]" connects component "ai" at pin "data_in[0]"
    And  net "ab_bus[0]" connects component "bi" at pin "data_in[0]"
    And  net "ab_bus[3]" connects component "ai" at pin "data_in[3]"

  # `a2`/`a1` declare no ports, so those formals are one bit wide and take the
  # LOW bit of what they are handed — which is why both land on bit 0.
  Scenario: ab_bus is propagated into sub-module a via the data_in port binding
    Then net "ab_bus[0]" connects component "ai/a2i" at pin "x"

  Scenario: ab_bus is propagated into sub-module b via the data_in port binding
    Then net "ab_bus[0]" connects component "bi/a1i1" at pin "q"

  Scenario: internal wire ai/w1 connects the two a1 instances inside ai
    Then net "ai/w1[0]" has 2 pins
    And  net "ai/w1[0]" connects component "ai/a1i1" at pin "q"
    And  net "ai/w1[0]" connects component "ai/a1i2" at pin "d"
