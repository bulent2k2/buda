Feature: Intelligent Net Bundling
  As a chip planner
  I want to bundle nets based on connectivity patterns
  So that I can optimize routing for related signals

  # Previous Scenario (Strict)
  Scenario: Strict Bundling (Shared Driver & Receiver)
    Given a netlist with nets:
      | Net Name | Driver      | Receivers      |
      | data_0   | u_cpu.d[0]  | u_mem.in[0]    |
      | data_1   | u_cpu.d[1]  | u_mem.in[1]    |
    When I run the bundler with "strict_connectivity"
    Then I should get 1 bundle containing "data_0, data_1"

  # NEW Scenario (Your Request)
  Scenario: Convergent Bundling (Ignore Driver, Shared Receivers)
    Given a netlist with nets:
      | Net Name | Driver       | Receivers             |
      | req_A    | u_blockA.req | u_arbiter.req[0]      |
      | req_B    | u_blockB.req | u_arbiter.req[1]      |
    # Note: Drivers are different (BlockA vs BlockB), but they share the same sink instance (Arbiter)
    When I run the bundler with "shared_receivers_only"
    Then I should get 1 bundle containing "req_A, req_B"
    And the bundle reason should be "Common Sink: u_arbiter"

  Scenario: Preventing False Positives
    Given a netlist with nets:
      | Net Name | Driver       | Receivers             |
      | net_X    | u_src.x      | u_dest_1.in           |
      | net_Y    | u_src.y      | u_dest_2.in           |
    # Drivers are same, but Sinks are totally different
    When I run the bundler
    Then I should get 0 bundles