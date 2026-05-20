Feature: Feedthru-enabled trunk generation
  As a chip planner
  I want to mark large relay blocks as feedthru-capable
  So that a trunk can pass through them without generating a physical stub,
  relying on the block's internal routing to complete the bus connection later.

  # Diagram notation used throughout this file:
  #
  #   +----+  block boundary  (+= corner,  - top/bottom,  | sides)
  #   | Nm |  block name
  #   +----+
  #
  #   x      busterm connection (bus segment meets block face)
  #   *      internal bend / T-junction
  #   ===    horizontal bus segment
  #   ||     vertical bus segment (double bar = multi-bit bus)
  #   ~~~    slide range zone (segment may be placed anywhere in this zone)
  #
  #   +-·-·+  dashed outline = feedthru block (FT)
  #   :    :
  #   +-·-·+
  #
  # Coordinates: x increases right, y increases upward.
  # All measurements in abstract layout units.

  # ---------------------------------------------------------------------------
  # Baseline geometry used by most scenarios:
  #
  #   Block A  (0,100)-(100,200)     Block B  (350,100)-(450,200)
  #   FT block (150,  0)-(300,300)   trunk H  at y=150  (passes through FT)
  #
  #   y=200 ─  +------+                              +------+
  #            |      |                              |      |
  #   y=150 ─  |  A   x==============================x  B   |
  #            |      |   +-·-·-·-·-·-·-·-+          |      |
  #   y=100 ─  +------+   :               :          +------+
  #                       :      FT       :
  #   y=  0 ─             +-·-·-·-·-·-·-·-+
  #
  # The trunk at y=150 falls inside FT's y range [0,300] — FT straddles it.
  # ---------------------------------------------------------------------------

  Scenario: Trunk passes through a feedthru-enabled block without a stub
    # FT straddles the trunk (y=150 ∈ [0,300]) and feedthru is enabled.
    # The trunk crosses FT's faces at x=150 and x=300 but emits NO V stub.
    # Two disconnected H segments are recorded; FT appears in feedthru_blocks.
    #
    #          +-·-·-·-·-·+
    #   +--+   :          :   +--+
    #   |  |   :          :   |  |
    #   |A x===x    FT    x===x B|
    #   |  |   :          :   |  |
    #   +--+   +-·-·-·-·-·+   +--+
    #            (no stub)
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "FT" at (150,0)-(300,300)
    And a block "B" at (350,100)-(450,200)
    And feedthru is enabled for block "FT"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then a candidate of type "TRUNK_H@y150" exists
    And "FT" appears in that topology's feedthru_blocks list
    And no V stub segment connects to block "FT" in that topology
    And the trunk H segment is split at x=150 and x=300

  Scenario: Trunk generates a stub when feedthru is disabled (default)
    # Same geometry but feedthru NOT enabled for FT.
    # FT straddles the trunk → pass_through_count is incremented,
    # but a V stub must be generated (from y=150 up to FT.y2=300 is too tall;
    # actually FT.face_y(150)=150 so has_stub=false → pass-through).
    # The trunk is continuous (not split) and FT gets pass_through_count += 1.
    #
    #          +----------+
    #          |          |
    #          |    FT    |
    #          |          |
    #   +--+   |          |   +--+
    #   |  |   |          |   |  |
    #   |A x===x====·=====x===x B|   ← single unbroken trunk
    #   |  |   |  (no FT  |   |  |
    #   +--+   |   stub)  |   +--+
    #          +----------+
    #              ^
    #        pass_through_count = 1; feedthru_blocks is empty
    #
    Given a block "A" at (0,100)-(100,200)
    And a block "FT" at (150,0)-(300,300)
    And a block "B" at (350,100)-(450,200)
    And feedthru is NOT enabled (default)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then a candidate of type "TRUNK_H@y150" exists
    And that topology's feedthru_blocks list is empty
    And that topology's pass_through_count equals 1
    And the trunk H segment is continuous (not split)

  Scenario: Per-block feedthru overrides global disabled setting
    # Global feedthru = false, but FT block has per-block feedthru = true.
    # Result: FT is treated as feedthru; other blocks still get stubs.
    #
    #   Global feedthru: OFF
    #   Block "FT":  feedthru ON  (per-block override)
    #   Block "mid": feedthru OFF (inherits global)
    #
    #          +-·-·-·-·-·+   +--------+
    #   +--+   :          :   |        |   +--+
    #   |A x===x    FT    x===x  mid   |   |  |
    #   |  |   :  (split) :   |        x===x B|
    #   +--+   +-·-·-·-·-·+   +----x---+   +--+
    #                              ||
    #                     =========*=========    ← trunk
    #
    Given a block "A"   at (0,100)-(100,200)
    And a block "FT"  at (150,0)-(300,300)
    And a block "mid" at (320,50)-(430,180)
    And a block "B"   at (480,100)-(580,200)
    And global feedthru is disabled
    And feedthru is enabled for block "FT" only
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "A" to ["B","mid"] using layers M4,M5
    Then the TRUNK_H topology has "FT" in feedthru_blocks
    And the TRUNK_H topology has no stub for "FT"
    And the TRUNK_H topology has a V stub for "mid"

  Scenario: Per-layer feedthru overrides global disabled setting
    # On layer M4 (H), feedthru is enabled; on M6 (H), it is not.
    # Two trunk-H candidates are generated (one per H layer).
    # Only the M4 candidate treats FT as feedthru.
    Given a block "A"  at (0,100)-(100,200)
    And a block "FT" at (150,0)-(300,300)
    And a block "B"  at (350,100)-(450,200)
    And global feedthru is disabled
    And feedthru is enabled for layer M4
    And layer M4 is HORIZONTAL with id 4
    And layer M6 is HORIZONTAL with id 6
    And layer M5 is VERTICAL  with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    And I generate multicast candidates from "A" to ["B"] using layers M6,M5
    Then the M4 TRUNK_H topology has "FT" in feedthru_blocks
    And the M6 TRUNK_H topology has "FT" NOT in feedthru_blocks

  Scenario: Feedthru block crossing recorded in Topology.feedthru_blocks
    # feedthru_blocks must contain exactly the blocks declared feedthru-capable
    # whose bbox straddles the trunk.  Non-straddling feedthru blocks are excluded.
    #
    #  y=300 ─     +-·-·-+         +------+
    #              :     :         | near |    ← straddles trunk at y=150
    #  y=150 ─ ====x FT  x=========x      x==    ← trunk
    #              :     :         +------+
    #  y=  0 ─     +-·-·-+
    #
    #  faraway  (y=500-600)  → feedthru ON but does NOT straddle → excluded
    #
    Given a block "A"       at (0,100)-(80,200)
    And a block "FT"      at (100,0)-(250,300)
    And a block "near"    at (260,80)-(380,220)
    And a block "faraway" at (400,500)-(500,600)
    And a block "B"       at (420,100)-(520,200)
    And feedthru is enabled for blocks "FT", "near", "faraway"
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL  with id 5
    When I generate multicast candidates from "A" to ["B"] using layers M4,M5
    Then the TRUNK_H@y150 topology's feedthru_blocks equals ["FT", "near"]
    And "faraway" is not in feedthru_blocks

  Scenario: Feedthru topology ranked lower than equivalent stub topology
    # When both a feedthru-based and a stub-based topology are feasible, the
    # stub topology should rank first (lower adjusted_wl) because feedthru adds
    # implicit internal-routing cost modelled by feedthru_penalty.
    #
    # Two competing TRUNK_H topologies at y=150:
    #   (a) FT is feedthru → trunk is shorter (fewer stubs) but incurs penalty
    #   (b) FT gets a normal stub → trunk is longer but no penalty
    #
    # With feedthru_penalty = 1.5×:
    #   adjusted_wl(feedthru) = nominal_wl × 1.5  >  adjusted_wl(stub)
    #
    Given a block "A"  at (0,100)-(100,200)
    And a block "FT" at (150,0)-(300,300)
    And a block "B"  at (350,100)-(450,200)
    And feedthru is enabled for block "FT"
    And feedthru_penalty is 1.5
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL  with id 5
    When I generate and sort candidates from "A" to ["B"] using layers M4,M5
    Then the first-ranked TRUNK_H topology does not have "FT" in feedthru_blocks
    And the feedthru TRUNK_H topology ranks below the stub TRUNK_H topology
