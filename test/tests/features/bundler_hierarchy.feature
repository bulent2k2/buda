@orphaned
# STATUS: not bound to any step file (never run). This early 2-scenario sketch of
# hierarchical bundling was superseded by hier_bundler.feature (LANDED, bound to
# test_hier_bundler.py), which covers depth-0/1 grouping, cell_context, and
# multiple-occurrence bundles against the real BDB hier flow. Kept for historical
# reference; a candidate for the attic. Do not rely on it for coverage.
Feature: Hierarchical Net Bundling
  As a chip planner
  I want to bundle nets based on shared drivers and receivers at a specific hierarchy depth
  So that I can plan routes at the appropriate level of abstraction

  Scenario: Bundling nets at Top Level (Depth 0)
    Given a hierarchical netlist with the following instances:
      | Instance Name | Type      | Parent |
      | u_cpu         | CPU_Core  | top    |
      | u_mem         | L2_Cache  | top    |
    And the following nets connecting them:
      | Net Name | Driver Pin     | Receiver Pin   |
      | data_0   | u_cpu.dout[0]  | u_mem.din[0]   |
      | data_1   | u_cpu.dout[1]  | u_mem.din[1]   |
      | data_2   | u_cpu.dout[2]  | u_mem.din[2]   |
      | ctrl_0   | u_cpu.valid    | u_mem.valid    |
      | clk_sys  | top.clk        | u_cpu.clk, u_mem.clk |
    When I run the bundler with parameters:
      | strategy | connectivity |
      | depth    | 0            |
    Then I should receive 2 bundles:
      | Bundle ID | Nets Included          | Reason                     |
      | Bundle_1  | data_0, data_1, data_2 | Shared Driver: u_cpu, Rec: u_mem |
      | Bundle_2  | ctrl_0                 | Shared Driver: u_cpu, Rec: u_mem |
    # Note: 'clk_sys' might be excluded or bundled separately depending on rule (it has 2 receivers)

  Scenario: Diving Deeper (Depth 1)
    Given the instance "u_cpu" contains sub-instances "u_alu" and "u_regs"
    And nets "alu_out[0..3]" connect "u_alu" to "u_regs"
    When I run the bundler with depth=1
    Then I should identify a bundle connecting "u_alu" to "u_regs"