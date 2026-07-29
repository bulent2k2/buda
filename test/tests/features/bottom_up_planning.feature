@landed
# Narrative spec (not pytest-bdd bound — see features/README.md). Executable
# coverage lives in test_hier_*.py and the bottom-up planning tests; the engine
# design is docs/internal/hier_bottom_up_planning.md. This file is the readable
# behavioral spec for the bottom-up hierarchical planning arc.
Feature: Bottom-up hierarchical planning (solve-once, copy to instances)
  As a chip planner with a hierarchical design
  I want a repeated cell's local interconnect solved once and copied to every
  congruent instance, with the copies frozen as keepouts for higher levels,
  So that identical cells route identically and the top level plans around them.

  # A cell is marked for bottom-up planning; its local interconnect is planned
  # and NUTS'd once on a reference instance and copied to every congruent
  # instance. The copies become keepouts the higher-level (top-down) planner
  # routes around. Opt-in: the default hier flow marks nothing, so results are
  # unchanged unless a cell is marked.

  Scenario: set_bottom_up marks a cell template
    Given an open BDB with a cell "proc" instantiated 4 times, all congruent
    When I run set_bottom_up proc
    Then cell "proc" is persisted with bottom_up = on (cell.bottom_up, schema v17)

  Scenario: congruent instances are detected geometrically across orientations
    # Hierarchical rotate/flip keep the component's orient token 'N', so
    # congruence is detected from geometry, not the token.
    Given a bottom-up cell "proc" with instances placed N, S, FN, FS
    When I run the hier flow through run_planner hier
    Then the direction-preserving instances (N/S/FN/FS) copy from the cell reference directly

  Scenario: the 90-degree family splits into a rotation-class clone template
    # E/W/FE/FW instances cannot copy from the upright reference; run_planner hier
    # splits them into a virtual clone template "<cell>90" (uniquified), generated
    # from the rotated reference's actual cell-local floorplan.
    Given a bottom-up cell "proc" with both upright (N) and 90-degree-rotated (E) instances
    When I run run_planner hier
    Then a clone template "proc90" is created (bundle.cloned_from, schema v19)
    And the 90-degree instances are solved once within their class and copied

  Scenario: a non-orientation-matching instance is refused, loud
    Given a bottom-up cell "proc" with an instance in no supported orientation class
    When I run run_planner hier
    Then the instance is reported and left on the top-down path (never silently mis-copied)

  Scenario: set_bottom_up * marks every eligible cell (keepout-scope generalization)
    # '*' marks every cell with congruent placed instances: >=2 instances =
    # solve-once-copy; a single instance = solve-once + freeze its routing as a
    # keepout (nothing to copy). Non-congruent >=2-instance cells are reported
    # and left top-down (fail LOUD).
    Given an open BDB with several cells, some congruent and some not
    When I run set_bottom_up *
    Then every congruent cell is marked and the non-congruent multi-instance cells are reported
    And set_bottom_up * off clears every mark

  Scenario: align_bottom_up nudges instances onto a common track phase
    # Run after def_track_pattern + set_bottom_up, BEFORE derive_busterms, so
    # copied instances share a track phase (else stage (c) check_template_tracks
    # stops DNUTS with the mismatch report).
    Given track patterns defined and cell "proc" marked bottom-up
    When I run align_bottom_up
    Then each instance is nudged by the minimal total movement onto its rotation-class common phase
    And a move that introduces a new overlap or outside-die issue is auto-reverted (unless force)

  Scenario: check_template_tracks verifies copied instances see the same track pool
    # Stage (c): after run_nuts, before run_detailed_nuts. Compares the span-aware
    # signal-track pools each instance sees per fixed segment window.
    Given a bottom-up cell whose instances were aligned
    When I run check_template_tracks
    Then instances agree iff their offset/reflection phase fits the layer pitch with no override cutting the windows differently

  Scenario: on a track mismatch, the default policy stops DNUTS with the report
    Given a bottom-up cell whose instances are NOT track-aligned
    When I run run_detailed_nuts
    Then check_template_tracks stop (default) refuses DNUTS with the mismatch report
    And on_mismatch independent instead copies the aligned instances and solves the misaligned ones individually

  Scenario: aligned cells solve DNUTS once and copy bits and vias to siblings
    Given a bottom-up cell whose instances are ALIGNED
    When I run run_detailed_nuts
    Then DNUTS solves on a reference instance and copies bits + per-bit vias to every sibling (pre-reserved)

  Scenario: measured-infeasibility uniformity break for a stranded locked copy
    # ✅ SHIPPED 2026-07-29 as ripup_reroute's RELEASE pass (the stage-b stall
    # chain's last tier, gated on `check_template_tracks on_mismatch
    # independent`; `no_release_moves` opts out) — opens #14 fix space (a).
    # Coverage: test/tests/test_ripup_release_moves.py.  The measured case:
    # mix2_fast_bottomup bundle 166 (dogleg1 at chip/i_dogleg1_3) — pools
    # MATCH the reference, but the 4th instance's surroundings close its
    # junction slide window (dynamic occupancy no static pool comparison
    # sees); release + topo 1->2 heals 8 opens -> 0 with a clean detailed
    # check_design.  The shipped PLACEMENT fix (align_bottom_up, the
    # mix2_align_and_save fixture pair) remains the complement for designs
    # whose placement CAN move.
    Given a bottom-up class whose copied routing is measured DNUTS-open at one instance only
    When ripup_reroute stalls at stage b under the on_mismatch independent policy
    Then the RELEASE pass unlocks exactly that instance and solves it individually while the aligned siblings keep the uniform copy
