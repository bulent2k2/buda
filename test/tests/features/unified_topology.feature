@landed
Feature: Unified topology generation for two-busterm case
  As a chip planner
  I want the topology generator to enumerate I, L, Z, U, and UU shapes
  for every pair of source and destination blocks
  So that the bundle planner can choose the best route given congestion
  and physical constraints.

  # Diagram notation used throughout this file:
  #
  #   +----+  block boundary  (+= corner,  - top/bottom,  | sides)
  #   | Nm |  block name
  #   +----+
  #
  #   x      busterm: bus segment endpoint on a block face
  #              on top/bottom face → x replaces a - in the boundary line
  #              on left/right face → x replaces the | in the boundary line
  #   *      internal T-junction or bend (NOT on a block face)
  #   ===    horizontal bus segment
  #   ||     vertical bus segment
  #   ~~~    slide range zone (segment may be placed anywhere inside)
  #
  # Coordinates: x increases right, y increases upward.
  # All measurements in abstract layout units.

  # ---------------------------------------------------------------------------
  # I-shape: blocks share a horizontal or vertical alignment band.
  # A single segment — no bend.
  # ---------------------------------------------------------------------------

  Scenario: I_H shape — blocks share a horizontal alignment band
    # A and B share y-band [100,200]; the segment runs at y=A.cy=B.cy=150.
    # Busterms on A's right face and B's left face.
    #
    #   y=200 ─  +------+              +------+
    #            |      |              |      |
    #   y=150 ─  |  A   x==============x  B   |
    #            |      |              |      |
    #   y=100 ─  +------+              +------+
    #              0  100            300  400
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "B" at (300,100)-(400,200)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "I_H" exists
    And the I_H candidate has exactly 1 segment
    And that segment is horizontal at y=150 from x=100 to x=300

  Scenario: I_V shape — blocks share a vertical alignment band
    # A and B share x-band [50,150]; the segment runs at x=A.cx=B.cx=100.
    # Busterms on A's bottom face and B's top face.
    #
    #   y=500 ─  +------+
    #            |      |
    #   y=400 ─  |  A   |
    #            |      |
    #   y=300 ─  +--x---+   ← A bottom face; busterm at x=100
    #               ||
    #   y=200 ─  +--x---+   ← B top face; busterm at x=100
    #            |      |
    #   y=100 ─  |  B   |
    #            |      |
    #   y=  0 ─  +------+
    #            50 150
    #
    Given a block "A" at (50,300)-(150,500)
    And a block "B" at (50,0)-(150,200)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "I_V" exists
    And the I_V candidate has exactly 1 segment
    And that segment is vertical at x=100 from y=200 to y=300

  # ---------------------------------------------------------------------------
  # L-shape: blocks do NOT share an alignment band.
  # One bend: H first then V (L_HV), or V first then H (L_VH).
  # Bend is placed at (B.cx, A.cy) for L_HV, or (A.cx, B.cy) for L_VH.
  # The stub ends at the NEAREST block face, not the center.
  # ---------------------------------------------------------------------------

  Scenario: L_HV shape — horizontal segment first, then vertical
    # A at (0,100)-(100,200), B at (300,200)-(400,300).
    # A.cy=150, B.cx=350, B.y1=200 (nearest face from y=150 looking up).
    #
    # H stub: A.right(100,150) → bend(350,150).
    # V stub: bend(350,150) → B.bottom(350,200).   length = 50
    #
    #   y=300 ─                  +------+
    #                            |      |
    #   y=250 ─                  |  B   |
    #                            |      |
    #   y=200 ─  +------+        +--x---+   ← B bottom face; busterm at x=B.cx=350
    #            |      |           ||
    #   y=150 ─  |  A   x===========*        ← A right face; H stub; bend (350,150)
    #            |      |
    #   y=100 ─  +------+
    #              0  100             300 400
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "B" at (300,200)-(400,300)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "L_HV" exists
    And the L_HV candidate has exactly 2 segments
    And segment 0 is horizontal at y=180 from x=100 to x=300
    And segment 1 is vertical   at x=300 from y=180 to y=200

  Scenario: L_VH shape — vertical segment first, then horizontal
    # A at (0,100)-(100,200), B at (300,200)-(400,300).
    # A.cx=50, A.y2=200 (top face), B.cy=250, B.x1=300 (left face).
    #
    # V stub: A.top(50,200) → bend(50,250).         length = 50
    # H stub: bend(50,250) → B.left(300,250).       length = 250
    #
    #   y=300 ─                  +------+
    #                            |      |
    #   y=250 ─  *===============x  B   |   ← bend (50,250); H stub; B left face
    #            ||               |      |
    #   y=200 ─  +--x---+         +------+   ← A top face; busterm at x=A.cx=50
    #            |      |
    #   y=150 ─  |  A   |
    #            |      |
    #   y=100 ─  +------+
    #              0  100             300 400
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "B" at (300,200)-(400,300)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "L_VH" exists
    And the L_VH candidate has exactly 2 segments
    And segment 0 is vertical   at x=100 from y=200 to y=220
    And segment 1 is horizontal at y=220 from x=100 to x=300

  # ---------------------------------------------------------------------------
  # Z-shape: an intermediate trunk segment between the two blocks.
  # Three segments. One Z candidate per intermediate Hanan grid line.
  # Requires at least one intermediate Hanan line between the blocks —
  # a third block "mid" is added in these scenarios to create that line.
  # ---------------------------------------------------------------------------

  Scenario: Z_HVH shapes — horizontal stubs with a vertical trunk
    # A (left), B (right, offset in y). Block "mid" at x=170-230 provides
    # intermediate Hanan x lines at 170 and 230 between A.x2=100 and B.x1=300.
    #
    # Z_HVH at x=170 (shown below):
    #   H stub from A at y=A.cy=150: (100,150)→(170,150)
    #   V trunk at x=170:            (170,150)→(170,300)
    #   H stub to B at y=B.cy=300:   (170,300)→(300,300)
    #
    #         0  100     170         300 400
    #   y=350 ─                      +------+
    #                                |      |
    #   y=300 ─          *============x  B   |   ← T-junc (170,300); H stub; B.left
    #                    ||            |      |
    #   y=250 ─          ||            +------+
    #                    ||   (V trunk at x=170)
    #   y=200 ─  +------+ ||
    #            |       | ||
    #   y=150 ─  |  A   x====*          ← A.right(100,150); H stub; T-junc(170,150)
    #            |       |   ||
    #   y=100 ─  +-------+
    #              0  100  170         300 400
    #
    Given a block "A"   at (0,100)-(100,200)
    And a block "B"   at (300,250)-(400,350)
    And a block "mid" at (170,0)-(230,500)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then at least one candidate of type "Z_HVH" exists
    And every Z_HVH candidate has exactly 3 segments
    And every Z_HVH trunk segment is vertical
    And the Z_HVH trunk x positions include 200

  Scenario: Z_VHV shapes — vertical stubs with a horizontal trunk
    # A (bottom), B (upper-right, offset in x). Block "mid" at y=160-240 provides
    # intermediate Hanan y lines at 160 and 240 between A.y2=100 and B.y1=300.
    #
    # Z_VHV at y=200 (shown below):
    #   V stub from A at x=A.cx=100: (100,100)→(100,200)
    #   H trunk at y=200:            (100,200)→(250,200)
    #   V stub to B at x=B.cx=250:   (250,200)→(250,300)
    #
    #         50 150          200 300
    #   y=400 ─               +------+
    #                         |      |
    #   y=300 ─               +--x---+   ← B bottom face; busterm at x=B.cx=250
    #                            ||
    #   y=200 ─  *================*        ← T-junc (100,200); H trunk; T-junc (250,200)
    #            ||
    #   y=100 ─  +--x---+          ← A top face; busterm at x=A.cx=100
    #            |      |
    #   y=  0 ─  +------+
    #            50 150           200 300
    #
    Given a block "A"   at (50,0)-(150,100)
    And a block "B"   at (200,300)-(300,400)
    And a block "mid" at (0,160)-(400,240)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then at least one candidate of type "Z_VHV" exists
    And every Z_VHV candidate has exactly 3 segments
    And every Z_VHV trunk segment is horizontal
    And the Z_VHV trunk y positions include 200

  # ---------------------------------------------------------------------------
  # Partial y-overlap: both I and L (and Z) shapes coexist.
  # When two blocks partially share a y band, I shapes exist inside the overlap
  # zone and L shapes exist using bends outside the overlap zone.
  # ---------------------------------------------------------------------------

  Scenario: Partial y-overlap — I_H shapes at the two overlap-boundary y values
    # A at (0,100)-(100,300), B at (200,250)-(300,450).
    # Overlap zone: y ∈ [250, 300].
    # Hanan y lines: {100, 250, 300, 450}.
    #
    # I_H@y=250: A straddles (face_y=250), B.y1=250 (face_y=250) → direct, no stubs.
    # I_H@y=300: A.y2=300 (face_y=300), B straddles (face_y=300) → direct, no stubs.
    #
    #         0  100         200 300
    #   y=450 ─              +------+
    #                        |      |
    #   y=300 ─  +------+    |      |
    #            |      x====x  B   |   ← I_H@y300: A.y2 ↔ B.left (B straddles)
    #   y=250 ─  |  A   x====x      |   ← I_H@y250: A straddles ↔ B.y1
    #            |      |    +------+
    #   y=100 ─  +------+
    #              0  100    200 300
    #
    Given a block "A" at (0,100)-(100,300)
    And a block "B" at (200,250)-(300,450)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then at least one candidate of type "I_H" exists
    And every I_H candidate has no V stubs

  Scenario: Partial y-overlap — L shapes at bends outside the overlap zone
    # Same geometry: A at (0,100)-(100,300), B at (200,250)-(300,450).
    # A.cy=200 (below overlap), B.cy=350 (above overlap).
    #
    # L_HV: H from A.right(100,200) to bend(B.cx=250,200), then V up 50 to B.y1=250.
    # L_VH: V from A.top(50,300) up 50 to bend(50,B.cy=350), then H right to B.left(200,350).
    #
    #  L_HV (bend below overlap at y=200):
    #
    #   y=250 ─              +--x---+   ← B bottom face; busterm at B.cx=250
    #                           ||
    #   y=200 ─  +------+       *        ← bend at (250,200) = (B.cx, A.cy)
    #            |      x=======         ← A right face at y=A.cy=200
    #   y=100 ─  +------+
    #              0  100    200 300
    #
    #  L_VH (bend above overlap at y=350):
    #
    #   y=450 ─              +------+
    #                        |      |
    #   y=350 ─  *============x  B   |   ← bend at (50,350); H stub; B left face
    #            ||            |      |
    #   y=300 ─  +--x---+      +------+   ← A top face; busterm at A.cx=50
    #            |      |
    #   y=100 ─  +------+
    #              0  100    200 300
    #
    Given a block "A" at (0,100)-(100,300)
    And a block "B" at (200,250)-(300,450)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "L_HV" exists with H at y=230 and V stub length 20
    And a candidate of type "L_VH" exists with V at x=100 and H stub length 100
    And the L_HV bend is below the overlap zone (y=230 < 250)
    And the L_VH bend is above the overlap zone (y=320 > 300)

  Scenario: Partial y-overlap — Z topology via intermediate Hanan line from a third block
    # A at (0,100)-(100,300), B at (200,250)-(300,450).
    # Block "mid" at (150,0)-(160,500) adds Hanan x=150 between A.x2=100 and B.x1=200.
    #
    # Z_HVH at x=150:
    #   H stub from A at y=A.cy=200: (100,200)→(150,200)
    #   V trunk at x=150:            (150,200)→(150,350)  [passes through overlap zone]
    #   H stub to B at y=B.cy=350:   (150,350)→(200,350)
    #
    #         0  100 150    200 300
    #   y=450 ─              +------+
    #                        |      |
    #   y=350 ─       *=======x  B   |   ← T-junc (150,350); H stub; B.left(200,350)
    #                 ||       |      |
    #   y=300 ─  +------+      |      |
    #            |      |      |      |
    #   y=250 ─  |  A   |      +------+
    #            |      |
    #   y=200 ─  |      x======*        ← A.right(100,200); H stub; T-junc(150,200)
    #            |      |      ||       ← V trunk runs from (150,200) to (150,350)
    #   y=100 ─  +------+
    #              0  100  150  200 300
    #
    Given a block "A"   at (0,100)-(100,300)
    And a block "B"   at (200,250)-(300,450)
    And a block "mid" at (150,0)-(160,500)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "Z_HVH@x150" exists
    And the Z_HVH@x150 V trunk runs from y=250 to y=300
    And the Z_HVH@x150 V trunk passes through the overlap zone [250,300]
    And an I_H candidate also exists for the same block pair

  # ---------------------------------------------------------------------------
  # U-shape: detour outside the block bounding box.
  # Trunk is placed beyond the extreme Hanan grid lines (OOB).
  # Two orientations: U_HVH (V trunk outside), U_VHV (H trunk outside).
  # ---------------------------------------------------------------------------

  Scenario: U_HVH shape — vertical trunk placed outside the x bounding box
    # A and B have x-overlap so I_H is not generated; U_HVH detours around.
    # A=(0,100)-(200,200), B=(100,300)-(500,400). x-overlap=[100,200].
    # Left detour: V trunk at x = −50 (left of A.x1=0).
    # Right detour: V trunk at x = 550 (right of B.x2=500 > 400).
    #
    #   y=400 ─            +-----------------+
    #                      |                 |
    #   y=300 ─  *==========x      B         x==*   left/right trunks
    #            ||  100  300               500 ||
    #   y=200 ─  +----x---------+               ||
    #            |      A       |               ||
    #   y=100 ─  +---x----------+       ========*
    #
    Given a block "A" at (0,100)-(200,200)
    And a block "B" at (100,300)-(500,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "U_HVH" with trunk left  of x=0   exists
    And a candidate of type "U_HVH" with trunk right of x=400 exists
    And each U_HVH candidate has exactly 3 segments
    And each U_HVH candidate has busterms on the outer faces of A and B

  Scenario: U_VHV shape — horizontal trunk placed outside the y bounding box
    # A and B have similar x ranges but overlap in y.
    # Top detour: H trunk above both blocks (y = B.y2 + margin = 400 + 30 = 430).
    # Bottom detour: H trunk below both blocks (y = A.y1 − margin = 200 − 30 = 170).
    #
    # Top detour (trunk at y=430):
    #
    #   y=430 ─  *================================*   ← H trunk (OOB above both)
    #            ||                              ||
    #   y=400 ─  +--x---+                  +--x---+   ← busterms on top faces
    #            |      |                  |      |
    #   y=300 ─  |  A   |                  |  B   |
    #            |      |                  |      |
    #   y=200 ─  +------+                  +------+
    #              0  100                300  400
    #
    Given a block "A" at (0,200)-(100,400)
    And a block "B" at (300,200)-(400,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "U_VHV" with trunk above y=400 exists
    And a candidate of type "U_VHV" with trunk below y=200 exists
    And each U_VHV candidate has exactly 3 segments
    And each U_VHV candidate has busterms on the outer faces of A and B

  # ---------------------------------------------------------------------------
  # UU-shape: double detour — trunk segments on both sides of the bounding box.
  # ---------------------------------------------------------------------------

  Scenario: UU shape — two detour trunks in perpendicular directions
    # UU adds a second level of detour. Generated as fallback when single U is blocked.
    # Four segments; one trunk in each OOB direction.
    #
    #   y=430 ─  *================================*   ← top H trunk (OOB)
    #            ||                              ||
    #   y=400 ─  +--x---+                  +--x---+
    #            |      |                  |      |
    #   y=200 ─  +--x---+                  +--x---+
    #            ||                              ||
    #   y=170 ─  *================================*   ← bottom H trunk (OOB)
    #              0  100                300  400
    #
    Given a block "A" at (0,200)-(100,400)
    And a block "B" at (300,200)-(400,400)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then at least one candidate of type "UU" exists

  # ---------------------------------------------------------------------------
  # Topology completeness: all shape families generated for non-aligned blocks.
  # ---------------------------------------------------------------------------

  Scenario: Generator produces I, L, Z, and U candidates for non-aligned blocks
    # A and B are non-aligned (no shared y band) and non-adjacent (x gap > 0).
    # A third block "anchor" creates intermediate Hanan lines for Z shapes.
    # All five shape families must be present.
    #
    Given a block "A"      at (0,100)-(100,200)
    And a block "B"      at (300,300)-(400,400)
    And a block "anchor" at (180,50)-(220,450)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then at least one candidate of type "L_HV"  exists
    And at least one candidate of type "L_VH"  exists
    And at least one candidate of type "Z_HVH" exists
    And at least one candidate of type "Z_VHV" exists
    And at least one candidate of type "U_HVH" exists
    And at least one candidate of type "U_VHV" exists

  Scenario: Fully aligned blocks produce only I shape
    # When A and B share their entire y band, L and Z are strictly worse.
    # Only I_H is generated; L and Z are suppressed.
    #
    #   y=200 ─  +------+              +------+
    #            |      |              |      |
    #   y=150 ─  |  A   x==============x  B   |
    #            |      |              |      |
    #   y=100 ─  +------+              +------+
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "B" at (300,100)-(400,200)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate candidates from "A" to "B" using layers M4,M5
    Then a candidate of type "I_H" exists
    And the I_H candidate has exactly 1 segment
    And that segment is horizontal at y=150 from x=100 to x=300
