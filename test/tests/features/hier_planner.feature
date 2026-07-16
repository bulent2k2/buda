@landed
Feature: Hierarchical Planner (Phase E)

  Background:
    Given a pipeline BDB with 1 proc instance and 4 buses of 8 bits

  Scenario: run_planner hier expands cell-level bundles to per-instance wrappers
    When I run run_planner hier 5
    Then self.bundles contains at least as many wrappers as original bundles

  Scenario: all expanded wrappers have a selected topology after run_planner hier
    When I run run_planner hier 5
    Then every wrapper in self.bundles has selected_topology_index >= 0

  Scenario: depth-0 bundles have higher priority than depth-1 bundles
    When priorities are assigned before run_planner hier
    Then every depth-0 wrapper has priority greater than every depth-1 wrapper

  Scenario: constraint-first ordering — fewer candidates means higher priority
    When priorities are assigned before run_planner hier
    Then within depth-0, a wrapper with fewer candidates has priority >= wrapper with more candidates

  Scenario: two instances of the same cell-level bundle get distinct bundle IDs
    Given a pipeline BDB with 2 proc instances and 4 buses of 8 bits
    When I expand hier bundles
    Then the expanded wrappers have unique original_bundle ids
