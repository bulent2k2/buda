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
