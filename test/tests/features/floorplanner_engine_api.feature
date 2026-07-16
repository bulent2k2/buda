@landed
Feature: Floorplanner engine API — core placement operations
  The GUI should delegate traditional floorplanning operations to a C++ engine
  exposed through Python bindings, so the behavior is deterministic and testable
  without a graphical front end.

  Background:
    Given a new floorplanner engine
    And the engine has die size 2000 by 1200 microns
    And the engine has placement grid 10 microns

  Scenario: snap a moved block to the placement grid
    Given engine block "u_cpu" spans from (100, 100) to (700, 600)
    When the engine moves block "u_cpu" to raw origin (123, 147)
    Then engine block "u_cpu" spans from (120, 150) to (720, 650)

  Scenario: reject a non-positive die size
    When the engine sets die size to 0 by 1200 microns
    Then the engine reports a floorplan error containing "die"

  Scenario: detect overlapping blocks
    Given engine block "u_cpu" spans from (100, 100) to (700, 600)
    And engine block "u_mem" spans from (650, 100) to (1150, 600)
    When the engine validates placement
    Then the engine reports an overlap between "u_cpu" and "u_mem"

  Scenario: detect blocks outside the die
    Given engine block "u_io" spans from (1800, 1000) to (2200, 1250)
    When the engine validates placement
    Then the engine reports "u_io" outside the die

  Scenario: align selected blocks by bottom edge
    Given engine block "u_cpu" spans from (100, 100) to (700, 600)
    And engine block "u_mem" spans from (900, 140) to (1400, 640)
    When the engine aligns blocks "u_cpu,u_mem" to the bottom edge
    Then engine block "u_mem" spans from (900, 100) to (1400, 600)

  Scenario: maintain local and absolute coordinates for child placement
    Given engine block "u_cluster" spans from (400, 300) to (1000, 800)
    And engine child block "u_cluster/u_core" has local origin (50, 60) and size 200 by 150
    When the engine moves child block "u_cluster/u_core" to local origin (80, 90)
    Then engine child block "u_cluster/u_core" has local origin (80, 90)
    And engine block "u_cluster/u_core" spans from (480, 390) to (680, 540)

  Scenario: write floorplan edits back to BDB
    Given engine block "u_cpu" spans from (100, 100) to (700, 600)
    When the engine writes placements to BDB
    Then BDB component "u_cpu" spans from (100, 100) to (700, 600)
