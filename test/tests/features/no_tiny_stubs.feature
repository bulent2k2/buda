@landed
Feature: Elimination of Trivial or Tiny Stubs
  As a chip planner
  I want topology slide ranges to exclude positions that result in trivial stubs
  So that NUTS track assignment produces robust physical layout

  Background:
    Given a floorplan
    And block "u_s" at 50, 400 to 250, 600
    And block "u_d" at 900, 200 to 1000, 1000
    And global corner margin is dx=15, dy=20

  Scenario: L-Topology Spine Slide Range Excludes Block Core
    Given a bundle from "u_s.tx" to "u_d.rx"
    When I generate topologies
    Then all L-shape candidates should have slide ranges for their spine segments
    And these slide ranges should NOT overlap with the connected block's core (non-margin) area
    And all stubs should have a minimum length of 20 units

  Scenario: L-Topology Connects to Physical Face
    Given a bundle from "u_s.tx" to "u_d.rx"
    When I generate topologies
    Then all L-shape candidates should connect exactly to the block's physical face
    # For u_s.tx, the V-stub should start or end at y=600.

  Scenario: U-Topology Trunk Slide Range is Well-Separated
    Given a bundle from "u_s.tx" to "u_d.rx"
    When I generate topologies
    Then all U-shape candidates should have trunk segments with non-trivial slide ranges
    And all stubs should have a minimum length of 20 units
