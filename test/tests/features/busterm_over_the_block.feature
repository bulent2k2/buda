@landed
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
  #     The block's rects are declared NOT internally connected, so every rect
  #     needs its own real external metal.  When a trunk falls in the gap
  #     between rects, EVERY rect gets a stub to the trunk (each T-junctions
  #     the spine), so the rects are joined THROUGH the trunk; when a trunk
  #     crosses a rectilinear block without spanning every rect, each
  #     un-spanned rect gets a perpendicular CONNECTOR LEG from the trunk to
  #     its nearest face.  All of it is ordinary topology segments, so the
  #     planner, NUTS, DetailedNUTS, the audits and report_wl all route and
  #     count it.  (Historical note: the connection metal used to be a
  #     "bridge segment" on the union bbox's outer face, kept OUTSIDE
  #     Topology.segments — it touched neither the trunk nor, for rectilinear
  #     blocks, the rect it existed to connect, and nothing downstream ever
  #     placed it; teg_multirect_status.md §1.1/§1.3.  Topology.bridge_segments
  #     survives only for candidates restored from pre-change checkpoints,
  #     where the TEG_OPEN audit reports the unrealized bridge.)
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

  Scenario: Over-the-block — trunk in gap stubs both rects, joined through the trunk
    # Same geometry. teg_mode = over.
    # Trunk at y=200 → gap → two stubs, each ENDING ON THE TRUNK:
    #   V stub down to R1 top face (y=100): length=100
    #   V stub up   to R2 bot face (y=300): length=100
    # OVER revokes the block's internal continuity, so each rect gets its own
    # real metal; the rects are JOINED THROUGH THE TRUNK (each stub T-junctions
    # the spine).  No bridge segment: the former union-face bridge lay on the
    # union bbox's outer face where it connected to no stub — floating metal
    # that nothing downstream ever placed (teg_multirect_status.md §1.3) — so
    # the connection metal is now the ordinary stubs, which ride the whole
    # pipeline (planner, NUTS, DetailedNUTS, audits, report_wl).
    #
    # Offset geometry: R1=(200,0)-(280,100), R2=(220,300)-(300,400).
    #
    #   y=400  +---------+
    #          :   R2    :
    #   y=300  x·········x
    #                ||       ← V stub up: y=200 to y=300 (length=100)
    #   y=200  ===x===*        ← trunk at y=200; both stubs junction here
    #          (A)  ||         ← V stub down: y=200 to y=100 (length=100)
    #   y=100  x·········x
    #          :   R1    :
    #   y=  0  +---------+
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
    And the TRUNK_H@y200 candidate has no bridge segment for "B"
    And both V stubs of "B" in the TRUNK_H@y200 candidate end on the trunk at y=200

  Scenario: Over-the-block — trunk inside one disjoint rect stubs the other rect
    # Trunk at y=50 is INSIDE R1 (y=0 to y=100): the spine crosses R1 (no
    # stub for it).  OVER revokes the block's internal continuity, so R2
    # still needs its own real metal (teg_multirect_status.md open 1
    # residual (i) — this shape used to emit NOTHING for R2, leaving the
    # routed result TEG_OPEN at the placed stages): a V stub from R2's
    # bottom face (y=300) down to the trunk at R2's along-centre x=260,
    # T-junctioning the spine — which the pre-pass extends through the
    # junction, so R1's contact becomes a pass-through crossing rather than
    # an endpoint face tap.  A rect ADJACENT to the landing rect stays
    # metal-free — a contiguous shape needs no connection metal (see the
    # adjacency scenario below); R2 here is 200 units away.
    #
    #   y=400  +---------+
    #          :   R2    :
    #   y=300  x·········x
    #               ||        ← V stub down: y=300 to y=50 (length 250)
    #   y= 50  ==x=R1=*==     ← trunk at y=50 crossing R1, extended to x=260
    #   y=  0  +------+
    #
    Given a block "A" at (0,0)-(100,100)
    And a block "B" with rects (200,0)-(280,100) and (220,300)-(300,400) and teg_mode "over"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then in the TRUNK_H@y50 candidate "B" has no bridge segment
    And in the TRUNK_H@y50 candidate "B" has exactly 1 V stub
    And the V stub up   from "B" in TRUNK_H@y50 has length 250

  Scenario: Over-the-block — L-shaped block with V trunk beside notch
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
    # → wide base: Direct (no H stub); the trunk crosses it at x=250.
    # → tall arm: x=250 is outside [0,100] → arm NOT spanned by the trunk.
    # Over-the-block mode (rectilinear): the arm needs its own real metal, so a
    # perpendicular H CONNECTOR LEG is emitted from the trunk to the arm's
    # right face at the arm's along-centre — it taps the arm at (100,200) and
    # T-junctions the spine at (250,200).  (The former spec put a "bridge" on
    # the union bbox's right face x=400, which touched neither the trunk nor
    # the arm it existed to connect — floating metal nothing downstream ever
    # placed; teg_multirect_status.md §1.3.  The leg is an ordinary segment
    # and rides the whole pipeline.)
    #
    #   y=400 ─ +──────────+
    #           | tall arm |
    #   y=200 ─ |     x────╫────║  ← H connector leg y=200, x=100→250 (trunk)
    #           |          |    ║  ← V trunk at x=250
    #   y=100 ─ +──────────+────╫─+ ← wide base crossed by the trunk (Direct)
    #   y=  0 ─ +───────────────╫─+
    #           x=0  x=100    x=250  x=400
    #
    Given a block "src" at (500,150)-(600,250)
    And a block "L" with rects (0,0)-(100,400) and (0,0)-(400,100) and teg_mode "over"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["L"] using layers M4,M5
    Then the TRUNK_V@x250 candidate has no bridge segment for "L"
    And the TRUNK_V@x250 candidate has an H connector leg tapping "L" from x=100 to the trunk at x=250 at y=200

  Scenario: Over-the-block — V trunk with horizontal gap (pure TEG, stubs joined through the trunk)
    # Pure TEG block B: two disjoint rects side-by-side (horizontal gap).
    # Right rect listed first so the topology generator picks it as the primary
    # connection point (right of trunk), keeping A (left of trunk) on the opposite
    # side — this prevents stub suppression and lets the TEG gap code activate.
    # Rleft  (left):  (100,0)-(200,400)
    # Rright (right): (300,0)-(500,400)
    # Horizontal gap: x=200 to x=300.
    # Source A at (0,150)-(50,250).
    #
    # V trunk at x=250 (midpoint of gap) falls between Rleft.x2=200 and Rright.x1=300.
    # → teg_mode=over: each rect gets its own H stub to the trunk (Rleft from its
    # right face x=200, Rright to its left face x=300, each at the rect's
    # along-centre y=200), and the rects are JOINED THROUGH THE TRUNK — both
    # stubs T-junction the spine at x=250.  No bridge segment: the former
    # union-face bridge at x=500 connected to no stub (floating metal nothing
    # downstream ever placed; teg_multirect_status.md §1.3).
    #
    #   y=400 +──────+     +──────────+
    #         |Rleft |  ║  |  Rright  |
    #   y=200 x──────x──╫──x──────────x   ← two H stubs meeting the trunk
    #                   ║
    #                 x=250 (V trunk)
    #   y=  0 +──────+     +──────────+
    #       x=100 x=200 x=250 x=300  x=500
    #
    Given a block "A" at (0,150)-(50,250)
    And a block "B" with rects (300,0)-(500,400) and (100,0)-(200,400) and teg_mode "over"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then the TRUNK_V@x250 candidate has no bridge segment for "B"
    And in the TRUNK_V@x250 candidate "B" has 2 H stubs (one to each rect)
    And both H stubs of "B" in the TRUNK_V@x250 candidate end on the trunk at x=250

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
    # Two blocks, same trunk in both gaps; only B (over) stubs BOTH its rects
    # (its rects need real external metal), while C (thru) stubs the nearest
    # rect only (its internal routing joins the sides by declaration).
    #
    Given a block "A" at (0,150)-(100,250)
    And a block "B" with rects (200,0)-(280,100) and (220,300)-(300,400) and teg_mode "over"
    And a block "C" with rects (400,0)-(480,100) and (420,300)-(500,400) and teg_mode "thru"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","C"] using layers M4,M5
    Then in the TRUNK_H@y200 candidate "B" has 2 V stubs (one to each rect)
    And in the TRUNK_H@y200 candidate "C" has exactly 1 V stub
    And the TRUNK_H@y200 candidate has no bridge segment for "B"

  Scenario: Over-the-block connection metal is real priced wirelength, so over ranks after thru
    # Post-emission (teg_multirect_status.md open 1(a)) there is no separate
    # "adjusted wirelength": the OVER connection metal is ordinary segments
    # priced in estimated_wirelength, so thru-vs-over ranking is plain WL
    # ranking of genuinely different metal.  teg_mode is a property of the
    # BLOCK, so one pool cannot hold both modes — the comparison is two
    # pools on the same geometry, and the asserted property is the
    # SAME-LOCUS TWINS': the over twin carries strictly more priced WL, and
    # the extra metal is real segments.  Measured: TRUNK_H@y200 is wl=200 /
    # 2 segments under thru (one stub to the nearest rect) and wl=360 /
    # 3 segments under over (both rects stubbed to the trunk).  Pools are
    # WL-sorted, so among otherwise-equal competitors the higher-WL twin
    # sorts later — that is what "thru ranks before over, all else equal"
    # means; a cross-pool ORDINAL comparison is deliberately NOT asserted,
    # because the two pools are different candidate populations (the other
    # OVER-affected members shift too) and the ordinal would be confounded
    # by them.
    #
    Given a block "A" at (0,150)-(100,250)
    When I generate candidate pools from "A" for block "B" with rects (200,0)-(280,100) and (220,300)-(300,400) under both teg modes
    Then the same-locus TRUNK_H@y200 candidate has strictly higher estimated wirelength under over than under thru
    And the over twin of TRUNK_H@y200 carries its extra wirelength as real segments
