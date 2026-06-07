Feature: add_inst_to_cell — cell-occurrence expansion

  Scenario: child component is auto-created when the parent cell is placed
    Given an empty in-memory BDB
    When I define cell "blk" with size 100 by 80
    And I define cell "dot" with size 20 by 10
    And cell "blk" contains "d1" of "dot" at (10, 5)
    And I place instance "u1" of cell "blk" under "-" at (0, 0)
    Then component "u1/d1" spans from (10, 5) to (30, 15)

  Scenario: placing a parent with cell children marks it as non-leaf
    Given an empty in-memory BDB
    When I define cell "blk" with size 100 by 80
    And I define cell "dot" with size 20 by 10
    And cell "blk" contains "d1" of "dot" at (10, 5)
    And I place instance "u1" of cell "blk" under "-" at (0, 0)
    Then component "u1" is not a leaf

  Scenario: child depth is parent depth plus one
    Given an empty in-memory BDB
    When I define cell "blk" with size 100 by 80
    And I define cell "dot" with size 20 by 10
    And cell "blk" contains "d1" of "dot" at (10, 5)
    And I place instance "u1" of cell "blk" under "-" at (0, 0)
    Then component "u1" has depth 0
    And component "u1/d1" has depth 1

  Scenario: two placements of the same cell create independent subtrees
    Given an empty in-memory BDB
    When I define cell "blk" with size 100 by 80
    And I define cell "dot" with size 20 by 10
    And cell "blk" contains "d1" of "dot" at (10, 5)
    And I place instance "u1" of cell "blk" under "-" at (0, 0)
    And I place instance "u2" of cell "blk" under "-" at (200, 0)
    Then component "u1/d1" spans from (10, 5) to (30, 15)
    And component "u2/d1" spans from (210, 5) to (230, 15)
    And the database contains 4 components

  Scenario: three-level cell structure expands all descendants
    Given an empty in-memory BDB
    When I define cell "top" with size 300 by 200
    And I define cell "mid" with size 100 by 80
    And I define cell "leaf" with size 20 by 10
    And cell "top" contains "m1" of "mid" at (10, 10)
    And cell "mid" contains "l1" of "leaf" at (5, 5)
    And I place instance "u_top" of cell "top" under "-" at (0, 0)
    Then component "u_top/m1" spans from (10, 10) to (110, 90)
    And component "u_top/m1/l1" spans from (15, 15) to (35, 25)
    And component "u_top" has depth 0
    And component "u_top/m1" has depth 1
    And component "u_top/m1/l1" has depth 2

  Scenario: add_inst_to_cell after placement does not retroactively expand
    Given an empty in-memory BDB
    When I define cell "box" with size 50 by 50
    And I define cell "dot" with size 10 by 10
    And I place instance "u" of cell "box" under "-" at (0, 0)
    And cell "box" contains "p" of "dot" at (5, 5)
    Then the database contains 1 component
    And component "u/p" does not exist
