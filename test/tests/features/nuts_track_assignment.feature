Feature: Non-Uniform Track Sharing (NUTS)
  As a chip planner
  I want to assign concrete perpendicular track positions to every bus segment
  So that no two buses on the same layer with overlapping spans physically overlap,
  while each bus stays within its hard Hanan-grid-cell interval constraint.

  # Background: a simple two-block floorplan with one horizontal layer (M3)
  # and one vertical layer (M4).
  Background:
    Given a floorplan with blocks:
      | Name   | x1  | y1  | x2  | y2  |
      | u_src  | 0   | 0   | 100 | 100 |
      | u_dst  | 400 | 400 | 500 | 500 |
    And a layer stack with:
      | Name | ID | Direction  |
      | M3   | 3  | HORIZONTAL |
      | M4   | 4  | VERTICAL   |

  # -----------------------------------------------------------------------
  Scenario: Single segment is placed within its interval
    Given a single horizontal bus segment
      | bundle_id | span_lo | span_hi | interval_lo | interval_hi | width |
      | 1         | 0       | 300     | 100         | 200         | 10    |
    When I run NUTS with track_pitch 1.0
    Then segment (bundle=1, seg=0) should be placed
    And  segment (bundle=1, seg=0) track_position should be >= 100
    And  segment (bundle=1, seg=0) track_position + width should be <= 200

  # -----------------------------------------------------------------------
  Scenario: Two non-overlapping segments share no tracks (no conflict possible)
    Given two horizontal bus segments on layer 3 with non-overlapping spans:
      | bundle_id | span_lo | span_hi | interval_lo | interval_hi | width |
      | 1         | 0       | 100     | 100         | 200         | 10    |
      | 2         | 150     | 300     | 100         | 200         | 10    |
    When I run NUTS with track_pitch 1.0
    Then there should be 0 track overlaps
    And  there should be 0 interval violations

  # -----------------------------------------------------------------------
  Scenario: Two overlapping segments are separated to different tracks
    Given two horizontal bus segments on layer 3 with overlapping spans:
      | bundle_id | span_lo | span_hi | interval_lo | interval_hi | width |
      | 1         | 0       | 300     | 0           | 500         | 20    |
      | 2         | 100     | 400     | 0           | 500         | 20    |
    When I run NUTS with track_pitch 1.0
    Then there should be 0 track overlaps
    And  segment (bundle=1, seg=0) track_position should differ from
         segment (bundle=2, seg=0) track_position by at least 21 (width + pitch)

  # -----------------------------------------------------------------------
  Scenario: Non-uniform widths are respected during placement
    Given three horizontal bus segments with different widths:
      | bundle_id | span_lo | span_hi | interval_lo | interval_hi | width |
      | 1         | 0       | 300     | 0           | 1000        | 5     |
      | 2         | 0       | 300     | 0           | 1000        | 30    |
      | 3         | 0       | 300     | 0           | 1000        | 15    |
    When I run NUTS with track_pitch 2.0
    Then there should be 0 track overlaps
    And  all segments should have track_position >= 0

  # -----------------------------------------------------------------------
  Scenario: Layers are solved independently (H and V segments do not conflict)
    Given a horizontal segment on layer 3 and a vertical segment on layer 4,
    both spanning the same routing range:
      | bundle_id | layer | span_lo | span_hi | interval_lo | interval_hi | width |
      | 1         | 3     | 0       | 300     | 100         | 200         | 10    |
      | 2         | 4     | 0       | 300     | 100         | 200         | 10    |
    When I run NUTS with track_pitch 1.0
    Then there should be 0 track overlaps
    # Different layers never conflict — NUTS solves each independently.

  # -----------------------------------------------------------------------
  # Regression: two segments whose spans start at the same coordinate (same
  # span_lo) must still be separated.  The sweep-line must add each segment
  # to the active set after placing it so the next segment sees it as
  # occupied.  Without that push, every segment lands at its preferred
  # coordinate regardless of prior placements.
  Scenario: Two co-starting segments with overlapping spans are separated
    Given two horizontal bus segments on layer 3 with the same span start:
      | bundle_id | span_lo | span_hi | interval_lo | interval_hi | width |
      | 1         | 100     | 400     | 200         | 500         | 10    |
      | 2         | 100     | 300     | 200         | 500         | 10    |
    When I run NUTS with track_pitch 1.0
    Then there should be 0 track overlaps
    And  segment (bundle=1, seg=0) track_position should differ from
         segment (bundle=2, seg=0) track_position by at least 11 (width + pitch)

  # -----------------------------------------------------------------------
  # Regression: three segments all starting at the same span_lo, each
  # must be placed at a distinct perpendicular position (active-set grows
  # incrementally so segment k+1 sees segments 0..k as occupied).
  Scenario: Three co-starting segments with fully overlapping spans are all separated
    Given three horizontal bus segments on layer 3 all starting at span_lo=0:
      | bundle_id | span_lo | span_hi | interval_lo | interval_hi | width |
      | 1         | 0       | 500     | 0           | 1000        | 10    |
      | 2         | 0       | 400     | 0           | 1000        | 10    |
      | 3         | 0       | 300     | 0           | 1000        | 10    |
    When I run NUTS with track_pitch 1.0
    Then there should be 0 track overlaps

  # -----------------------------------------------------------------------
  Scenario: Interval too narrow for bus width triggers a violation count
    Given a horizontal bus segment whose interval is smaller than its width:
      | bundle_id | span_lo | span_hi | interval_lo | interval_hi | width |
      | 1         | 0       | 300     | 100         | 105         | 20    |
    When I run NUTS with track_pitch 1.0
    Then the segment should still be placed (best-effort)
    And  the result should report at least 1 interval violation
