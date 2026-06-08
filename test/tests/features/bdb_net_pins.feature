Feature: BDB net/pin commands and bdb_net_mode toggle
  add_cell_pin defines cell-level port interfaces.
  add_net_pins inserts a net plus leaf and intermediate hierarchy pins.
  bdb_net_mode on/off controls whether add_net/add_bus write to BDB.

  # ── add_cell_pin ─────────────────────────────────────────────────────────

  Scenario: add_cell_pin stores direction and offset
    Given an empty in-memory BDB with net support
    When I define cell "buf" with size 100 by 50
    And I add cell pin "buf"."in" direction INPUT at (0, 25)
    And I add cell pin "buf"."out" direction OUTPUT at (100, 25)
    Then cell pin "buf"."in" has direction INPUT and offset (0, 25)
    And cell pin "buf"."out" has direction OUTPUT and offset (100, 25)

  Scenario: add_cell_pin without position stores sentinel -1
    Given an empty in-memory BDB with net support
    When I define cell "buf" with size 100 by 50
    And I add cell pin "buf"."out" direction OUTPUT without position
    Then cell pin "buf"."out" has direction OUTPUT and offset (-1, -1)

  Scenario: add_cell_pin is idempotent — second call updates the record
    Given an empty in-memory BDB with net support
    When I define cell "buf" with size 100 by 50
    And I add cell pin "buf"."p" direction INOUT at (0, 10)
    And I add cell pin "buf"."p" direction INPUT at (5, 10)
    Then cell pin "buf"."p" has direction INPUT and offset (5, 10)

  # ── add_net_pins: leaf pins ───────────────────────────────────────────────

  Scenario: add_net_pins creates net and leaf pins with correct directions
    Given an empty in-memory BDB with a two-level hierarchy
    When I call add_net_pins "sig" driver "u1/l1.out" receivers "u2/l1.in"
    Then net "sig" exists in BDB
    And pin on net "sig" at component "u1/l1" is named "out" with direction OUTPUT
    And pin on net "sig" at component "u2/l1" is named "in" with direction INPUT

  # ── add_net_pins: hierarchy propagation ──────────────────────────────────

  Scenario: add_net_pins propagates interface pins through hierarchy
    Given an empty in-memory BDB with a two-level hierarchy
    When I call add_net_pins "sig" driver "u1/l1.out" receivers "u2/l1.in"
    Then net "sig" has 4 pins total
    And pin on net "sig" at component "u1" is named "sig" with direction OUTPUT
    And pin on net "sig" at component "u2" is named "sig" with direction INPUT

  Scenario: add_net_pins with three-level hierarchy propagates all intermediate levels
    Given an empty in-memory BDB with a three-level hierarchy
    When I call add_net_pins "data" driver "top/mid/leaf.q" receivers "top/mid2/leaf.d"
    Then net "data" has 4 pins total
    And pin on net "data" at component "top/mid/leaf" is named "q" with direction OUTPUT
    And pin on net "data" at component "top/mid" is named "data" with direction OUTPUT
    And pin on net "data" at component "top/mid2/leaf" is named "d" with direction INPUT
    And pin on net "data" at component "top/mid2" is named "data" with direction INPUT

  # ── add_net_pins: cell_pin offset used for absolute pin position ──────────

  Scenario: add_net_pins uses cell_pin offset for absolute leaf pin position
    Given an empty in-memory BDB with a two-level hierarchy
    When I add cell pin "leaf"."out" direction OUTPUT at (50, 25)
    And I call add_net_pins "sig" driver "u1/l1.out" receivers "u2/l1.in"
    Then pin on net "sig" at component "u1/l1" has absolute position (110, 60)
    # u1/l1 is at (60, 35); cell_pin offset (50, 25) → abs (110, 60)

  # ── bdb_net_mode via BudaSession CLI ─────────────────────────────────────

  Scenario: bdb_net_mode off (default) — add_net does not write to BDB
    Given a BudaSession with an open BDB and a two-level hierarchy
    When I run "add_net clk u1/l1.out u2/l1.in" through the session
    Then net "clk" is not present in the session BDB

  Scenario: bdb_net_mode on — add_net writes net and pins to BDB
    Given a BudaSession with an open BDB and a two-level hierarchy
    When I run "bdb_net_mode on" through the session
    And I run "add_net clk u1/l1.out u2/l1.in" through the session
    Then net "clk" exists in the session BDB
    And the session BDB has 4 pins for net "clk"

  Scenario: bdb_net_mode on — add_bus writes N nets to BDB
    Given a BudaSession with an open BDB and a two-level hierarchy
    When I run "bdb_net_mode on" through the session
    And I run "add_bus data[4] u1/l1.out u2/l1.in" through the session
    Then net "data_0" exists in the session BDB
    And net "data_1" exists in the session BDB
    And net "data_2" exists in the session BDB
    And net "data_3" exists in the session BDB

  Scenario: bdb_net_mode off again — subsequent add_net does not write to BDB
    Given a BudaSession with an open BDB and a two-level hierarchy
    When I run "bdb_net_mode on" through the session
    And I run "bdb_net_mode off" through the session
    And I run "add_net clk u1/l1.out u2/l1.in" through the session
    Then net "clk" is not present in the session BDB
