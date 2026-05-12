Feature: Span-Aware Layer Assignment
  As a chip planner
  I want to assign segments to metal layers based on their span length and congestion
  So that short segments use lower metals and long segments use higher metals,
  with congestion naturally overriding span preference when a layer is saturated.

  # Layer stack used by most scenarios:
  #   M4 (H, TOP, span_max=500) — preferred for short H segments
  #   M6 (H, TOP, span_min=400) — preferred for long H segments
  #   M5 (V, TOP)               — only V layer
  #   M2 (H, non-TOP, span_max=300) — fallback short H

  Scenario: Short segment prefers lower TOP-H layer
    Given a layer stack with M4 (H, TOP, span_max=500) and M6 (H, TOP, span_min=400)
    And a single H bundle with span 200
    When I run the planner with no congestion
    Then the bundle is assigned to M4

  Scenario: Long segment prefers higher TOP-H layer
    Given a layer stack with M4 (H, TOP, span_max=500) and M6 (H, TOP, span_min=400)
    And a single H bundle with span 800
    When I run the planner with no congestion
    Then the bundle is assigned to M6

  Scenario: Segment in overlap zone is assigned by congestion
    # Both M4 and M6 have span_cost=0 for span=450 (inside both windows).
    # A second same-span bundle saturates M6 first (higher ID, chosen first);
    # the third bundle should spill to M4.
    Given a layer stack with M4 (H, TOP, span_max=500) and M6 (H, TOP, span_min=400)
    And a bottleneck floorplan where H-channel capacity equals one bundle width
    And three H bundles of equal width with span 450
    When I run the planner
    Then the first two bundles use different layers
    And the third bundle is placed despite M6 being saturated

  Scenario: Non-TOP layer incurs base cost penalty
    Given a layer stack with M4 (H, TOP) and M2 (H, non-TOP, span_max=300)
    And a single H bundle with span 100
    When I run the planner with no congestion
    Then the bundle is assigned to M4
    # M2 span_cost=0 within its window, but base_cost_non_top beats that

  Scenario: Congestion on preferred layer drives spill to span-mismatched layer
    Given a layer stack with M4 (H, TOP, span_max=500) and M6 (H, TOP, span_min=400)
    And a bottleneck floorplan where H-channel capacity equals one bundle width
    And a first bundle with span 200 that fills M4
    And a second bundle with span 200
    When I run the planner
    Then the first bundle is assigned to M6 (higher ID wins tie)
    And the second bundle is assigned to M4 (M6 saturated, span mismatch accepted)

  Scenario: Hyperbolic congestion cost grows super-linearly near capacity
    # At 50% utilization cost = kCong * 1.0
    # At 90% utilization cost = kCong * 9.0  (9x, not 1.8x as linear would give)
    Given a single H layer M4 (TOP) with band capacity 100
    When I measure the congestion cost at 50% utilization (usage=50, width=0)
    And I measure the congestion cost at 90% utilization (usage=90, width=0)
    Then the 90% cost is at least 8 times the 50% cost

  Scenario: Per-layer kSpan override
    Given a layer stack with M4 (H, TOP, span_max=500) and M6 (H, TOP, span_min=400, kSpan=0.0)
    # M6 kSpan=0 means span mismatch is free on M6
    And a single H bundle with span 100
    When I run the planner with no congestion
    Then the bundle is assigned to M6
    # Tie broken by higher ID (M6 > M4), and M6 has no span penalty

  Scenario: set_planner_param kCong scales congestion cost
    Given a layer stack with M4 (H, TOP) and M6 (H, TOP, span_min=400)
    And kCong set to 100.0
    And a nearly-full M4 channel (90% utilized)
    And a short H bundle with span 100
    When I run the planner
    Then the bundle is assigned to M6
    # Even though span_cost for M6 is large (span=100 vs span_min=400),
    # kCong=100 makes M4 congestion cost dominate
