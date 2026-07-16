@landed
Feature: Floorplanner GUI — basic manual floorplanning workflow
  A designer starting from Verilog should be able to create a quick traditional
  hierarchical floorplan before running the HBundle feedback loop.

  Background:
    Given a new floorplanner GUI session
    And the user has imported Verilog hierarchy "top"

  Scenario: create a design canvas with die size and grid
    When the user sets the die to 2000 by 1200 microns
    And the user sets the placement grid to 10 microns
    Then the canvas shows a 2000 by 1200 micron die
    And block drag operations snap to the 10 micron grid

  Scenario: place top-level modules by dragging them on the canvas
    Given top-level modules "u_cpu", "u_mem", and "u_io" are unplaced
    When the user places "u_cpu" at (100, 100) with size 600 by 500
    And the user places "u_mem" at (900, 100) with size 500 by 500
    And the user places "u_io" at (100, 750) with size 400 by 250
    Then "u_cpu" spans from (100, 100) to (700, 600)
    And "u_mem" spans from (900, 100) to (1400, 600)
    And "u_io" spans from (100, 750) to (500, 1000)

  Scenario: resize a block with handles
    Given block "u_cpu" spans from (100, 100) to (700, 600)
    When the user drags the east resize handle of "u_cpu" to x=760
    Then "u_cpu" spans from (100, 100) to (760, 600)
    And the cell size for "u_cpu" is 660 by 500

  Scenario: align selected blocks to a common edge
    Given block "u_cpu" spans from (105, 100) to (705, 600)
    And block "u_mem" spans from (900, 140) to (1400, 640)
    When the user selects "u_cpu" and "u_mem"
    And the user aligns the selected blocks to the bottom edge
    Then "u_cpu" spans from (105, 100) to (705, 600)
    And "u_mem" spans from (900, 100) to (1400, 600)

  Scenario: distribute selected blocks horizontally
    Given block "u_a" spans from (100, 100) to (300, 300)
    And block "u_b" spans from (550, 100) to (750, 300)
    And block "u_c" spans from (1100, 100) to (1300, 300)
    When the user selects "u_a", "u_b", and "u_c"
    And the user distributes the selected blocks horizontally
    Then the horizontal gaps between selected blocks are equal

  Scenario: edit a child block inside a hierarchical parent
    Given block "u_cluster" spans from (400, 300) to (1000, 800)
    And child block "u_cluster/u_core" has local origin (50, 60) and size 200 by 150
    When the user descends into "u_cluster"
    And the user moves child block "u_core" to local origin (80, 90)
    Then child block "u_cluster/u_core" has local origin (80, 90)
    And "u_cluster/u_core" spans from (480, 390) to (680, 540)

  Scenario: rotate a block and transform its descendants
    Given block "u_cluster" spans from (400, 300) to (1000, 800)
    And child block "u_cluster/u_core" has local origin (50, 60) and size 200 by 150
    When the user rotates "u_cluster" by 90 degrees
    Then "u_cluster" spans from (400, 300) to (900, 900)
    And descendant "u_cluster/u_core" is transformed with the rotation

  Scenario: validate a floorplan before HBundle generation
    Given block "u_cpu" spans from (100, 100) to (700, 600)
    And block "u_mem" spans from (650, 100) to (1150, 600)
    And block "u_io" spans from (1800, 1000) to (2200, 1250)
    When the user runs floorplan validation
    Then validation reports an overlap between "u_cpu" and "u_mem"
    And validation reports "u_io" outside the die

  Scenario: export an HBundle-ready BUDA script
    Given the user has placed all modules through depth 1
    When the user exports the hierarchical bundle flow script for depth 1
    Then the script contains "derive_busterms 1"
    And the script contains "add_blocks_from_bdb 0"
    And the script contains "add_blocks_from_bdb 1 skip"
    And the script contains "run_hier_bundler depth 1"
    And the script contains "generate_hier_topologies"
    And the script contains "run_planner hier"
