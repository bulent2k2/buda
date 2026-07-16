@landed
# Narrative spec (not pytest-bdd bound — see features/README.md). Executable
# coverage lives in test_gds_export.py (import<->export round-trip incl.
# rotation/mirror) and tools/gds_demo.py. Engine: gds_io.cpp (Phases G0-G4).
# Design: docs/internal/gds_oa_interchange.md. All stored coordinates are um.
Feature: GDSII interchange (import + export against the BDB)
  As a chip planner in a GDS-based flow
  I want to import a GDSII layout into the BDB and export the placed-and-routed
  result back to deterministic GDSII,
  So that BUDA sits inside a GDS interchange path without OpenAccess.

  Scenario: def_gds_layer binds a metal to a GDS layer/datatype pair
    When I run def_gds_layer <buda_layer_id> <gds_layer> <dt>
    Then import treats shapes on that pair as wires (not macro outlines) and export writes that metal to it

  Scenario: import rebuilds the component hierarchy from structures and SREF/AREF
    Given a GDSII file with structures and SREF/AREF placements
    When I run import_gds <file>
    Then structures become cells with recursive footprints and placements become the component hierarchy

  Scenario: TEXT labels recover nets and pins (zero-Verilog hier flow)
    Given a labeled GDSII file
    When I run import_gds <file> labels <csv>
    Then net/pin rows are recovered from the TEXT labels so the hier flow runs with no Verilog

  Scenario: shapes on mapped routing pairs are excluded from cell footprints
    Given def_gds_layer mappings for the routing metals
    When I import a GDS with wires on those pairs
    Then the mapped shapes are treated as wires, not part of the macro outline

  Scenario: export streams the persisted placed-and-routed BDB to deterministic GDS
    # Cells -> structures + SREFs (instance orient via component.orient);
    # net_segment wires (bus_segment fallback) on mapped pairs; vias as squares;
    # one net-name TEXT per pin. Zeroed timestamps -> deterministic bytes.
    Given a persisted, routed BDB
    When I run export_gds <file.gds>
    Then the output is deterministic GDSII re-importable with the same layer map

  Scenario: the import <-> export round-trip is exact, including rotation and mirror
    Given a design with rotated and mirrored instances
    When I export_gds then import_gds with the same map
    Then the component hierarchy, placements, orientations, and routed shapes round-trip
