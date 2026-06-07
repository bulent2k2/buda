Feature: add_blocks_from_bdb
  Walk BDB hierarchy at a given depth and populate the Floorplan with
  add_block calls so the routing engine can generate topologies.

  Three modes control what happens when a branch is shallower than <depth>:
    deepest  — add the deepest available component on that branch (default)
    skip     — ignore the short branch entirely
    error    — abort with a diagnostic if any branch is too shallow

  Background:
    Given a BDB with a mixed-depth hierarchy for routing tests

  # ── mode=deepest (default) ─────────────────────────────────────────────────

  Scenario: deepest mode adds exact-depth components and shallow-branch fallbacks
    When I call add_blocks_from_bdb at depth 2 mode deepest
    Then the floorplan contains blocks "u_a/sub0/leaf0, u_a/sub0/leaf1, u_b/sub0/leaf0, u_c"
    # u_c is depth-1 only: no children → deepest fallback

  Scenario: deepest mode is the default when no mode is given
    When I call add_blocks_from_bdb at depth 2 mode deepest
    Then the floorplan block count is 4

  # ── mode=skip ──────────────────────────────────────────────────────────────

  Scenario: skip mode only adds components at exactly the requested depth
    When I call add_blocks_from_bdb at depth 2 mode skip
    Then the floorplan contains blocks "u_a/sub0/leaf0, u_a/sub0/leaf1, u_b/sub0/leaf0"
    # u_c branch (depth 1 only) produces nothing

  Scenario: skip mode block count excludes shallow branches
    When I call add_blocks_from_bdb at depth 2 mode skip
    Then the floorplan block count is 3

  # ── mode=error ─────────────────────────────────────────────────────────────

  Scenario: error mode adds no blocks when any branch is too shallow
    When I call add_blocks_from_bdb at depth 2 mode error
    Then the floorplan block count is 0

  Scenario: error mode succeeds when all branches reach the requested depth
    When I call add_blocks_from_bdb at depth 0 mode error
    Then the floorplan block count is 3

  # ── unplaced components ────────────────────────────────────────────────────

  Scenario: unplaced components are silently skipped in all modes
    When I call add_blocks_from_bdb at depth 2 mode deepest
    Then the floorplan does not contain block "u_b/sub0/unplaced"
