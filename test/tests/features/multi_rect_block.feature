@landed
# STATUS: landed (retagged 2026-08-22 — the @future tag and its "all scenarios
# xfail" header had gone stale: all 7 scenarios PASS, and had for some time, via
# the shipped `Floorplan.add_block_rects` Python API the step defs drive.  The
# CLI spelling of the same feature is
# `add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [teg_mode thru|over]`.)
# These scenarios pin best-fit rect selection, per-rect Hanan contributions and
# stub geometry; `teg_mode over` bridge emission (`Topology::bridge_segments`)
# is specced in busterm_over_the_block.feature.  A missing expected candidate
# is a FAILURE, not an xfail — see _cand_ct in test_multi_rect_block.py.
# Downstream-stage status (bridges are generation-only today) is appraised in
# docs/internal/teg_multirect_status.md.
Feature: Multi-rect blocks and equivalent busterms
  As a chip planner
  I want blocks to expose multiple candidate connection rectangles
  So that the topology generator connects to whichever rect minimises stub length,
  enabling correct handling of equivalent busterms and rectilinear block shapes.

  # Diagram notation:
  #   [R1] / [R2]  — the two rects of a multi-rect block
  #   ===          — horizontal trunk segment
  #   ||           — vertical trunk segment
  #   x            — busterm connection point
  #   *            — T-junction or bend
  #
  # All scenarios drive the shipped multi-rect API (Floorplan.add_block_rects).

  # ---------------------------------------------------------------------------
  # H trunk: pick the rect whose y-range straddles the trunk (Direct)
  # and the rect nearest the trunk when neither straddles (L-stub).
  # ---------------------------------------------------------------------------

  Scenario: H trunk uses the rect whose y-range contains the trunk — lower rect
    # dst has two rects stacked vertically.
    # A trunk at y=50 passes through the lower rect → Direct (no V stub).
    # A trunk at y=350 passes through the upper rect → Direct (no V stub).
    # A trunk at y=125 is between the two rects; lower rect is nearer → short stub.
    # A trunk at y=275 is between the two rects; upper rect is nearer → short stub.
    #
    #   y=400 ─  +-------+
    #            |  dst  |   ← upper rect (0,300)-(100,400)
    #   y=300 ─  +-------+
    #
    #   y=275 ─  . . . . . .   ← TRUNK_H@y275: upper rect nearer (stub=25)
    #
    #   y=125 ─  . . . . . .   ← TRUNK_H@y125: lower rect nearer (stub=25)
    #
    #   y=100 ─  +-------+
    #            |  dst  |   ← lower rect (0,0)-(100,100)
    #   y=  0 ─  +-------+
    #                           +-------+
    #   y=250 ─                 |  src  |  at (300,150)-(400,250)
    #   y=150 ─                 +-------+
    #
    Given a block "src" at (300,150)-(400,250)
    And a block "dst" with rects (0,0)-(100,100) and (0,300)-(100,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["dst"] using layers M4,M5
    Then "dst" has no V stub in the TRUNK_H@y50 candidate
    And  "dst" has no V stub in the TRUNK_H@y350 candidate
    And  the stub from "dst" in the TRUNK_H@y125 candidate has length 25
    And  the stub from "dst" in the TRUNK_H@y275 candidate has length 25

  # ---------------------------------------------------------------------------
  # V trunk: equivalent left and right ports; each Direct when trunk is nearby.
  # ---------------------------------------------------------------------------

  Scenario: V trunk uses the rect whose x-range contains the trunk — Direct connections
    # dst has a left port (200–300) and a right port (400–500).
    # V trunk at x=250 passes through the left port → Direct.
    # V trunk at x=450 passes through the right port → Direct.
    # V trunk at x=150 is left of both ports; left port is nearer → short H stub.
    #
    #   +----+  src (0,150)-(100,250)
    #   |    |
    #   |    x────*    V trunk @ x=150
    #   |    |    ║
    #   +----+    ║   x=250 ┊ x=450
    #             ║    ┊      ┊
    #             ║   +--+  +--+
    #             ║   |L |  |R |   dst rects
    #             ║   +--+  +--+
    #
    Given a block "src" at (0,150)-(100,250)
    And a block "dst" with rects (200,0)-(300,100) and (400,0)-(500,100)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["dst"] using layers M4,M5
    Then "dst" has no H stub in the TRUNK_V@x250 candidate
    And  "dst" has no H stub in the TRUNK_V@x450 candidate
    And  the H stub from "dst" in the TRUNK_V@x150 candidate has length 50

  # ---------------------------------------------------------------------------
  # Rectilinear block: L-shaped block uses the nearest face for each trunk.
  # ---------------------------------------------------------------------------

  Scenario: Trunk above an L-shaped block connects via the tall arm's top face
    # L-block: tall arm (0,0)-(100,400) + wide base (0,0)-(400,100).
    # An H trunk between the block top (y=400) and src bottom (y=450) at y=425
    # must use the tall arm's face (stub=25), not the wide base (stub=325).
    #
    #   y=550 ─  +---+
    #            |src|   at (200,450)-(300,550)
    #   y=450 ─  +---+
    #               ↕ 25
    #   y=425 ─  ===x===x===  ← TRUNK_H@y425; L uses rect1 face y=400 (stub=25)
    #               ↕ 25
    #   y=400 ─  +--+
    #            |  |         ← tall arm top face
    #            |L |
    #            |  |
    #   y=100 ─  +--+--+
    #            |   wide  |  ← wide base top face at y=100 (would give stub=325)
    #   y=  0 ─  +--------+
    #
    Given a block "src" at (200,450)-(300,550)
    And a block "L" with rects (0,0)-(100,400) and (0,0)-(400,100)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["L"] using layers M4,M5
    Then the stub from "L" in the TRUNK_H@y425 candidate has length 25

  Scenario: Trunk to the right of an L-shaped block connects via the wide arm's right face
    # Same L-block. A V trunk between the wide arm's right (x=400) and src.left (x=450)
    # at x=425 must use the wide arm's face (stub=25), not the tall arm (stub=325).
    #
    #                    x=425 ← TRUNK_V@x425
    #   +--+              ┊
    #   |  |         ┊    ║  +---+
    #   |L |rect1    ┊    ║  |src|  (450,150)-(550,250)
    #   |  |x2=100   ┊    ║  +---+
    #   +--+--+      ┊    ║
    #   |   wide|  ←stub=25→
    #   | rect2 x2=400    x=425
    #   +-------+
    #
    Given a block "src" at (450,150)-(550,250)
    And a block "L" with rects (0,0)-(100,400) and (0,0)-(400,100)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["L"] using layers M4,M5
    Then the H stub from "L" in the TRUNK_V@x425 candidate has length 25

  # ---------------------------------------------------------------------------
  # Hanan grid: all rect edges contribute, enabling trunk candidates only
  # reachable via a second rect's boundaries.
  # ---------------------------------------------------------------------------

  Scenario: Hanan grid includes the edges of all rects of a multi-rect block
    # src (0,150)-(100,250) and B with two rects: left rect (200,0)-(300,100) and
    # right rect (500,0)-(600,100).
    # The right rect's edges (x=500,x=600) add Hanan x-lines.
    # Without the right rect, no trunk candidate exists with x between 500 and 600.
    # With the right rect, TRUNK_V@x550 (midpoint of [500,600]) is generated.
    # src is at a different y-range to ensure a non-degenerate V trunk span.
    #
    #   y=250 ─  [src]
    #   y=150 ─  0─100        200─300          500─600
    #                         [rect1]          [rect2]
    #   y=100 ─               ───────          ───────
    #   y=  0 ─
    #                                             ║  ← TRUNK_V@x550; only reachable
    #                                             ║    if x=500/600 are Hanan lines
    #
    Given a block "src" at (0,150)-(100,250)
    And a block "B" with rects (200,0)-(300,100) and (500,0)-(600,100)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["B"] using layers M4,M5
    Then a candidate of type "TRUNK_V@x550" exists

  # ---------------------------------------------------------------------------
  # Equivalent busterms — both endpoints are multi-rect (teg1.buda pattern).
  # Each block independently selects its nearest rect; a trunk that falls
  # inside one of the dst's rects produces a Direct connection (no H stub).
  # ---------------------------------------------------------------------------

  Scenario: Both src and dst are multi-rect; dst is Direct when trunk falls inside one of its rects
    # src "A" has two vertically stacked rects (bt1-style in teg1.buda).
    # dst "B" has two horizontally separated rects (bt2-style in teg1.buda).
    # Trunks are placed at midpoints of adjacent Hanan x-intervals.
    # Hanan x from A+B: [0,100,200,400,500,700,900] → midpoints include 450 and 800.
    # x=450 is inside B.rect1 (400–500) → B is Direct (no H stub).
    # x=800 is inside B.rect2 (700–900) → B is Direct (no H stub).
    # x=300 is between A and B; B must reach via H stub to its nearest rect (rect1).
    #
    #   A.rect1  A.rect2          B.rect1   B.rect2
    #   0──100   0──200           400─500   700──900
    #   y=200    y=200            y=200──400 y=200──400
    #
    #   V trunk @x=300 ──────────────x           (B: H stub to rect1, length=100)
    #   V trunk @x=450 ─────────────────x        (B: Direct via rect1, no H stub)
    #   V trunk @x=800 ──────────────────────────x  (B: Direct via rect2, no H stub)
    #
    Given a block "A" with rects (0,200)-(100,400) and (0,600)-(200,800)
    And a block "B" with rects (400,200)-(500,400) and (700,200)-(900,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then "B" has no H stub in the TRUNK_V@x450 candidate
    And  "B" has no H stub in the TRUNK_V@x800 candidate
    And  the H stub from "B" in the TRUNK_V@x300 candidate has length 100

  # ---------------------------------------------------------------------------
  # Selected rect face — not the union bounding box — determines stub position.
  # ---------------------------------------------------------------------------

  Scenario: Stub length uses the selected rect's face, not the union bounding box
    # B has two widely separated rects: left (0,0)-(100,100) and right (500,0)-(600,100).
    # Union bbox: x=[0,600], cx=300.
    # For TRUNK_V@x50  (inside left rect):  B is Direct via left  rect (no H stub).
    # For TRUNK_V@x550 (inside right rect): B is Direct via right rect (no H stub).
    # If the union cx=300 were used, both trunks would compute a spurious H stub
    # of length 250 instead of recognising the Direct connection.
    #
    #   union bbox cx=300 ──────────────────────────────────────────
    #                  ↑ wrong stub if union is used
    #
    #   [B rect1]         src=(300,150)-(400,250)    [B rect2]
    #   0──100                 300──400              500──600
    #    ║                                             ║
    #   TRUNK_V@x50                            TRUNK_V@x550
    #   B: Direct (rect1)                      B: Direct (rect2)
    #   no H stub                              no H stub
    #
    Given a block "src" at (300,150)-(400,250)
    And a block "B" with rects (0,0)-(100,100) and (500,0)-(600,100)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "src" to ["B"] using layers M4,M5
    Then "B" has no H stub in the TRUNK_V@x50 candidate
    And  "B" has no H stub in the TRUNK_V@x550 candidate
