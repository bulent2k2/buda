@landed
Feature: Topology ranking by routing flexibility score
  As a chip planner
  I want topologies to be ranked not only by estimated wirelength
  but also by the slide range available on each segment
  So that topologies with more NUTS placement freedom are preferred
  even at a small wirelength premium, improving downstream routability.

  # Diagram notation used throughout this file:
  #
  #   +----+  block boundary  (+= corner,  - top/bottom,  | sides)
  #   | Nm |  block name
  #   +----+
  #
  #   x      busterm: bus segment endpoint on a block face
  #              on top/bottom face → x replaces - in the boundary line
  #              on left/right face → x replaces | in the boundary line
  #   *      T-junction or bend (NOT on a block face)
  #   ===    horizontal bus segment
  #   ||     vertical bus segment
  #   ~~~    slide range boundary line (segment may be placed anywhere between two ~~~ lines)
  #   ─────  Hanan reference line (not a routed segment)
  #
  # Ranking formula:
  #   adjusted_wl = estimated_wl × (1 − kFlex × clamp(min_slide / ref_slide, 0, 1))
  #
  # Default parameters:
  #   kFlex     = 0.20   (max 20 % wirelength discount for maximum slide)
  #   ref_slide = 60     (slide value at which full discount applies)
  #
  # Topologies are sorted ascending by adjusted_wl.
  # Ties broken by −min_slide (prefer more slide), then |pull_balance|.
  #
  # Coordinates: x increases right, y increases upward.

  # ---------------------------------------------------------------------------
  # Slide range computation: perp_lo / perp_hi per segment.
  # ---------------------------------------------------------------------------

  Scenario: Slide range on the H segment of an L_HV topology
    # A at (0,100)-(100,200), B at (300,200)-(400,300).
    # L_HV: H stub at y=A.cy=150 from x=100 to x=350; V stub at x=350 from y=150 to y=200.
    #
    # H segment perp axis = y. Hanan y lines: {100, 200, 300}.
    # Hanan cell containing y=150: [100, 200].
    # perp_lo = 100.  perp_hi = 180: the raw Hanan cell top is y=200 (B's bottom
    # face), but the H segment cannot slide onto that face — the perpendicular V
    # stub up to B must keep at least the default minimum stub length (20 units,
    # MinStubLength.global) — so the upper bound insets to 200 − 20 = 180.
    #
    #   ~~~y=180~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  ← perp_hi (200 − 20)
    #   y=200 ─  +------+         +--x---+   ← A.y2; B bottom face (B.y1=200)
    #            |      |            ||
    #   y=150 ─  |  A   x============*        ← H stub; bend at (350,150)
    #            |      |
    #   y=100 ─  +------+
    #   ~~~y=100~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  ← perp_lo
    #              0  100              300 400
    #
    #   H segment slide = perp_hi(180) − perp_lo(100) = 80
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "B" at (300,200)-(400,300)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    And I compute slide ranges for each candidate
    Then the L_HV candidate's H segment has perp_lo=100 and perp_hi=180
    And the L_HV candidate's H segment slide equals 80

  Scenario: Slide range on the V trunk of a Z_HVH topology
    # A at (0,100)-(100,200), B at (300,200)-(400,300). "mid" at (170,0)-(230,500)
    # provides trunk position x=170 (and x=230) between A.x2=100 and B.x1=300.
    #
    # Z_HVH at x=170: V trunk perp axis = x. Hanan x lines: {0,100,170,230,300,400}.
    # Hanan cell containing x=170: [100, 230] (next Hanan x above 170 is 230).
    # perp_lo = 100, perp_hi = 230.  slide = 130.
    #
    #         0  100  170 230        300 400
    #   y=300 ─                      +------+
    #                                |      |
    #   y=250 ─                      |  B   |
    #                                |      |
    #   y=200 ─  +------+            +--x---+   ← B.y1 face; busterm at B.cx
    #            |      |               ||
    #   y=150 ─  |  A   x====*          ||       ← A.right; H stub; T-junc(170,150)
    #            |      |    ||         ||
    #   y=100 ─  +------+    ||  ← V trunk at x=170 (perp cell [100,230])
    #                        ||
    #   ←──perp_lo=100  perp_hi=230──→
    #   ~~x=100~~~~~~~~~~~~~~~~x=230~~  (slide zone for V trunk)
    #
    Given a block "A"   at (0,100)-(100,200)
    And a block "B"   at (300,200)-(400,300)
    And a block "mid" at (170,0)-(230,500)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    And I compute slide ranges for each candidate
    Then the Z_HVH@x200 candidate's V trunk segment has perp_lo=120 and perp_hi=280
    And the Z_HVH@x200 candidate's V trunk segment slide equals 160

  # ---------------------------------------------------------------------------
  # adjusted_wl formula: Z beats L when slide discount offsets extra wirelength.
  # ---------------------------------------------------------------------------

  Scenario: Z topology ranked above L when its slide discount covers extra WL
    # A at (0,100)-(100,200), B at (300,200)-(400,300). "mid" at (170,0)-(230,500).
    # kFlex=0.20, ref_slide=60.
    #
    # L_HV: WL = H(100→350=250) + V(150→200=50) = 300.
    #   min_slide(L_HV) = min(H_slide=100, V_slide=100) = 100.
    #   adjusted_wl(L) = 300 × (1 − 0.20 × min(100/60,1)) = 300 × 0.80 = 240.
    #
    # Z_HVH@x170: WL = H_A(100→170=70) + V(150→250=100) + H_B(170→300=130) = 300.
    #   min_slide(Z) = min(H_A_slide=100, V_slide=130, H_B_slide=100) = 100.
    #   adjusted_wl(Z) = 300 × 0.80 = 240.   [tied → break by min_slide; Z wins]
    #
    # (With wider gaps, Z's absolute WL is lower and it wins outright.)
    #
    Given a block "A"   at (0,100)-(100,200)
    And a block "B"   at (300,200)-(400,300)
    And a block "mid" at (170,0)-(230,500)
    And kFlex is 0.20
    And ref_slide is 60
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate and rank candidates from "A" to "B" using layers M4,M5
    Then each candidate has adjusted_wl = estimated_wl × (1 − kFlex × clamp(min_slide/ref_slide,0,1))
    And the Z_HVH@x200 candidate's adjusted_wl equals 240
    And the L_HV candidate's adjusted_wl equals 240

  # ---------------------------------------------------------------------------
  # kFlex = 0 disables the flexibility discount entirely.
  # ---------------------------------------------------------------------------

  Scenario: kFlex=0 — adjusted_wl equals estimated_wl for all topologies
    # With kFlex=0, the slide discount term vanishes; topologies rank by raw WL.
    #
    Given a block "A"   at (0,100)-(100,200)
    And a block "B"   at (300,200)-(400,300)
    And a block "mid" at (170,0)-(230,500)
    And kFlex is 0.0
    And ref_slide is 60
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate and rank candidates from "A" to "B" using layers M4,M5
    Then for every candidate adjusted_wl equals estimated_wl

  # ---------------------------------------------------------------------------
  # ref_slide boundary: clamp(s/ref_slide, 0, 1) saturates at 1 when s ≥ ref_slide.
  # ---------------------------------------------------------------------------

  Scenario: Full discount when min_slide equals ref_slide
    # I_H between A and B: trunk at y=200 (A.cy=B.cy=200), one segment.
    # Hanan y: {100, 300}.  Perp cell = [100, 300].  slide = 200.
    # clamp(200/60, 0, 1) = 1.0.  discount = kFlex × 1.0 = 0.20.
    # adjusted_wl = estimated_wl × (1 − 0.20) = estimated_wl × 0.80.
    #
    #   ~~~y=300~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  ← perp_hi
    #   y=300 ─  +------+                        +------+
    #            |      |                        |      |
    #   y=200 ─  |  A   x========================x  B   |  ← I_H trunk at y=200
    #            |      |                        |      |
    #   y=100 ─  +------+                        +------+
    #   ~~~y=100~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  ← perp_lo
    #
    Given a block "A" at (0,100)-(100,300)
    And a block "B" at (400,100)-(500,300)
    And kFlex is 0.20
    And ref_slide is 60
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate and rank candidates from "A" to "B" using layers M4,M5
    Then the I_H candidate has min_slide = 200
    And the I_H candidate discount factor is 0.20
    And the I_H candidate adjusted_wl equals estimated_wl multiplied by 0.80

  Scenario: Zero discount when min_slide equals 0
    # A and B are exactly adjacent (touching faces); the one segment has zero slide.
    # clamp(0/60, 0, 1) = 0.  adjusted_wl = estimated_wl × 1.0.
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "B" at (100,100)-(200,200)
    And kFlex is 0.20
    And ref_slide is 60
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate and rank candidates from "A" to "B" using layers M4,M5
    And I find a candidate with min_slide = 0
    Then that candidate's adjusted_wl equals estimated_wl

  # ---------------------------------------------------------------------------
  # Secondary tie-break: among equal adjusted_wl, prefer larger min_slide.
  # ---------------------------------------------------------------------------

  Scenario: Equal adjusted_wl — higher min_slide ranks first
    # Two Z candidates at different trunk x positions may have the same adjusted_wl
    # because both are clamped at ref_slide (both slide ≥ ref_slide).
    # The one with larger min_slide ranks first.
    #
    Given a block "A"   at (0,100)-(100,200)
    And a block "B"   at (300,200)-(400,300)
    And a block "mid" at (170,0)-(230,500)
    And kFlex is 0.20
    And ref_slide is 60
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate and rank candidates from "A" to "B" using layers M4,M5
    And two candidates have equal adjusted_wl
    Then the candidate with larger min_slide ranks first among those two
