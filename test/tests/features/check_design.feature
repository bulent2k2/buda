@landed
# Narrative spec (not pytest-bdd bound — see features/README.md). Executable
# coverage lives in test_check_design_hbundle.py, test_check_layer_dir.py, and the
# per-violation verify tests. Engine: verify.h/cpp (check_topo / check_nuts /
# check_dnuts). This is the readable spec for the design-audit / verify spine.
Feature: check_design — the design-audit spine (verify at every stage)
  As a chip planner
  I want a report-only audit at the current pipeline stage,
  So that connectivity opens, illegal layers, keepout crossings, and stranded
  bits are surfaced (never silently routed).

  # check_design (legacy alias check_connectivity) auto-detects the stage: topo
  # (nominal ConnTopology positions), nuts (NUTS-placed positions), or dnuts
  # (per-bit NetSegment positions). It is REPORT-ONLY — it never filters or aborts;
  # generation's coverage gate is what drops uncovered candidates.

  Scenario: an open segment is flagged (SEG_OPEN)
    Given a topology whose segments do not form a connected wire graph
    When I run check_design
    Then a SEG_OPEN violation is reported

  Scenario: a busterm with no tap is flagged (BUSTERM_OPEN)
    Given a candidate leaving a bundle block with no busterm tap and no pass-through
    When I run check_design
    Then a BUSTERM_OPEN violation is reported

  Scenario: a segment landing off a block face is flagged (BUSTERM_FACE)
    Given a stub whose endpoint is not on the block face it claims
    When I run check_design
    Then a BUSTERM_FACE violation is reported

  Scenario: a dropped net driver is flagged (NET_DRIVER_OPEN)
    Given a CONVERGENT multi-driver bundle whose topology attaches only one driver
    When I run check_design
    Then a NET_DRIVER_OPEN violation is reported

  Scenario: a silent feedthrough relay is flagged (FEEDTHRU_RELAY)
    # A single-rect block whose incident wires do not actually touch — used as a
    # relay. Suppressed only for blocks the bundle declared via set_feedthru.
    Given an undeclared block silently relaying two stubs
    When I run check_design
    Then a FEEDTHRU_RELAY violation is reported, but a declared feedthru is accepted

  Scenario: a wrong-direction wire is flagged (LAYER_DIR)
    Given an H segment assigned to a V layer after run_nuts
    When I run check_design
    Then a LAYER_DIR violation is reported

  Scenario: a placed wire crossing a keepout is flagged (KEEPOUT_CROSS)
    # Zones come from the session floorplan (zone_fp), so a hier bundle's cell-local
    # floorplan cannot mask a conflict.
    Given a NUTS-placed segment lying on a keepout overlapping its span
    When I run check_design
    Then a KEEPOUT_CROSS violation is reported

  Scenario: two different bits sharing a layer+track are flagged (BIT_SHORT)
    Given two different nets of one bundle sharing a layer+track over an extended span in dnuts
    When I run check_design
    Then a BIT_SHORT violation is reported

  Scenario: an unplaced bit is flagged (UNPLACED)
    Given a bit with no signal track available after run_detailed_nuts
    When I run check_design
    Then an UNPLACED violation is reported

  Scenario: check_design all audits every candidate topology
    Given multiple candidate topologies before planning
    When I run check_design all
    Then every candidate is audited (auto-promoted to all-candidates mode pre-planning)
