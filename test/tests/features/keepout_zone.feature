@landed
Feature: Designer Keepout Zones
  As a physical designer
  I want to define regions where specific layers are prohibited
  So that I can force routing around restricted areas or sensitive blocks

  Background:
    Given a layer stack with:
      | Name | ID | Direction  | Type |
      | M4   | 4  | HORIZONTAL | TOP  |
      | M5   | 5  | VERTICAL   | TOP  |
    And a floorplan with blocks:
      | Name  | x1  | y1  | x2  | y2  |
      | unit1 | 100 | 100 | 200 | 200 |
      | unit2 | 700 | 700 | 800 | 800 |

  Scenario: Global Planner Detours Around Keepout
    # L_HV (WL=1200) normally uses M4 then M5.
    # Placing a Keepout on M4 should force an alternative or incur penalty.
    Given a keepout zone at (300, 100) to (500, 300) for layer "M4"
    When I generate topologies for bundle "unit1->unit2"
    And I run the global planner
    Then the selected topology should not intersect the keepout region on "M4"
    And the selected topology should be "L_VH" or a detour shape

  Scenario: Detailed Router Blocks Tracks in Keepout
    Given a track pattern for layer 4 with "SIGNAL" width 2.0 and spacing 2.0
    And a keepout zone at (300, 100) to (500, 300) for layer 4
    When I query signal tracks for layer 4 in range y=[150, 250] at x=400
    Then the number of available signal tracks should be 0
    When I query signal tracks for layer 4 in range y=[150, 250] at x=100
    Then the number of available signal tracks should be greater than 0
