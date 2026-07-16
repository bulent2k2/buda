@doc
Feature: Detailed NUTS — expand abstract bus segments to concrete bit-wire tracks
  As a chip planner
  I want each abstract BusSegment to be split into N individual NetSegments
  snapped to exact signal track positions from the RoutingGrid,
  So that every bit-wire has a physical track that respects power/clock blockages.

  # Routing grid used throughout (origin=0, unit_pitch=14):
  #   [POWER(w=2,sp=1), SIGNAL(w=1,sp=1), SIGNAL(w=1,sp=1),
  #    GROUND(w=2,sp=1), SIGNAL(w=1,sp=1), SIGNAL(w=1,sp=1)]
  # Signal track centres in one unit [0,14]: 3.5, 5.5, 10.5, 12.5

  # -----------------------------------------------------------------------
  Scenario: LO_HI assigns the first bit_width signal tracks, low-to-high
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=2, interval=[0, 14], bit_order=LO_HI
    When I run detailed NUTS
    Then the result should contain 2 NetSegments for that BusSegment
    And bit_index=0 should be at track_position 3.5
    And bit_index=1 should be at track_position 5.5
    # First two signal tracks in [0,14] are at 3.5 and 5.5

  # -----------------------------------------------------------------------
  Scenario: HI_LO assigns the last bit_width signal tracks, high-to-low
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=2, interval=[0, 14], bit_order=HI_LO
    When I run detailed NUTS
    Then bit_index=0 should be at track_position 12.5
    And bit_index=1 should be at track_position 10.5
    # Last two signal tracks in [0,14] are at 12.5 and 10.5 (reversed)

  # -----------------------------------------------------------------------
  Scenario: All bit_width bits are assigned; result has exactly bit_width NetSegments
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=4, interval=[0, 14], bit_order=LO_HI
    When I run detailed NUTS
    Then the result should contain 4 NetSegments
    And num_unplaced should be 0
    And track positions should be 3.5, 5.5, 10.5, 12.5 in order

  # -----------------------------------------------------------------------
  Scenario: Power and ground tracks are never assigned to signal bits
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=4, interval=[0, 14], bit_order=LO_HI
    When I run detailed NUTS
    Then no NetSegment should have track_position 1.0
    And no NetSegment should have track_position 8.0

  # -----------------------------------------------------------------------
  Scenario: Narrow interval returns only tracks within [interval_lo, interval_hi]
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=2, interval=[4.0, 11.0], bit_order=LO_HI
    When I run detailed NUTS
    Then the result should contain 2 NetSegments
    And track positions should be 5.5 and 10.5
    # In [4, 11]: signal tracks at 5.5 and 10.5; 3.5 and 12.5 are outside

  # -----------------------------------------------------------------------
  Scenario: timing_critical picks the tightest contiguous window of signal tracks
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=2, interval=[0, 14], timing_critical=true, bit_order=LO_HI
    When I run detailed NUTS
    Then bit_index=0 should be at track_position 3.5
    And bit_index=1 should be at track_position 5.5
    # (3.5, 5.5) is contiguous — no blocking track between them.
    # (5.5, 10.5) is NOT contiguous — GROUND at 8.0 lies between them.

  # -----------------------------------------------------------------------
  Scenario: timing_critical skips non-contiguous windows and finds the next valid one
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=2, interval=[5.0, 14.0], timing_critical=true, bit_order=LO_HI
    When I run detailed NUTS
    Then bit_index=0 should be at track_position 10.5
    And bit_index=1 should be at track_position 12.5
    # In [5,14]: signal tracks are 5.5, 10.5, 12.5.
    # (5.5, 10.5) has GROUND@8.0 between them → not contiguous.
    # (10.5, 12.5) is contiguous → selected.

  # -----------------------------------------------------------------------
  Scenario: num_unplaced is non-zero when fewer signal tracks exist than bit_width
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=6, interval=[0, 14], bit_order=LO_HI
    When I run detailed NUTS
    Then num_unplaced should be 6
    # Only 4 signal tracks available in [0,14]; 6 bits requested → entire bus unplaced

  # -----------------------------------------------------------------------
  Scenario: timing_critical with no contiguous window of required size leaves bus unplaced
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=3, interval=[0, 14], timing_critical=true, bit_order=LO_HI
    When I run detailed NUTS
    Then num_unplaced should be 3
    # In [0,14]: contiguous windows are (3.5,5.5) size=2 and (10.5,12.5) size=2.
    # No contiguous window of size 3 exists → entire bus unplaced.

  # -----------------------------------------------------------------------
  Scenario: Multiple BusSegments are expanded independently
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And BusSegment A with bit_width=2, interval=[0, 7], bit_order=LO_HI
    And BusSegment B with bit_width=2, interval=[7, 14], bit_order=LO_HI
    When I run detailed NUTS
    Then BusSegment A should have NetSegments at 3.5 and 5.5
    And BusSegment B should have NetSegments at 10.5 and 12.5
    And total NetSegments should be 4

  # -----------------------------------------------------------------------
  Scenario: NetSegment width matches the TrackSlot width, not the BusSegment width
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=1, interval=[0, 14], bit_order=LO_HI
    When I run detailed NUTS
    Then the single NetSegment should have track_position 3.5
    And it should have width 1.0

  # -----------------------------------------------------------------------
  Scenario: Pattern tiling across two units yields signal tracks from both
    Given a RoutingGrid layer with the standard pattern (unit_pitch=14, origin=0)
    And a BusSegment with bit_width=8, interval=[0, 28], bit_order=LO_HI
    When I run detailed NUTS
    Then the result should contain 8 NetSegments
    And num_unplaced should be 0
