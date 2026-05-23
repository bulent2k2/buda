Feature: Over-the-Block vs Thru-the-Block TEG Routing Modes
  As a chip planner
  I want to control whether a non-rectangular (multi-rect) receiver block
  has its two sides connected by an explicit bridge segment (over-the-block)
  or left for the block's internal routing to join (thru-the-block)
  So that I can accurately model both physically-routed bridges over notched
  blocks and logically-internal connections relying on the block's own wiring.

  # Background on TEG (Terminal Equivalence Group):
  #   A non-rectangular block is represented in BUDA as a multi-rect block.
  #   Each rect is a candidate connection point — together they form a TEG.
  #   Two bus segments connecting to different rects of the same block form
  #   a "split connection" — the block is reached from two sides.
  #
  # Routing modes (set per block via teg_mode flag in add_block):
  #
  #   thru-the-block (default):
  #     Each topology segment independently picks the nearest rect.
  #     A split connection is left DISCONNECTED externally — the block's
  #     internal routing is assumed to join the two sides.
  #     No bridge segment is generated.
  #
  #   over-the-block:
  #     When a trunk position falls in the gap between two rects, BOTH rects
  #     are connected by stubs AND an explicit bridge segment is generated
  #     over the outer boundary of the block to physically join them.
  #     The bridge runs along the top (H trunk gap) or side (V trunk gap)
  #     of the block's union bounding box, just beyond the outermost face.
  #
  # Diagram notation:
  #   [R1] [R2]  two rects of a multi-rect block
  #   ===        horizontal trunk or bridge
  #   ||         vertical trunk or stub
  #   x          busterm: segment endpoint on a block face
  #   +-·-+      bridge segment (over-the-block, explicit connection)
  #   ~~~        NUTS slide range zone
  #   ·          gap between rects (the notch)
  #
  # Coordinates: x increases right, y increases upward.

  # ---------------------------------------------------------------------------
  # Baseline geometry used by multiple scenarios:
  #
  #   y=400  +------+      y=400  +-·-·-+      y=400  +-·-·-+
  #          |      |             :     :             :     :
  #   y=300  |  B   |      y=300  x  B  x──────────── (bridge)
  #   y=300  |      |             :     :
  #   y=200  · gap  ·      y=200  ·     ·   ← trunk at y=200
  #   y=200  |      |
  #   y=100  |  B   |      y=100  x  B  x
  #          |      |             :     :
  #   y=  0  +------+      y=  0  +-·-·-+
  #
  #   left: thru-the-block        right: over-the-block
  #
  # Block B (multi-rect, vertically split):
  #   Rect R1 (bottom): (200, 0)–(300,100)
  #   Rect R2 (top):    (200,300)–(300,400)
  #   Gap (notch):      y=100 to y=300
  #
  # Block A (source): (0,150)–(100,250)
  # Trunk H at y=200 (midpoint of gap, between R1.y2=100 and R2.y1=300)
  # ---------------------------------------------------------------------------

  Scenario: Thru-the-block (default) — trunk in gap connects to nearest rect only
    # Trunk at y=200 falls in the gap between R1 (y2=100) and R2 (y1=300).
    # Distances: R1 is 100 units away (200−100), R2 is 100 units away (300−200).
    # Tie → connect to R1 (lower rect; first in definition order).
    # One V stub: from B.R1 top face (y=100) up to trunk at y=200. Length=100.
    # No bridge segment. No connection to R2.
    #
    #   y=400  +------+
    #          |  R2  |   ← not connected (thru-the-block assumption)
    #   y=300  +------+
    #                      ·  gap  ·
    #   y=200  ===x=====*  ← trunk at y=200; stub goes DOWN to R1 top face
    #          (A)     ||
    #   y=100  +--x----+  ← R1 top face; stub from y=100 to y=200 (length=100)
    #          |  R1  |
    #   y=  0  +------+
    #
    Given a block "A" at (0,150)-(100,250)
    And a block "B" with rects (200,0)-(300,100) and (200,300)-(300,400) and teg_mode "thru"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then a candidate of type "TRUNK_H@y200" exists
    And in the TRUNK_H@y200 candidate "B" has exactly 1 V stub connecting to its lower rect
    And the V stub from "B" in the TRUNK_H@y200 candidate has length 100
    And the TRUNK_H@y200 candidate has no bridge segment for "B"
    And "B"'s upper rect is not connected in the TRUNK_H@y200 candidate

  Scenario: Thru-the-block — trunk inside a rect needs no stub (Direct connection)
    # Trunk at y=50 falls INSIDE R1 (y=0 to y=100). Direct connection.
    # No stub needed for B. No bridge. R2 is not connected.
    #
    #   y=400  +------+
    #          |  R2  |   ← not connected (Direct to R1, not in gap)
    #   y=300  +------+
    #   y=100  +------+
    #   y= 50  x  R1  x──── (H trunk; B is Direct via R1, no stub)
    #   y=  0  +------+
    #
    Given a block "A" at (0,0)-(100,100)
    And a block "B" with rects (200,0)-(300,100) and (200,300)-(300,400) and teg_mode "thru"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then in the TRUNK_H@y50 candidate "B" has no V stub (Direct connection via lower rect)
    And "B"'s upper rect is not connected in the TRUNK_H@y50 candidate

  Scenario: Over-the-block — trunk in gap generates bridge over block
    # Same geometry. teg_mode = over.
    # Trunk at y=200 → gap → two stubs:
    #   V stub down to R1 top face (y=100): length=100
    #   V stub up   to R2 bot face (y=300): length=100
    # Bridge: H segment at y=400 (R2.y2, outer face of B's union bbox)
    #   from B.R1.cx=250 to B.R2.cx=250 (both same cx here; 0-length bridge possible)
    # OR bridge runs at y=400 (top of R2) connecting x=B.x1 to x=B.x2.
    # (for same-cx rects the bridge degenerates; scenario uses offset rects below)
    #
    # NOTE: In this geometry R1 and R2 have the same x range → bridge is degenerate.
    # Use offset geometry: R1=(200,0)-(280,100), R2=(220,300)-(300,400).
    # Bridge at y=400 from x=200 (R1.x1) to x=300 (R2.x2).
    #
    #   y=400  +-·-·-·-·-+
    #          :   R2    :   ← bridge at y=400 connects R1 and R2
    #   y=300  x·········x
    #                ||       ← V stub up: y=200 to y=300 (length=100)
    #   y=200  ===x===*        ← trunk at y=200
    #          (A)  ||         ← V stub down: y=200 to y=100 (length=100)
    #   y=100  x·········x
    #          :   R1    :
    #   y=  0  +-·-·-·-·-+
    #
    Given a block "A" at (0,150)-(100,250)
    And a block "B" with rects (200,0)-(280,100) and (220,300)-(300,400) and teg_mode "over"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then a candidate of type "TRUNK_H@y200" exists
    And in the TRUNK_H@y200 candidate "B" has 2 V stubs (one to each rect)
    And the V stub down from "B" in TRUNK_H@y200 has length 100
    And the V stub up   from "B" in TRUNK_H@y200 has length 100
    And the TRUNK_H@y200 candidate has a bridge segment for "B" at y=400
    And the bridge segment spans from B's leftmost rect face to B's rightmost rect face

  Scenario: Over-the-block — trunk inside one rect → no bridge needed
    # Trunk at y=50 is INSIDE R1 (y=0 to y=100).
    # Direct connection to R1. R2 not reachable by this trunk without a stub.
    # Even in over-the-block mode: Direct + no stub + no bridge.
    # Over-the-block only activates when the trunk is in the GAP, not inside a rect.
    #
    Given a block "A" at (0,0)-(100,100)
    And a block "B" with rects (200,0)-(280,100) and (220,300)-(300,400) and teg_mode "over"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then in the TRUNK_H@y50 candidate "B" has no bridge segment
    And "B" has a Direct connection in the TRUNK_H@y50 candidate

  Scenario: Over-the-block — L-shaped block with H trunk above notch
    # L-block: tall arm (0,0)-(100,400) + wide base (0,0)-(400,100).
    # Notch: x=100–400, y=100–400.
    # src at (500,150)-(600,250).
    #
    # Hanan x-grid from individual rects: 0, 100, 400, 500, 600.
    # In-bbox V trunk candidates (midpoints of each channel):
    #   (0+100)/2=50, (100+400)/2=250, (400+500)/2=450, (500+600)/2=550.
    #
    # V trunk at x=250 falls inside the wide base's x-range [0,400] but NOT
    # inside the tall arm's x-range [0,100].
    # → wide base: Direct (no H stub); trunk connects at y=100 (wide-base top face).
    # → tall arm: x=250 is outside [0,100] → arm is NOT reachable by a direct stub.
    # Over-the-block mode: emit a bridge H segment at y=400 (union bbox top) to
    # signal that the tall arm must be connected externally over the block.
    #
    #   y=400 ─ +──────────────────────+ ← bridge H at y=400 (union bbox top)
    #           | tall arm |            :
    #   y=100 ─ +──────────────────────x ← wide-base top; V trunk direct connection
    #   y=  0 ─ +──────────────────────+
    #           x=0          x=250  x=400
    #
    Given a block "src" at (500,150)-(600,250)
    And a block "L" with rects (0,0)-(100,400) and (0,0)-(400,100) and teg_mode "over"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["L"] using layers M4,M5
    Then the TRUNK_V@x250 candidate has a bridge segment for "L"
    And the bridge segment runs along the top face of "L"'s union bounding box

  Scenario: Over-the-block — bridge is omitted when rects are adjacent (no gap)
    # When two rects share an edge (no gap between them), thru-the-block and
    # over-the-block produce identical results — no bridge is needed because
    # the two rects are already physically contiguous.
    #
    # B: rects (200,0)-(300,100) and (200,100)-(300,200) — touching at y=100.
    # No gap. Trunk at y=150 (inside upper rect) → Direct to upper rect.
    # Even with teg_mode=over: no bridge.
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "B" with rects (200,0)-(300,100) and (200,100)-(300,200) and teg_mode "over"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then the TRUNK_H@y150 candidate has no bridge segment for "B"

  Scenario: Global teg_mode overridden per block
    # Global teg_mode = thru; block "B" has per-block teg_mode = over.
    # Block "C" has no override → inherits global = thru.
    # Two blocks, same trunk; only B generates a bridge.
    #
    Given a block "A" at (0,150)-(100,250)
    And a block "B" with rects (200,0)-(280,100) and (220,300)-(300,400) and teg_mode "over"
    And a block "C" with rects (400,0)-(480,100) and (420,300)-(500,400) and teg_mode "thru"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","C"] using layers M4,M5
    Then in TRUNK_H@y200 "B" has a bridge segment and "C" does not

  Scenario: Over-the-block bridge topology has higher adjusted wirelength than thru
    # The bridge segment adds explicit wirelength. With the same underlying trunk,
    # the over-the-block topology costs more than the thru-the-block one.
    # The topology sorter must rank thru-the-block before over-the-block
    # (lower wirelength wins, all else equal).
    #
    Given a block "A" at (0,150)-(100,250)
    And a block "B" with rects (200,0)-(280,100) and (220,300)-(300,400)
    And both "thru" and "over" teg_mode candidates are generated for "B"
    When I rank the TRUNK_H@y200 candidates by adjusted wirelength
    Then the thru-the-block candidate ranks before the over-the-block candidate
