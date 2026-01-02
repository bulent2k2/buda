Feature: NUTS Detailed Track Assignment
  As a chip planner
  I want to assign specific tracks to bus segments
  So that I minimize wirelength (via VICs) and avoid corner congestion

  Background:
    Given a Horizontal Layer "M3" with tracks at indices 0..100
    And the user specifies a Corner Margin of 2 tracks

  Scenario: VIC Side Preference (The "Pull" Effect)
    # A Bus connects to a block edge spanning Track 10 to Track 20.
    # The other end of the bus is "above" at Track 50.
    # Therefore, we should prefer Track 20 (top of the edge) to minimize the vertical wire.
    Given a segment with Vertical Interval Constraint [10, 20]
    And the preferred target is Track 50 (Upward Pull)
    And Tracks 10 through 20 are all empty
    When I request a track assignment
    Then the system should assign Track 20
    # If Track 20 was full, it would try 19, 18, etc.

  Scenario: Corner Margins (Keep-out Zones)
    Given a block corner exists at X=100 on Track 50
    And the user margin is 2 tracks
    When I check availability for a segment passing through X=100
    Then Track 50 should be "Blocked" or "High Cost"
    And Track 51 should be "Blocked" or "High Cost"
    And Track 48 should be "Blocked" or "High Cost"
    And Track 40 should be "Free"

  Scenario: Non-Uniform Width Allocation
    # A "Fat" bus needs 3 contiguous tracks
    Given a bus "PowerRail" requiring width 3
    And Track 10 is occupied
    When I request assignment
    Then the system should NOT assign Track 9, 10, or 11
    And it should find the first available contiguous block (e.g., 12, 13, 14)
