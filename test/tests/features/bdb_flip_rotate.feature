@landed
Feature: flip_comp and rotate_comp transform component subtrees

  # ── Single-level: blk(120×90) at (50,50) containing leaf(45×50) at rel(10,15)
  # Absolute leaf before transform: (60,65)–(105,115)

  Scenario: flip_x mirrors children left-right about the vertical centre
    Given an empty in-memory BDB
    When I define cell "blk" with size 120 by 90
    And I define cell "leaf" with size 45 by 50
    And cell "blk" contains "l1" of "leaf" at (10, 15)
    And I place instance "u1" of cell "blk" under "-" at (50, 50)
    And I flip component "u1" in the x direction
    Then component "u1" spans from (50, 50) to (170, 140)
    And component "u1/l1" spans from (115, 65) to (160, 115)
    # new_x1 = 50+170−105 = 115,  new_x2 = 50+170−60 = 160

  Scenario: flip_y mirrors children up-down about the horizontal centre
    Given an empty in-memory BDB
    When I define cell "blk" with size 120 by 90
    And I define cell "leaf" with size 45 by 50
    And cell "blk" contains "l1" of "leaf" at (10, 15)
    And I place instance "u1" of cell "blk" under "-" at (50, 50)
    And I flip component "u1" in the y direction
    Then component "u1" spans from (50, 50) to (170, 140)
    And component "u1/l1" spans from (60, 75) to (105, 125)
    # new_y1 = 50+140−115 = 75,   new_y2 = 50+140−65  = 125

  Scenario: rotate_90 CCW swaps bbox dimensions and repositions children
    Given an empty in-memory BDB
    When I define cell "blk" with size 120 by 90
    And I define cell "leaf" with size 45 by 50
    And cell "blk" contains "l1" of "leaf" at (10, 15)
    And I place instance "u1" of cell "blk" under "-" at (50, 50)
    And I rotate component "u1" by 90 degrees
    Then component "u1" spans from (50, 50) to (140, 170)
    And component "u1/l1" spans from (75, 60) to (125, 105)
    # rel(10,15) cw=45 ch=50 H=90 → nrx=H−15−50=25 nry=10 → abs(75,60) size ch×cw=50×45

  Scenario: rotate_180 combines both mirrors; bbox is unchanged
    Given an empty in-memory BDB
    When I define cell "blk" with size 120 by 90
    And I define cell "leaf" with size 45 by 50
    And cell "blk" contains "l1" of "leaf" at (10, 15)
    And I place instance "u1" of cell "blk" under "-" at (50, 50)
    And I rotate component "u1" by 180 degrees
    Then component "u1" spans from (50, 50) to (170, 140)
    And component "u1/l1" spans from (115, 75) to (160, 125)

  Scenario: rotate_270 CCW (90 CW) swaps bbox dimensions and repositions children
    Given an empty in-memory BDB
    When I define cell "blk" with size 120 by 90
    And I define cell "leaf" with size 45 by 50
    And cell "blk" contains "l1" of "leaf" at (10, 15)
    And I place instance "u1" of cell "blk" under "-" at (50, 50)
    And I rotate component "u1" by 270 degrees
    Then component "u1" spans from (50, 50) to (140, 170)
    And component "u1/l1" spans from (65, 115) to (115, 160)
    # rel(10,15) cw=45 ch=50 W=120 → nrx=15 nry=W−10−45=65 → abs(65,115) size ch×cw=50×45

  Scenario: flip_x on one add_inst_to_cell occurrence leaves siblings unchanged
    Given an empty in-memory BDB
    When I define cell "blk" with size 120 by 90
    And I define cell "leaf" with size 45 by 50
    And cell "blk" contains "l1" of "leaf" at (10, 15)
    And I place instance "u1" of cell "blk" under "-" at (50, 50)
    And I place instance "u2" of cell "blk" under "-" at (200, 50)
    And I flip component "u2" in the x direction
    Then component "u1/l1" spans from (60, 65) to (105, 115)
    And component "u2/l1" spans from (265, 65) to (310, 115)
    # u2 (200,50,320,140): new_x1=200+320−255=265  new_x2=200+320−210=310

  Scenario: rotate_90 propagates through a three-level add_inst_to_cell hierarchy
    Given an empty in-memory BDB
    When I define cell "top" with size 120 by 90
    And I define cell "mid" with size 60 by 40
    And I define cell "leaf" with size 20 by 10
    And cell "top" contains "m1" of "mid" at (5, 5)
    And cell "mid" contains "l1" of "leaf" at (5, 5)
    And I place instance "u1" of cell "top" under "-" at (0, 0)
    And I rotate component "u1" by 90 degrees
    Then component "u1" spans from (0, 0) to (90, 120)
    And component "u1/m1" spans from (45, 5) to (85, 65)
    And component "u1/m1/l1" spans from (70, 10) to (80, 30)
    # u1/m1:     rel(5,5)   cw=60 ch=40 H=90 → nrx=H−5−40=45  nry=5  → abs(45,5)  size 40×60
    # u1/m1/l1:  rel(10,10) cw=20 ch=10 H=90 → nrx=H−10−10=70 nry=10 → abs(70,10) size 10×20
