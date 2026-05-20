Feature: Multicast topology generation for N-busterm case
  As a chip planner
  I want the topology generator to enumerate trunk-based and MST-based topologies
  for one-to-many and many-to-many bus connections
  So that multi-destination buses can be routed with minimal wire and congestion.

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
  #   ===    horizontal bus segment (trunk or stub)
  #   ||     vertical bus segment (stub or V trunk)
  #   ~~~    slide range zone
  #
  # Coordinates: x increases right, y increases upward.

  # ---------------------------------------------------------------------------
  # TRUNK_H: horizontal trunk; vertical stubs reach blocks above/below.
  # ---------------------------------------------------------------------------

  Scenario: TRUNK_H — trunk connects three blocks with V stubs
    # A=(0,150)-(100,200), B=(200,300)-(300,400), C=(400,150)-(500,200).
    # Block "ref" at (180,200)-(220,300) provides Hanan y=200,300 → trunk midpoint y=250.
    # All three destinations need V stubs to reach the trunk at y=250.
    #
    #   y=400 ─          +------+
    #                    |  B   |
    #   y=300 ─          +--x---+        ← B bottom face; V stub down to trunk
    #                       ||
    #   y=250 ─  ===========*============   ← H trunk at y=250
    #                       ||       ||
    #   y=200 ─  +--x---+          +--x---+   ← A top / C top faces; V stubs up
    #            |  A   |          |  C   |
    #   y=150 ─  +------+          +------+
    #
    Given a block "A"   at (0,150)-(100,200)
    And a block "B"   at (200,300)-(300,400)
    And a block "C"   at (400,150)-(500,200)
    And a block "ref" at (180,200)-(220,300)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","C"] using layers M4,M5
    Then a candidate of type "TRUNK_H@y250" exists
    And the TRUNK_H@y250 topology connects "A", "B", and "C"

  Scenario: TRUNK_H — multiple candidates at all Hanan y lines
    # A (left), B and C (right, different y ranges).
    # Trunk candidates are generated at every Hanan y in all blocks' y ranges.
    # Each candidate has V stubs for every block whose face_y ≠ trunk y.
    #
    #          0  80    200 280    200 280
    #   y=350 ─          +------+
    #                    |      |
    #   y=300 ─          |  C   |
    #                    |      |
    #   y=250 ─          +--x---+   ← C bottom face (when trunk is below C)
    #                       ||
    #   ~trunk candidates~  *       ← T-junction on trunk
    #
    #   y=200 ─          +--x---+   ← B top face (when trunk is above B)
    #                    |      |
    #   y=150 ─          |  B   |
    #                    |      |
    #   y=100 ─          +--x---+   ← B bottom face (when trunk is below B)
    #               +--+    *       ← T-junction on trunk
    #   y= 75 ─     |  x====       ← A right face at y=A.cy=75; H stub to trunk
    #               |A |
    #   y= 25 ─     +--+
    #
    Given a block "A" at (0,25)-(80,125)
    And a block "B" at (200,100)-(280,200)
    And a block "C" at (200,250)-(280,350)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","C"] using layers M4,M5
    Then at least one candidate of type "TRUNK_H" exists
    And every TRUNK_H candidate has a V stub to "B" when trunk y is outside B's y range
    And every TRUNK_H candidate has a V stub to "C" when trunk y is outside C's y range

  # ---------------------------------------------------------------------------
  # TRUNK_V: vertical trunk; horizontal stubs reach blocks left/right.
  # ---------------------------------------------------------------------------

  Scenario: TRUNK_V — trunk at a Hanan x connects three blocks with H stubs
    # A (bottom), B (upper-left), C (upper-right). Trunk at x=200 (Hanan x candidate).
    #
    # face_x(200): A → 150 (A.x2=150, nearest to 200), B → 200 (B.x2=200 straddles),
    #              C → 350 (C.x1=350).
    # A needs H stub: x=150→200, length=50. C needs H stub: x=200→350, length=150.
    # B straddles x=200 → Direct connector.
    #
    #       100 200   200    350 450
    #   y=500 ─  +------+
    #            |      |
    #   y=450 ─  |  B   x            ← B right face at y=B.cy=450; Direct (B straddles)
    #            |      |
    #   y=400 ─  +------+
    #                   ||  ← V trunk at x=200
    #   y=325 ─         *===========x  C   ← T-junc; H stub length=150; C.left face
    #                   ||            |      |
    #   y=275 ─         ||            |      |
    #                   ||            +------+
    #                   ||            350 450
    #   y=200 ─    +----x||           ← A right face (150,200); H stub; T-junc(200,200)
    #              |   A |||
    #   y=150 ─    +-----+||
    #              100 200
    #
    Given a block "A" at (100,150)-(200,250)
    And a block "B" at (100,400)-(200,500)
    And a block "C" at (350,250)-(450,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","C"] using layers M4,M5
    Then at least one candidate of type "TRUNK_V" exists
    And every TRUNK_V candidate has an H stub to each block whose face_x differs from the trunk x

  # ---------------------------------------------------------------------------
  # TRUNK_H_OOB: all blocks are on the same side → trunk placed outside (OOB).
  # ---------------------------------------------------------------------------

  Scenario: TRUNK_H_OOB — blocks at different y-ranges; trunk placed outside all
    # A=[300,400], B=[350,450], C=[400,500]. No single y is inside all three ranges.
    # An OOB trunk at y=280 (below A.y1=300) reaches all blocks via V stubs.
    #
    #   y=500 ─                        +------+
    #                                  |  C   |
    #   y=450 ─            +------+    |      |
    #                      |  B   |    |      |
    #   y=400 ─  +------+  |      |    +--x---+   ← C bottom face; V stub down
    #            |  A   |  +--x---+       ||
    #   y=350 ─  |      |     ||          ||       ← B bottom face; V stub down
    #            +--x---+     ||          ||
    #   y=300 ─     ||        ||          ||       ← A bottom face; V stub down
    #   y=280 ─     *==========*===========*       ← OOB trunk below all blocks
    #
    Given a block "A" at (0,300)-(100,400)
    And a block "B" at (200,350)-(300,450)
    And a block "C" at (400,300)-(500,500)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","C"] using layers M4,M5
    Then at least one candidate of type "TRUNK_H_OOB" exists
    And at least one TRUNK_H_OOB candidate connects all three blocks

  # ---------------------------------------------------------------------------
  # pass_through_count: blocks that straddle the trunk need no stub.
  # ---------------------------------------------------------------------------

  Scenario: Block straddling the trunk y increments pass_through_count
    # A=(0,150)-(100,200) needs a V stub up to trunk. B=(300,300)-(400,400) needs stub down.
    # C=(200,100)-(280,400) straddles trunk at y=250 → pass_through_count=1.
    # "guide" at (100,200)-(110,300) provides Hanan y=200, y=300 → trunk at y=250.
    #
    #   y=400 ─             +------+
    #                       |  B   |
    #   y=300 ─  +--------+ +--x---+   ← B bottom face; V stub down to trunk
    #            |        |    ||
    #   y=250 ─  |   C    x====*======   ← trunk at y=250; C straddles (no stub, count=1)
    #            | (strad)|
    #   y=200 ─  +--x---+  +--------+   ← A top face; V stub up to trunk
    #            |  A   |
    #   y=150 ─  +------+
    #
    Given a block "A"     at (0,150)-(100,200)
    And a block "B"     at (300,300)-(400,400)
    And a block "C"     at (200,100)-(280,400)
    And a block "guide" at (100,200)-(110,300)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","C"] using layers M4,M5
    Then a TRUNK_H@y250 candidate exists
    And the TRUNK_H@y250 candidate has pass_through_count at least 1
    And the TRUNK_H@y250 topology includes a V stub for "A"

  # ---------------------------------------------------------------------------
  # MST fallback: scattered blocks with no single covering trunk.
  # ---------------------------------------------------------------------------

  Scenario: MST topology for spatially scattered blocks
    # A, B, C in three different quadrants. No single H or V trunk covers all.
    # MST: A–B trunk at y=250, then B–C trunk at x=340.
    #
    #   y=500 ─       +------+
    #                 |      |
    #   y=450 ─       |  C   |
    #                 |      |
    #   y=400 ─       +--x---+        ← C bottom face; V stub down to B–C trunk
    #                    ||
    #   y=250 ─  +----+  *============x  B   ← T-junc; H trunk A–B; B right face
    #            |    x===|            |    |
    #   y=200 ─  | A  |  *            +----+
    #            |    |                0  80
    #   y=150 ─  +----+             300 380
    #
    Given a block "A" at (0,200)-(80,300)
    And a block "B" at (300,200)-(380,300)
    And a block "C" at (200,400)-(280,500)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","C"] using layers M4,M5
    Then at least one candidate of type "MST" exists
    And every MST candidate connects all three blocks
    And every MST candidate has no cycles

  # ---------------------------------------------------------------------------
  # BITRUNK: two-level trunk hierarchy.
  # ---------------------------------------------------------------------------

  Scenario: BITRUNK — root H trunk feeds two vertical branch regions
    # Four blocks in two left/right clusters. Root H trunk at y=250.
    # Left cluster (A1, A2) and right cluster (B1, B2) each get a V branch
    # that connects their two blocks with H stubs.
    #
    #       0  80  120 200         320 400  420 500
    #   y=400 ─  +--+  +--+             +--+  +--+
    #            |  |  |  |             |  |  |  |
    #   y=350 ─  |A1x  xA2|             |B1x  xB2|  ← H stubs to V branch trunks
    #            |  |  |  |             |  |  |  |
    #   y=300 ─  +--+  +--+             +--+  +--+
    #             ||    ||               ||    ||     ← V stubs to root H trunk
    #   y=250 ─  =*=====*================*=====*=    ← root H trunk
    #
    Given a block "A1" at (0,200)-(80,300)
    And a block "A2" at (100,350)-(180,450)
    And a block "B1" at (300,200)-(380,300)
    And a block "B2" at (400,350)-(480,450)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A1" to ["A2","B1","B2"] using layers M4,M5
    Then at least one candidate of type "BITRUNK" or "MST" exists

  # ---------------------------------------------------------------------------
  # Slide range on trunk segments.
  # ---------------------------------------------------------------------------

  Scenario: TRUNK_H candidate carries a non-zero slide range on its trunk segment
    # Trunk at y=200. A and B both span y=[100,300], so trunk y=200 is inside both.
    # The trunk segment's perp interval = Hanan cell = [100,300]. slide=200.
    #
    #   ~~~y=300~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  ← perp_hi (slide top)
    #   y=300 ─  +------+                        +------+
    #            |      |                        |      |
    #   y=200 ─  |  A   x========================x  B   |  ← trunk; Direct both sides
    #            |      |                        |      |
    #   y=100 ─  +------+                        +------+
    #   ~~~y=100~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  ← perp_lo (slide bottom)
    #
    #   slide = 300 − 100 = 200
    #
    Given a block "A"   at (0,150)-(80,250)
    And a block "B1"  at (120,100)-(200,200)
    And a block "B2"  at (220,100)-(300,200)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B1","B2"] using layers M4,M5
    And I compute slide ranges for each candidate
    Then every TRUNK_H candidate has a non-negative slide on its trunk segment
