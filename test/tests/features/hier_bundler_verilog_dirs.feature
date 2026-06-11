Feature: HierarchicalBundler derives pin directions from Verilog declarations
  Verilog-only bootstrap may import net pins with UNKNOWN direction.
  HBundle generation is responsible for deriving endpoint roles from module
  port declarations, writing those directions back into BDB, and then grouping
  the directed connectivity.

  Background:
    Given a BDB imported from Verilog with declared module port directions

  Scenario: imported Verilog pins start without BDB directions
    Then net "sig" has pin "u_src.y" with direction "UNKNOWN"
    And net "sig" has pin "u_dst.a" with direction "UNKNOWN"

  Scenario: HBundle generation writes inferred pin directions back into BDB
    When run_hier_bundler is called with max_depth 0
    Then net "sig" has pin "u_src.y" with direction "OUTPUT"
    And net "sig" has pin "u_dst.a" with direction "INPUT"

  Scenario: HBundle generation groups Verilog-imported connectivity after direction inference
    When run_hier_bundler is called with max_depth 0
    Then there is 1 hbundle
    And the hbundle for net "sig" has reason "DRV:u_src|REC:u_dst,"
