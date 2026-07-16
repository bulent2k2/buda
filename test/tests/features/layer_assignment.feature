@orphaned
# STATUS: not bound to any step file (never run). This thin 2-scenario sketch of
# an "optimistic layer assigner" was superseded by span_aware_layer_assignment.feature
# (LANDED, bound to test_span_layer_assignment.py) and the congestion planner's
# actual layer assignment (span-scaled non-TOP penalty, kHeight short-stub steering,
# TOP-preference) — see also planner_knobs.feature (LANDED). Kept for historical
# reference; a candidate for the attic. Do not rely on it for coverage.
Feature: Optimistic Layer Assignment
  As a chip planner
  I want to assign topology segments to the highest available metal layers
  So that I can establish a high-performance baseline before congestion optimization

  Background:
    Given a layer stack with:
      | Name    | ID | Direction  | Type |
      | M_Low_H | 1  | HORIZONTAL | LOW  |
      | M_Low_V | 2  | VERTICAL   | LOW  |
      | M_Top_H | 3  | HORIZONTAL | TOP  |
      | M_Top_V | 4  | VERTICAL   | TOP  |

  Scenario: Initial Assignment (HV Topology)
    Given a topology "L_HV" with segments:
      | Segment | Direction  | Length |
      | Seg_1   | HORIZONTAL | 100    |
      | Seg_2   | VERTICAL   | 50     |
    When I run the optimistic layer assigner
    Then "Seg_1" should be on layer "M_Top_H" (ID 3)
    And "Seg_2" should be on layer "M_Top_V" (ID 4)

  Scenario: Demotion Preparation (Identification)
    # The assigner should tag bundles for future NUTS processing
    Given a short bundle with total length 20
    And a long bundle with total length 2000
    When I assess demotion candidates
    Then the short bundle should be flagged as "Demotable"
    And the long bundle should be flagged as "Critical"
