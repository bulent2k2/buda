@landed
Feature: Connector shape selection rules
  As a chip planner
  I want the topology generator to automatically choose the correct connector shape
  (Direct / L / Z / U) based on the relationship between a leaf block's face position
  and the trunk's perp slide range
  So that each leaf is reached by the shortest feasible path given the trunk's
  actual placement freedom.

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
  #   ~~~    perp slide-range boundary line
  #
  # Trunk model:
  #   A trunk is NOT a fixed line — it has a perp slide range [perp_lo, perp_hi]
  #   derived from the Hanan cell containing the trunk's nominal position.
  #
  #   For an H trunk, the perp axis is y.  For a V trunk, the perp axis is x.
  #
  #   The connector shape from leaf B to an H trunk is determined by:
  #     Direct   B.face_y(y) = y  for some  y ∈ [perp_lo, perp_hi]
  #              ↔ B.y-range ∩ [perp_lo, perp_hi] ≠ ∅
  #     L        B.y-range ∩ [perp_lo, perp_hi] = ∅
  #              stub from B.face (nearest edge) to the nearest slide-range boundary
  #     Z        L-like but the along-axis position of B also requires a relay segment
  #     U        direct path blocked by an obstacle; detour required
  #
  #   Stub length for L (H trunk, leaf below range): perp_lo − B.y2
  #   Stub length for L (H trunk, leaf above range): B.y1 − perp_hi
  #
  # Coordinates: x increases right, y increases upward.

  # ---------------------------------------------------------------------------
  # Direct connector: leaf's y-range overlaps the trunk's perp slide range.
  # The trunk can be placed at a position that eliminates the stub entirely.
  # ---------------------------------------------------------------------------

  Scenario: Direct — leaf y-range fully inside trunk perp slide range
    # src at (0,200)-(100,400) creates Hanan y at {200, 400}.
    # B   at (300,250)-(400,350) adds Hanan y at {250, 350}.
    # All Hanan y: {200, 250, 350, 400}.
    # H trunk at nominal y=300 → Hanan cell [250, 350] → slide range [250, 350].
    # B.y = [250, 350].  B.y-range ∩ [250, 350] = [250, 350] ≠ ∅  →  Direct.
    # At any trunk y ∈ [250, 350], B.face_y(y) = y (straddles) → no stub.
    #
    #   ~~~y=350~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  perp_hi
    #   y=350 ─  +------+         +------+
    #            |      |         |      |
    #   y=300 ─  | src  |         |  B   |   ← trunk can be placed anywhere in ~~~
    #            |      |         |      |      B straddles for any such y
    #   y=250 ─  |      |         +------+
    #            |      |
    #   ~~~y=250~~~~~~~  perp_lo
    #            |      |
    #   y=200 ─  +------+
    #              0  100           300 400
    #
    Given a block "src" at (0,200)-(100,400)
    And a block "B"   at (300,250)-(400,350)
    And a horizontal trunk with perp slide range y ∈ [250, 350]
    When I compute the connector from "B" to the trunk
    Then the connector type is "Direct"
    And no stub segment is emitted for "B"

  Scenario: Direct — leaf y-range partially overlaps trunk perp slide range
    # src at (0,200)-(100,400): Hanan y {200, 400}.
    # B   at (300,350)-(400,550): adds Hanan y {350, 550}.
    # All Hanan y: {200, 350, 400, 550}.
    # H trunk at nominal y=300 → Hanan cell [200, 350] → slide range [200, 350].
    # B.y = [350, 550].  B.y-range ∩ [200, 350] = {350}  (single point at boundary).
    # At trunk y=350 (=perp_hi), B.face_y(350) = B.y1=350 → no stub.
    # Connector is Direct because ∃ a valid trunk position with zero stub.
    #
    #   y=550 ─                    +------+
    #                              |      |
    #   y=350 ─  +------+    ──────x  B   |  ← B bottom face at perp_hi=350; Direct here
    #   ~~~y=350~perp_hi~~~~~~~~~~~|      |    trunk placed at its maximum y
    #            |      |          |      |
    #   y=300 ─  | src  |     ← trunk range extends down to perp_lo=200
    #            |      |
    #   ~~~y=200~perp_lo~~~
    #   y=200 ─  +------+
    #              0  100            300 400
    #
    Given a block "src" at (0,200)-(100,400)
    And a block "B"   at (300,350)-(400,550)
    And a horizontal trunk with perp slide range y ∈ [200, 350]
    When I compute the connector from "B" to the trunk
    Then the connector type is "Direct"
    And no stub segment is emitted for "B"

  Scenario: Direct — V trunk; leaf x-range overlaps trunk perp slide range
    # src at (150,300)-(250,400): Hanan x {150, 250}.
    # B   at (150,100)-(250,200): adds Hanan x {150, 250}.
    # V trunk at nominal x=200 → Hanan cell [150, 250] → perp (x) slide range [150, 250].
    # B.x = [150, 250].  B.x-range ∩ [150, 250] = [150, 250] ≠ ∅  →  Direct.
    #
    #   y=400 ─  +------+
    #            |      |
    #   y=350 ─  | src  |
    #            |      |
    #   y=300 ─  +------+
    #            ~~||~~    ← V trunk; perp (x) slide range [150, 250]
    #   y=200 ─  +--x---+  ← B top face; busterm at x=200; Direct
    #            |      |
    #   y=150 ─  |  B   |
    #            |      |
    #   y=100 ─  +------+
    #           150 250
    #
    Given a block "src" at (150,300)-(250,400)
    And a block "B"   at (150,100)-(250,200)
    And a vertical trunk with perp slide range x ∈ [150, 250]
    When I compute the connector from "B" to the trunk
    Then the connector type is "Direct"
    And no stub segment is emitted for "B"

  # ---------------------------------------------------------------------------
  # L connector: leaf's y-range does NOT overlap the trunk's perp slide range.
  # A stub segment bridges the gap from leaf face to the nearest range boundary.
  # ---------------------------------------------------------------------------

  Scenario: L connector — leaf entirely below trunk perp slide range
    # src at (0,200)-(100,400): Hanan y {200, 400}.
    # B   at (300,50)-(400,150): adds Hanan y {50, 150}.
    # All Hanan y: {50, 150, 200, 400}.
    # H trunk at nominal y=300 → Hanan cell [200, 400] → slide range [200, 400].
    # B.y = [50, 150].  B.y-range ∩ [200, 400] = ∅  →  L connector.
    # Trunk placed at perp_lo=200 minimises stub: B.face_y(200)=B.y2=150.
    # Stub from B top face (y=150) up to perp_lo (y=200). Length = 50.
    #
    #   ~~~y=400~perp_hi~~~~~~~~~~~~~~~~~~~   top of slide range
    #   y=400 ─  +------+
    #            |      |
    #   y=300 ─  | src  |    (trunk can be anywhere in [200,400])
    #            |      |
    #   y=200 ─  +------+
    #   ~~~y=200~perp_lo~~~~~~~~~~~~~~~~~~~   bottom of slide range
    #                                         gap (stub needed here)
    #   y=150 ─              +--x---+   ← B top face; busterm at B.cx=350
    #                        |      |      stub length = perp_lo − B.y2 = 200−150 = 50
    #   y= 50 ─              +------+
    #                        300  400
    #
    Given a block "src" at (0,200)-(100,400)
    And a block "B"   at (300,50)-(400,150)
    And a horizontal trunk with perp slide range y ∈ [200, 400]
    When I compute the connector from "B" to the trunk
    Then the connector type is "L"
    And the stub connects B top face at y=150 to perp_lo at y=200
    And the stub length is 50

  Scenario: L connector — leaf entirely above trunk perp slide range
    # src at (0,200)-(100,400) and anchor at (0,400)-(100,600):
    # Hanan y {200, 400, 600}.
    # B   at (300,500)-(400,600): adds Hanan y {500, 600}.
    # All Hanan y: {200, 400, 500, 600}.
    # H trunk at nominal y=300 → Hanan cell [200, 400] → slide range [200, 400].
    # B.y = [500, 600].  B.y-range ∩ [200, 400] = ∅  →  L connector.
    # Trunk placed at perp_hi=400 minimises stub: B.face_y(400) = B.y1=500.
    # Stub from B bottom face (y=500) down to perp_hi (y=400). Length = 100.
    #
    #   y=600 ─              +------+
    #                        |      |
    #   y=500 ─              +--x---+   ← B bottom face; busterm at B.cx=350
    #                           ||      stub length = B.y1 − perp_hi = 500−400 = 100
    #   ~~~y=400~perp_hi~~~~~~~~~~~~~~~~~~~
    #   y=400 ─  +------+
    #            |      |
    #   y=300 ─  | src  |    (trunk can be anywhere in [200,400])
    #            |      |
    #   y=200 ─  +------+
    #   ~~~y=200~perp_lo~~~~~~~~~~~~~~~~~~~
    #              0  100         300 400
    #
    Given a block "src"    at (0,200)-(100,400)
    And a block "anchor" at (0,400)-(100,600)
    And a block "B"      at (300,500)-(400,600)
    And a horizontal trunk with perp slide range y ∈ [200, 400]
    When I compute the connector from "B" to the trunk
    Then the connector type is "L"
    And the stub connects B bottom face at y=500 to perp_hi at y=400
    And the stub length is 100

  Scenario: L connector (H stub) — leaf entirely left of V trunk perp slide range
    # V trunk with perp (x) slide range [300, 500].
    # B at (50,150)-(150,250). B.x = [50, 150].  [50,150] ∩ [300,500] = ∅  → L.
    # Trunk placed at perp_lo=300 minimises stub: B.face_x(300)=B.x2=150.
    # H stub from B right face (x=150) to perp_lo (x=300). Length = 150.
    #
    #                           ~~~x=300  ~~~x=500
    #                           perp_lo    perp_hi
    #   y=250 ─      +------+    ||    ||
    #                |      |    ||    ||   ← V trunk can be anywhere in x∈[300,500]
    #   y=200 ─      |  B   x====*    ||
    #                |      |    ||    ||
    #   y=150 ─      +------+    ||    ||
    #               50 150       300  500
    #
    Given a block "B"   at (50,150)-(150,250)
    And a vertical trunk with perp slide range x ∈ [300, 500]
    When I compute the connector from "B" to the trunk
    Then the connector type is "L"
    And the stub connects B right face at x=150 to perp_lo at x=300
    And the stub length is 150

  # ---------------------------------------------------------------------------
  # Partial overlap: same leaf generates Direct to one trunk, L to another.
  # The connector type changes based on which slide range the trunk belongs to.
  # ---------------------------------------------------------------------------

  Scenario: Same leaf — Direct to one trunk, L to a different trunk
    # B at (200,200)-(300,400). B.y = [200, 400].
    #
    # Trunk-1 slide range [100, 300]: [100,300] ∩ [200,400] = [200,300] ≠ ∅ → Direct.
    # Trunk-2 slide range [450, 650]: [450,650] ∩ [200,400] = ∅             → L.
    #   Stub from B top face (y=400) to Trunk-2 perp_lo (y=450). Length = 50.
    #
    #   y=650 ─  ~~~perp_hi of Trunk-2~~~~~~~~~~~~~~~~~~~
    #
    #   y=450 ─  ~~~perp_lo of Trunk-2~~~~~~~~~~~~~~~~~~~
    #                                    gap → L connector
    #   y=400 ─      +------+   ← B top face
    #                |      |
    #   y=300 ─      |  B   |
    #   ~~~y=300~perp_hi Trunk-1~~~~~~~~~~  Direct range ends here
    #   y=200 ─      +------+   ← B bottom face
    #   ~~~y=100~perp_lo Trunk-1~~~~~~~~~~
    #   y=100 ─
    #
    Given a block "B" at (200,200)-(300,400)
    And a horizontal trunk T1 with perp slide range y ∈ [100, 300]
    And a horizontal trunk T2 with perp slide range y ∈ [450, 650]
    When I compute the connector from "B" to trunk T1
    Then the connector type is "Direct"
    When I compute the connector from "B" to trunk T2
    Then the connector type is "L"
    And the stub length from "B" to T2 is 50

  Scenario: Partial y-overlap — connector type transitions across blocks
    # Three blocks at different y positions. Source "src" generates a trunk.
    # Trunk slide range [200, 350] from the Hanan grid.
    # B1 at y=[200,350]: fully inside range → Direct.
    # B2 at y=[350,500]: boundary case (perp_hi=350 = B2.y1) → Direct.
    # B3 at y=[400,550]: entirely above range (perp_hi=350 < B3.y1=400) → L; stub=50.
    #
    #   y=550 ─              +------+
    #                        |      |
    #   y=400 ─              +--x---+   B3 bottom face; L; stub=50 to perp_hi=350
    #   ~~~y=350~perp_hi~~~~~~~x  B3 ← but 350<400, so no overlap
    #   y=350 ─  +----+      +------+ +------+   B2.y1=350=perp_hi → Direct
    #            |    |      |  B2  | |      |
    #   y=300 ─  |src |      +------+ |  B1  |   B1 inside range → Direct
    #            |    |               |      |
    #   y=200 ─  +----+               +------+   B1.y1=200=perp_lo → Direct
    #   ~~~y=200~perp_lo~~~~~~~~~~~~~~~~~~~~~~~
    #
    Given a block "src" at (0,200)-(80,350)
    And a block "B1"  at (200,200)-(300,350)
    And a block "B2"  at (350,350)-(450,500)
    And a block "B3"  at (500,400)-(600,550)
    And a horizontal trunk with perp slide range y ∈ [200, 350]
    When I compute the connector from "B1" to the trunk
    Then the connector type is "Direct"
    When I compute the connector from "B2" to the trunk
    Then the connector type is "Direct"
    When I compute the connector from "B3" to the trunk
    Then the connector type is "L"
    And the stub length from "B3" to the trunk is 50

  # ---------------------------------------------------------------------------
  # Z connector: L-like in perp axis AND requires along-axis relay segment.
  # ---------------------------------------------------------------------------

  Scenario: Z connector — leaf outside perp range AND needs along-axis relay
    # Trunk (H) with perp slide range y ∈ [250, 400], along-axis extent x ∈ [0, 300].
    # B at (450,50)-(550,150). B.y=[50,150], entirely below perp_lo=250 → L in y.
    # B.cx=500 is outside the trunk's along-axis extent x=[0,300].
    # → relay H segment at the trunk level from x=300 to x=500 is needed → Z.
    #
    #   ~~~y=400~perp_hi~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    #   y=400 ─
    #
    #   y=300 ─  ===trunk body==========*===relay H===*    ← Z: relay at trunk level
    #                             0   300             ||  500
    #   ~~~y=250~perp_lo~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    #
    #   y=150 ─                                   +--x---+  ← B top face
    #                                             |      |
    #   y= 50 ─                                  +------+
    #                                            450   550
    #
    Given a block "src" at (0,250)-(100,400)
    And a block "B"   at (450,50)-(550,150)
    And a horizontal trunk with perp slide range y ∈ [250, 400] and along extent x ∈ [0, 300]
    When I compute the connector from "B" to the trunk
    Then the connector type is "Z" or "L"
    And the connector has a V stub from B top face toward the trunk level

  # ---------------------------------------------------------------------------
  # Shape selection: Direct is preferred when the slide range permits it.
  # L is preferred over Z when no along-axis relay is needed.
  # ---------------------------------------------------------------------------

  Scenario: Direct preferred when leaf y-range overlaps slide range
    # B.y-range ∩ trunk.perp_range ≠ ∅ → always use Direct, never L.
    #
    Given a block "B" at (200,100)-(300,200)
    And a horizontal trunk with perp slide range y ∈ [100, 200]
    When I compute the connector from "B" to the trunk
    Then the connector type is "Direct"

  Scenario: L preferred over Z when leaf along-axis position is within trunk extent
    # B is outside the perp range but within the trunk's along-axis extent.
    # No relay segment needed → L, not Z.
    #
    Given a block "B" at (200,50)-(300,150)
    And a horizontal trunk with perp slide range y ∈ [200, 350] and along extent x ∈ [0, 400]
    When I compute the connector from "B" to the trunk
    Then the connector type is "L"

  # ---------------------------------------------------------------------------
  # Corner-margin interaction: stub is measured from the shrunken bbox face.
  # ---------------------------------------------------------------------------

  Scenario: L connector stub length accounts for corner margin
    # B at (200,100)-(300,200) with corner_margin dy=10.
    # Shrunken B.y2 = 200 − 10 = 190.
    # Trunk perp slide range [250, 400] (entirely above B.y2=190).
    # Stub from shrunken B top face (y=190) up to perp_lo (y=250). Length = 60.
    # Without margin the stub would be from y=200 to y=250 → length 50.
    #
    #   ~~~y=400~perp_hi~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    #
    #   ~~~y=250~perp_lo~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    #                               gap: stub here
    #   y=200 ─ actual B.y2   +------+
    #                         |      |
    #   y=190 ─ shrunken face +--x---+   ← busterm on margin-adjusted top face
    #                         |  B   |      stub = perp_lo − 190 = 250 − 190 = 60
    #   y=100 ─               +------+
    #                        200   300
    #
    Given a block "B" at (200,100)-(300,200) with corner_margin dx=10 dy=10
    And a horizontal trunk with perp slide range y ∈ [250, 400]
    When I compute the connector from "B" to the trunk
    Then the connector type is "L"
    And the stub length is 60
