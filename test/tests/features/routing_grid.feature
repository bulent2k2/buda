@doc
Feature: Routing Grid — physical track structure per metal layer
  As a chip planner
  I want to define repeating power/signal/clock track patterns per layer
  So that abstract bus segments can be mapped to concrete signal tracks
  while respecting pre-routed power and clock blockages.

  # Standard test pattern used throughout:
  #   [POWER(w=2,sp=1), SIGNAL(w=1,sp=1), SIGNAL(w=1,sp=1),
  #    GROUND(w=2,sp=1), SIGNAL(w=1,sp=1), SIGNAL(w=1,sp=1)]
  # unit_pitch = 14, signal widths = 4, signal_density = 4/14 ≈ 0.286
  # Track centres (origin=0): POWER@1.0, SIG@3.5, SIG@5.5, GND@8.0, SIG@10.5, SIG@12.5

  # -----------------------------------------------------------------------
  Scenario: unit_pitch is the sum of all (width + space_after) in one repeating unit
    Given a TrackPattern with origin 0 and slots:
      | type   | label | width | space_after |
      | POWER  | VDD   | 2.0   | 1.0         |
      | SIGNAL | sig   | 1.0   | 1.0         |
      | SIGNAL | sig   | 1.0   | 1.0         |
      | GROUND | GND   | 2.0   | 1.0         |
      | SIGNAL | sig   | 1.0   | 1.0         |
      | SIGNAL | sig   | 1.0   | 1.0         |
    Then unit_pitch should be 14.0

  # -----------------------------------------------------------------------
  Scenario: signal_density is the fraction of unit pitch occupied by SIGNAL slots
    Given the standard 6-slot pattern with unit_pitch 14
    Then signal_density should be approximately 0.2857
    # 4 signal units wide out of 14 total

  # -----------------------------------------------------------------------
  Scenario: dilution_factor is the reciprocal of signal_density
    Given the standard 6-slot pattern with unit_pitch 14
    Then dilution_factor should be approximately 3.5
    # 14 / 4 = 3.5 — abstract bus width must be multiplied by this to reserve space

  # -----------------------------------------------------------------------
  Scenario: tracks_in_range returns all track centres within the interval
    Given the standard 6-slot pattern with origin 0
    When I call tracks_in_range(lo=0, hi=14)
    Then I should get 6 tracks with centres at:
      | centre | type   |
      | 1.0    | POWER  |
      | 3.5    | SIGNAL |
      | 5.5    | SIGNAL |
      | 8.0    | GROUND |
      | 10.5   | SIGNAL |
      | 12.5   | SIGNAL |

  # -----------------------------------------------------------------------
  Scenario: tracks_in_range tiles correctly across two full repeating units
    Given the standard 6-slot pattern with origin 0
    When I call tracks_in_range(lo=0, hi=28)
    Then I should get 12 tracks total
    And the 7th track should be POWER at centre 15.0
    # Second unit starts at 14: POWER centre = 14 + 1 = 15

  # -----------------------------------------------------------------------
  Scenario: tracks_in_range with non-zero origin shifts all centres
    Given the standard 6-slot pattern with origin 3.0
    When I call tracks_in_range(lo=3, hi=17)
    Then the first track centre should be 4.0
    # With origin=3, POWER centre = 3 + 1 = 4.0

  # -----------------------------------------------------------------------
  Scenario: tracks_in_range excludes tracks whose centres fall outside [lo, hi]
    Given the standard 6-slot pattern with origin 0
    When I call tracks_in_range(lo=4, hi=11)
    Then I should get 3 tracks
    And all returned centres should be >= 4 and <= 11
    # Centres in [0,14]: 1.0 (out), 3.5 (out), 5.5 (in), 8.0 (in), 10.5 (in), 12.5 (out)

  # -----------------------------------------------------------------------
  Scenario: signal_tracks_in returns only SIGNAL-type slots
    Given the standard 6-slot pattern with origin 0
    When I call signal_tracks_in(x=0, lo=0, hi=14)
    Then I should get 4 tracks
    And all returned tracks should have type SIGNAL
    And their centres should be 3.5, 5.5, 10.5, 12.5

  # -----------------------------------------------------------------------
  Scenario: signal_tracks_in over a narrow interval returns a subset
    Given the standard 6-slot pattern with origin 0
    When I call signal_tracks_in(x=0, lo=0, hi=7)
    Then I should get 2 tracks with centres 3.5 and 5.5
    # GND centre 8.0 is outside [0, 7]

  # -----------------------------------------------------------------------
  Scenario: RoutingGridStack define_layer and get_layer_grid round-trip
    Given a RoutingGridStack
    When I define layer 4 with the standard pattern
    Then get_layer_grid(4) should return a RoutingGrid
    And calling signal_tracks_in on that grid with lo=0, hi=14 should return 4 tracks

  # -----------------------------------------------------------------------
  Scenario: PatternOverride takes precedence over global pattern inside its region
    Given a RoutingGridStack with layer 4 using the standard pattern (origin=0)
    And a pattern override for layer 4 in region (x1=100, y1=0, x2=200, y2=500)
      with a 2-slot pattern: [POWER(w=1,sp=0), SIGNAL(w=1,sp=0)]
    When I call effective_pattern_at(x=150, y=250) on layer 4
    Then the returned pattern should have unit_pitch 2.0
    # Point is inside the override region

  # -----------------------------------------------------------------------
  Scenario: Global pattern applies outside override region
    Given a RoutingGridStack with layer 4 using the standard pattern (origin=0)
    And a pattern override in region (x1=100, y1=0, x2=200, y2=500)
    When I call effective_pattern_at(x=50, y=250) on layer 4
    Then the returned pattern should have unit_pitch 14.0
    # Point is outside the override region → global pattern

  # -----------------------------------------------------------------------
  Scenario: Multiple signal tracks across two tiled units are all returned
    Given a RoutingGridStack with layer 4 using the standard pattern (origin=0)
    When I call signal_tracks_in(x=0, lo=0, hi=28)
    Then I should get 8 signal tracks
    # 4 per unit × 2 units
