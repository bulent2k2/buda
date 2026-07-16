@orphaned
# STATUS: binding deliberately disabled — test_topology.py's
# `scenarios('features/topology_generation.feature')` line is commented out, so
# these scenarios never run. Superseded by unified_topology.feature (LANDED, bound
# to test_unified_topology.py), the authoritative I/L/Z/U/UU two-busterm geometry
# spec (14 scenarios), plus connector_shape.feature and topology_flexibility.feature.
# Kept for historical reference; a candidate for the attic. Do not rely on it for coverage.
Feature: Topology Generation with Hanan Grid
  As a chip planner
  I want to generate a set of optimal topology candidates (I, L, Z, shapes)
  So that I can choose the best path avoiding congestion

  Background:
    Given a floorplan with the following blocks:
      | Name   | X1  | Y1  | X2  | Y2  |
      | u_src  | 0   | 10  | 20  | 30  |
      | u_mid  | 40  | 15  | 60  | 35  |
      | u_dest | 80  | 50  | 100 | 70  |

  Scenario: Generating L-Shapes (Simple Misalignment)
    Given a bundle connecting "u_src" to "u_dest"
    When I generate topologies
    Then I should receive "L-Shape" candidates
    # L-Shape corners should align with source/dest edges (Hanan points)
    And one candidate should have a corner at (20, 50) 
    And one candidate should have a corner at (80, 20)

  Scenario: Generating Z-Shapes via Trunk Seeding
    # The "Trunk" is the middle segment of the Z. 
    # It must align with a Hanan Grid line derived from the "u_mid" block or others.
    Given a bundle connecting "u_src" to "u_dest"
    And I allow trunks on the global Hanan Grid
    When I generate topologies
    Then I should receive "Z-Shape" candidates
    # We expect a Z-shape that utilizes the edge of u_mid (X=40 or X=60) as a vertical trunk
    And one candidate should have a vertical trunk at X=40
    And one candidate should have a vertical trunk at X=60
    
  Scenario: Filtering Sub-optimal Shapes
    # A "U" shape going way around the outside might be too long
    Given a bundle connecting "u_src" to "u_dest"
    And the maximum allowed detour is 10%
    When I generate topologies
    Then I should NOT receive a candidate with length > 150
