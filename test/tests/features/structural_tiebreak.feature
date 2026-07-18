@landed
# Executable coverage: test/tests/test_topo_structural_tiebreak.py (binds this
# file and adds hand-written asserts for whole-pool ordering + determinism).
Feature: Structural WL tie-break in candidate ranking
  As a chip planner
  I want equal-wirelength topology candidates ranked by ascending segment
  count (fewer segments = fewer junctions = tighter realization risk),
  with the type string only as the final determinism anchor,
  So that a slide-coupled multi-junction staircase can no longer outrank a
  structurally-tight trunk at the same nominal WL just because ASCII
  '+' < '@' sorted its type name first (the b44 mis-pick — see
  docs/internal/wishlist-topo.md "Nominal-WL comparability", piece (b)).

  # Fixture = the flow/big_data_test/b44.buda block layout: the 3510 nominal
  # floor is shared by two 3-seg plain trunks AND the 6-seg TRUNK_H+MST
  # staircase; the planner's equal-score tie-break keeps the LOWEST index,
  # which this sort defines.  CAUTION: changing the key again renumbers the
  # 1-based select_topology indices pinned in checked-in flows/tests — it
  # needs a fresh pin audit (see the shipped audit in wishlist-topo.md).

  Scenario: equal-WL candidates tie-break structurally, not alphabetically
    Given several candidates with equal nominal wirelength
    When candidates are sorted for planning
    Then fewer-segment shapes rank ahead of slide-coupled staircases
