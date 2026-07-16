@landed
Feature: Pull preference model for topology segments
  As a chip planner
  I want each trunk segment to carry pull preference metadata
  (lo_pull and hi_pull) reflecting how many leaf stubs pull it
  toward the low vs high end of its slide range
  So that the topology ranker and NUTS placer can favour positions that
  minimise total stub wire, and topologies with a balanced pull are
  preferred over heavily skewed ones at equal adjusted_wl.

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
  #
  # Pull definition for an H trunk segment:
  #   lo_pull = number of V stubs going DOWN from trunk (toward lower y)
  #   hi_pull = number of V stubs going UP   from trunk (toward higher y)
  #   pull_balance = (lo_pull − hi_pull) / (lo_pull + hi_pull)   ∈ [−1, +1]
  #   pull_balance = 0  → perfectly balanced
  #   pull_balance = +1 → all stubs pull downward (trunk pulled toward lo y)
  #   pull_balance = −1 → all stubs pull upward   (trunk pulled toward hi y)
  #
  # For a V trunk segment, lo/hi refer to the x axis (left/right).
  #
  # The pull-balanced centroid is an additional trunk candidate placed at the
  # median of all leaf face positions; it minimises total stub WL AND pull_balance.
  # It is marked with a "~CEN" suffix in the topology type string.
  #
  # Coordinates: x increases right, y increases upward.

  # ---------------------------------------------------------------------------
  # L topology as the unit case: |pull_balance| = 1 on the single stub segment.
  # ---------------------------------------------------------------------------

  Scenario: L_HV topology — V segment has hi_pull=1, lo_pull=0
    # A at (0,100)-(100,200), B at (300,200)-(400,300).
    # L_HV: H stub from A at y=150, then V stub UP to B's bottom face (y=200).
    # The V stub goes toward higher y (B is above the bend) → hi_pull=1.
    # pull_balance = (0 − 1) / (0 + 1) = −1.0.
    #
    #   y=300 ─                  +------+
    #                            |      |
    #   y=250 ─                  |  B   |
    #                            |      |
    #   y=200 ─  +------+        +--x---+   ← B bottom face; busterm at B.cx=350
    #            |      |           ||       ← V stub; pulls trunk UPWARD → hi_pull
    #   y=150 ─  |  A   x===========*        ← A right face; bend (350,150)
    #            |      |
    #   y=100 ─  +------+
    #              0  100              300 400
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "B" at (300,200)-(400,300)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    And I compute pull preferences for each candidate
    Then the L_HV candidate's V segment has hi_pull=1 and lo_pull=0
    And the L_HV candidate's V segment pull_balance is -1.0

  Scenario: L_VH topology — V segment has lo_pull=1, hi_pull=0
    # A at (0,200)-(100,400), B at (300,0)-(400,150).
    # L_VH: V stub DOWN from A.bottom(y=200) to bend at B.cy=75, then H to B.
    # The V stub goes toward lower y (B is below the bend) → lo_pull=1.
    # pull_balance = (1 − 0) / (1 + 0) = +1.0.
    #
    #   y=400 ─  +------+
    #            |      |
    #   y=200 ─  +--x---+              ← A bottom face; busterm at A.cx=50
    #               ||                 ← V stub; pulls trunk DOWNWARD → lo_pull
    #   y= 75 ─     *==================x  B   ← bend (50,75); H stub; B left face
    #                                  |      |
    #   y=  0 ─                        +------+
    #              0  100                300 400
    #
    Given a block "A" at (0,200)-(100,400)
    And a block "B" at (300,0)-(400,150)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    And I compute pull preferences for each candidate
    Then the L_VH candidate's V segment has lo_pull=1 and hi_pull=0
    And the L_VH candidate's V segment pull_balance is +1.0

  # ---------------------------------------------------------------------------
  # TRUNK_H pull balance: stubs below trunk → lo_pull; stubs above → hi_pull.
  # ---------------------------------------------------------------------------

  Scenario: Balanced TRUNK_H — two stubs below, two stubs above
    # A straddles trunk at y=250 → Direct (no pull contribution).
    # B1, B2 are below trunk → lo_pull = 2.
    # C1, C2 are above trunk → hi_pull = 2.
    # pull_balance = (2 − 2) / (2 + 2) = 0.0.
    #
    #       0  80  120 200  220 300  320 400
    #   y=400 ─     +--+   +--+
    #               |  |   |  |
    #   y=350 ─     |C1x   xC2|        ← C1, C2 busterms (stubs go UP → hi_pull)
    #               |  |   |  |
    #   y=300 ─     +--+   +--+
    #                ||     ||   ← V stubs from C1, C2 up to trunk
    #   y=250 ─  +--x*=======*===   ← A.right face (Direct); trunk; T-juncs for Cs
    #            | A |||     ||
    #   y=200 ─  +---+||     ||   ← V stubs from B1, B2 down to trunk
    #                 ||     ||
    #   y=150 ─       +--+   +--+
    #                 |  |   |  |
    #   y=100 ─       |B1x   xB2|  ← B1, B2 busterms (stubs go DOWN → lo_pull)
    #                 |  |   |  |
    #   y= 50 ─       +--+   +--+
    #
    Given a block "A"  at (0,200)-(80,300)
    And a block "B1" at (120,50)-(200,150)
    And a block "B2" at (220,50)-(300,150)
    And a block "C1" at (120,300)-(200,400)
    And a block "C2" at (220,300)-(300,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B1","B2","C1","C2"] using layers M4,M5
    And I compute pull preferences for each candidate
    Then the TRUNK_H@y250 candidate trunk segment has lo_pull=2 and hi_pull=2
    And the TRUNK_H@y250 candidate trunk segment pull_balance is 0.0

  Scenario: Skewed TRUNK_H — three stubs below, one above
    # B1, B2, B3 below trunk → lo_pull=3.  C1 above trunk → hi_pull=1.
    # pull_balance = (3 − 1) / (3 + 1) = 0.5.
    #
    #       0  80              120 200  220 300  320 400  420 500
    #   y=400 ─                                   +--+
    #                                             |  |
    #   y=350 ─                                   |C1x   ← C1 (stub goes UP)
    #                                             |  |
    #   y=300 ─                                   +--+
    #                                              ||  ← 1 stub upward (hi_pull)
    #   y=250 ─  +--x*=================================   ← trunk; A Direct
    #            | A | ↓ ↓ ↓ (3 stubs downward: lo_pull=3)
    #   y=200 ─  +---+
    #                 +--+   +--+   +--+
    #   y=150 ─       |  |   |  |   |  |
    #                 |B1x   xB2|   |B3x   ← B1,B2,B3 busterms (stubs go DOWN)
    #   y=100 ─       +--+   +--+   +--+
    #                 120 200 220 300 320 400
    #
    Given a block "A"  at (0,200)-(80,300)
    And a block "B1" at (120,100)-(200,200)
    And a block "B2" at (220,100)-(300,200)
    And a block "B3" at (320,100)-(400,200)
    And a block "C1" at (420,300)-(500,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B1","B2","B3","C1"] using layers M4,M5
    And I compute pull preferences for each candidate
    Then a TRUNK_H@y250 candidate exists
    And that trunk segment has lo_pull=3 and hi_pull=1
    And that trunk segment pull_balance is 0.5

  # ---------------------------------------------------------------------------
  # Pull-balanced centroid (~CEN candidate).
  # ---------------------------------------------------------------------------

  Scenario: Pull-balanced centroid generates a TRUNK_H~CEN candidate
    # Leaves: B1 face_y=200, B2 face_y=200, C1 face_y=300.
    # Sorted face positions: [200, 200, 300].  Median = 200.
    # Centroid candidate: TRUNK_H@y200~CEN.
    # At y=200: B1 face_y=200 → Direct (lo_pull += 0).
    #           B2 face_y=200 → Direct (lo_pull += 0).
    #           C1 face_y=300 > 200 → stub going UP → hi_pull=1.
    # pull_balance = (0 − 1) / (0 + 1) = −1.0.
    #
    #       0  80   120 200  220 300  320 400
    #   y=300 ─                        +--+
    #                                  |  |
    #   y=250 ─                        |C1|
    #                                  |  |
    #   y=200 ─  +--+  +--x---+  +--x---+  ← B1 top face, B2 top face (trunk y)
    #            | A|  |  B1  |  |  B2  |    A straddles; B1, B2: Direct
    #   y=150 ─  +--+  +------+  +------+
    #
    # Trunk at y=200 (centroid): B1 and B2 are Direct; C1 has V stub going up.
    # This minimises total stub WL among all possible trunk positions.
    #
    Given a block "A"  at (0,150)-(80,250)
    And a block "B1" at (120,100)-(200,200)
    And a block "B2" at (220,100)-(300,200)
    And a block "C1" at (320,250)-(400,350)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B1","B2","C1"] using layers M4,M5
    Then a candidate of type "TRUNK_H@y200~CEN" exists
    And the ~CEN candidate has lo_pull=0 and hi_pull=1

  Scenario: Pull-balanced centroid is at the median of all leaf face positions
    # Five leaves: face_y values [100, 150, 200, 300, 350].
    # Median = 200.  Centroid candidate at y=200.
    #
    Given a block "src" at (0,150)-(80,250)
    And a block "d1"  at (100,50)-(180,150)
    And a block "d2"  at (200,100)-(280,200)
    And a block "d3"  at (300,150)-(380,250)
    And a block "d4"  at (400,250)-(480,350)
    And a block "d5"  at (500,300)-(580,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["d1","d2","d3","d4","d5"] using layers M4,M5
    Then a candidate with type suffix "~CEN" exists
    And its trunk y position equals the median of all leaf face_y positions

  # ---------------------------------------------------------------------------
  # Three-key sort: adjusted_wl → −min_slide → |pull_balance|.
  # ---------------------------------------------------------------------------

  Scenario: Equal adjusted_wl and min_slide — smaller |pull_balance| ranks first
    # Two TRUNK_H candidates at the same adjusted_wl and min_slide.
    # The one closer to pull_balance=0 (more balanced) ranks first.
    #
    Given a block "A"  at (0,150)-(80,250)
    And a block "B1" at (120,100)-(200,200)
    And a block "B2" at (220,100)-(300,200)
    And a block "C1" at (320,300)-(400,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B1","B2","C1"] using layers M4,M5
    And two TRUNK_H candidates have equal adjusted_wl and equal min_slide
    Then the candidate with smaller absolute pull_balance ranks first

  Scenario: Pull-balanced centroid ranks above equally-scored skewed trunk
    # The ~CEN candidate and a non-centroid TRUNK_H may have the same adjusted_wl.
    # ~CEN's pull_balance is closer to 0 → it ranks above the skewed candidate.
    #
    Given a block "A"  at (0,150)-(80,250)
    And a block "B1" at (120,100)-(200,200)
    And a block "B2" at (220,100)-(300,200)
    And a block "C1" at (320,300)-(400,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate and rank multicast candidates from "A" to ["B1","B2","C1"] using layers M4,M5
    Then for any equal-adjusted_wl TRUNK_H, the ~CEN candidate ranks first when its absolute pull_balance is smaller

  # ---------------------------------------------------------------------------
  # ConnSeg pull fields populated after ConnTopology.build().
  # ---------------------------------------------------------------------------

  Scenario: Trunk ConnSegs have lo_pull, hi_pull, pull_balance populated
    # After ConnTopology.build(), every trunk segment must have all pull fields set.
    #
    Given a block "A"  at (0,150)-(80,250)
    And a block "B1" at (120,100)-(200,200)
    And a block "C1" at (320,300)-(400,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B1","C1"] using layers M4,M5
    And I build ConnTopology for each candidate
    Then every trunk ConnSeg has lo_pull >= 0
    And every trunk ConnSeg has hi_pull >= 0
    And every trunk ConnSeg has pull_balance in the range [-1.0, 1.0]
    And lo_pull + hi_pull equals the total number of stubs on that segment

  Scenario: Leaf stub ConnSegs have lo_pull=0 and hi_pull=0
    # A stub segment connects a leaf face to the trunk. No sub-stubs hang off it.
    # → lo_pull = hi_pull = 0 on every stub segment.
    #
    Given a block "A"  at (0,150)-(80,250)
    And a block "B1" at (120,100)-(200,200)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B1" using layers M4,M5
    And I build ConnTopology for each candidate
    Then every stub ConnSeg has lo_pull=0 and hi_pull=0
