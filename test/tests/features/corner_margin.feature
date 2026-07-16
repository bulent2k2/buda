@doc
Feature: Block Corner Margin for Slide Ranges
  As a chip planner
  I want to keep trunk/stub connections away from block corners
  So that there is routing room around via locations

  # Corner margin is a per-block parameter.
  # - dx: absolute margin on horizontal faces (top/bottom), in the X direction.
  # - dy: absolute margin on vertical faces   (left/right), in the Y direction.
  # Percentage variants: pct_h computes dx = block_width  * pct_h / 100
  #                      pct_v computes dy = block_height * pct_v / 100
  # The margin shrinks the Pass-1 slide range inward from each end of the face.
  # Guard: if 2*margin >= face_extent, the margin is ignored (full extent used).

  Scenario: No margin — slide range is the full face extent
    Given a block "blk" at (0,0)-(100,200) with no corner margin
    And an H segment whose endpoint lies on the left face of "blk"
    When I compute the ConnTopology slide ranges
    Then the H segment's perp slide range is [0, 200]

  Scenario: Absolute dy margin — vertical face slide range shrinks symmetrically
    # Left/right face runs in Y; dy=20 shaves 20 off each end.
    Given a block "blk" at (0,0)-(100,200) with corner_margin dy=20
    And an H segment whose endpoint lies on the left face of "blk"
    When I compute the ConnTopology slide ranges
    Then the H segment's perp slide range is [20, 180]

  Scenario: Absolute dx margin — horizontal face slide range shrinks symmetrically
    # Top/bottom face runs in X; dx=15 shaves 15 off each end.
    Given a block "blk" at (0,0)-(200,100) with corner_margin dx=15
    And a V segment whose endpoint lies on the top face of "blk"
    When I compute the ConnTopology slide ranges
    Then the V segment's perp slide range is [15, 185]

  Scenario: Percentage pct_v margin — dy computed as fraction of block height
    # Block height = 200; pct_v=10 → dy = 20.
    Given a block "blk" at (0,0)-(100,200) with corner_margin pct_v=10
    And an H segment whose endpoint lies on the right face of "blk"
    When I compute the ConnTopology slide ranges
    Then the H segment's perp slide range is [20, 180]

  Scenario: Percentage pct_h margin — dx computed as fraction of block width
    # Block width = 200; pct_h=10 → dx = 20.
    Given a block "blk" at (0,0)-(200,100) with corner_margin pct_h=10
    And a V segment whose endpoint lies on the bottom face of "blk"
    When I compute the ConnTopology slide ranges
    Then the V segment's perp slide range is [20, 180]

  Scenario: Block too small — margin guard prevents inverted interval
    # Block height = 30; dy=20 → lo=20, hi=10 → inverted → fall back to full extent.
    Given a block "blk" at (0,0)-(100,30) with corner_margin dy=20
    And an H segment whose endpoint lies on the left face of "blk"
    When I compute the ConnTopology slide ranges
    Then the H segment's perp slide range is [0, 30]

  Scenario: Nominal at face boundary — margin not applied (topology-generator placement)
    # The topology generator places segment endpoints at the nearest block face.
    # When the endpoint lands exactly on the face boundary (e.g. y=y1 for a
    # below-to-above L), the margin range [y1+dy, y2-dy] excludes the nominal
    # position.  The guard falls back to the full face extent so NUTS can place
    # the segment at (or near) its nominal position without interval inversion.
    #
    # Block "src" at (200,400)-(300,600) with dy=20 → margin range [420,580].
    # H segment at nominal y=400 (= src.y1, a face boundary) → 400 < 420,
    # so margin is skipped → slide range = [400, 600].
    Given a block "src" at (200,400)-(300,600) with corner_margin dy=20
    And an H segment at nominal y=400 (the bottom face boundary of "src")
    When I compute the ConnTopology slide ranges
    Then the H segment's perp slide range is [400, 600]

  Scenario: Nominal at opposite face boundary — margin not applied
    # V segment at nominal x=300 (= src.x2, right face boundary) with dx=15
    # → margin range [215, 285] excludes 300 → guard → slide = [200, 300].
    Given a block "src" at (200,400)-(300,600) with corner_margin dx=15
    And a V segment at nominal x=300 (the right face boundary of "src")
    When I compute the ConnTopology slide ranges
    Then the V segment's perp slide range is [200, 300]

  Scenario: Specifying only dy mirrors to dx
    Given a block "blk" at (0,0)-(200,200) with corner_margin dy=25
    And an H segment whose endpoint lies on the left face of "blk"
    And a V segment whose endpoint lies on the top face of "blk"
    When I compute the ConnTopology slide ranges
    Then the H segment's perp slide range is [25, 175]
    And the V segment's perp slide range is [25, 175]

  Scenario: NUTS places trunk within margin-adjusted range
    # With dy=30 the slide range is [30, 170]; NUTS must place within this range.
    Given a block "blk" at (0,0)-(100,200) with corner_margin dy=30
    And an H segment at nominal y=100 connecting to the left face of "blk"
    When I run NUTS on this topology
    Then the placed track position is in [30, 170]

  Scenario: Global margin applies to blocks with no per-block override
    Given a global corner_margin dy=20
    And a block "a" at (0,0)-(100,200) with no corner margin
    And a block "b" at (0,0)-(100,200) with corner_margin dy=10
    And an H segment whose endpoint lies on the left face of "a"
    And an H segment whose endpoint lies on the left face of "b"
    When I compute the ConnTopology slide ranges
    Then the H segment for "a" has perp slide range [20, 180]
    And the H segment for "b" has perp slide range [10, 190]
