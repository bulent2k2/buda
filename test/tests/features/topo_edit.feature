@landed
# Narrative spec (not pytest-bdd bound — see features/README.md). Executable
# coverage lives in test_topo_explorer_edit_mode.py and test_c_double_detour.py.
# Engine: topo_edit.h/cpp (the edit_* CLI ops) + the explorer edit mode. Design:
# docs/internal/wishlist/wishlist-topoedit.md.
Feature: TopoEdit — transactional expert editing of a candidate topology
  As an expert chip planner
  I want to open a working copy of a candidate and apply transactional edits,
  So that I can hand-build or refine a routing topology the generator won't produce.

  # edit_topology opens a working copy (or an empty topology); each edit_* op prints
  # a verdict (check_topo violations + zero-slide pinch + wire-graph components).
  # edit_commit appends the result to the bundle's pool as a USER candidate
  # (uid-deduped); edit_abort discards.

  Scenario: open a working copy of a candidate
    Given a bundle with candidate topologies
    When I run edit_topology <bundle_id> 2
    Then a working copy of candidate 2 is opened for editing

  Scenario: open an empty topology and build a trunk
    When I run edit_topology <bundle_id> new
    And I run edit_add_trunk H <perp>
    Then a full-span horizontal trunk is added at the Hanan line and a verdict is printed

  Scenario: a new trunk spans the busterm extent, not the whole die
    # A full-span trunk would overshoot its busterms and stubs; endpoints land at
    # the extreme busterm centres where stubs drop.
    When I add a trunk to a fresh session
    Then the trunk covers exactly the blocks it serves

  Scenario: an out-of-bounds detour trunk seeds its slide from the Hanan cell
    # Placed beyond the extreme Hanan line: the block-facing side clamps at the
    # nearest grid line (perp_clamp), the outward side stays free, so NUTS has a
    # finite pull target and stubs tapping it can tighten the detour.
    When I add a trunk on a grid line outside the block bbox
    Then its slide is seeded half-open from its Hanan cell (no floating relay)

  Scenario: stub a block onto the selected segment
    When I run edit_add_stub <block> <seg#>
    Then a stub connects the block to that segment
    And with only the trunk present, S auto-selects it as the stub target

  Scenario: connect and disconnect perpendicular segments
    When I run edit_connect <i> <j>
    Then the two segments are joined at their perpendicular junction
    And edit_disconnect <i> <j> <retract_to> retracts the join

  Scenario: override a segment span
    When I run edit_set_span <seg#> <lo> <hi>
    Then the segment's along-axis span is set to [lo, hi]

  Scenario: commit appends a uid-deduped USER candidate
    When I run edit_commit pin
    Then the edited topology is appended as a USER candidate and pinned (identical uid dedups to the existing one)
    And edit_abort instead discards the working copy

  Scenario: pin a trunk's span to selected busterms (P gesture)
    # For custom topologies where two trunks share one Hanan row but each should
    # cover only a limited span: click the busterm blocks the trunk should reach.
    Given two horizontally-aligned block pairs on one Hanan row
    When I pin span (P), click each pair's busterms, and apply
    Then each trunk's span is limited to its own pair's along-axis extent (edit_set_span), with a grid line reaching a C-detour beyond the busterms

  Scenario: a layer change in an edit session survives commit
    # The +/- layer edit on the working copy is snapshotted as the pinned overrides
    # on commit, so it is not overridden by the source candidate's stale pins.
    Given an edit session started from a pre-pinned candidate
    When I change a segment's layer and commit
    Then the committed USER candidate reflects the edited layer, not the stale pin
