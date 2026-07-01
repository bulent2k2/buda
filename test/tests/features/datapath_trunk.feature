Feature: Multi-trunk BITRUNK trees for high-fan-out datapath nets
  As a chip planner
  I want the generator to build two-level BITRUNK trees (a root spine feeding
  perpendicular branch trunks) in both orientations for high-fan-out nets over
  regular datapath-like placements
  So that a driver broadcasting to rows/columns of blocks routes as the naturally
  optimal T / H shape instead of one long single spine.

  # Opt-in: these candidates only appear under the multi_trunk generator flag,
  # so default candidate sets (and their rankings) are unchanged.

  # ---------------------------------------------------------------------------
  # Column datapath → BITRUNK_HVH: root H spine feeds two V branch trunks, each
  # running down a column of receivers (multi-tap pass-through), driver taps root.
  # ---------------------------------------------------------------------------
  Scenario: Two receiver columns broadcast from a driver → BITRUNK_HVH
    Given a block "S"  at (0,0)-(60,60)
    And a block "A0" at (200,100)-(260,160)
    And a block "A1" at (200,300)-(260,360)
    And a block "A2" at (200,500)-(260,560)
    And a block "B0" at (500,100)-(560,160)
    And a block "B1" at (500,300)-(560,360)
    And a block "B2" at (500,500)-(560,560)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multi-trunk candidates from "S" to ["A0","A1","A2","B0","B1","B2"]
    Then a candidate of type "BITRUNK_HVH" exists
    And every BITRUNK candidate connects all 7 blocks
    And every BITRUNK candidate has no cycles
    And every BITRUNK candidate's root trunk is horizontal

  # ---------------------------------------------------------------------------
  # Row datapath → BITRUNK_VHV: root V spine feeds two H branch trunks, each
  # running across a row of receivers, driver taps root.
  # ---------------------------------------------------------------------------
  Scenario: Two receiver rows broadcast from a driver → BITRUNK_VHV
    Given a block "S"  at (0,0)-(60,60)
    And a block "A0" at (100,200)-(160,260)
    And a block "A1" at (300,200)-(360,260)
    And a block "A2" at (500,200)-(560,260)
    And a block "B0" at (100,500)-(160,560)
    And a block "B1" at (300,500)-(360,560)
    And a block "B2" at (500,500)-(560,560)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multi-trunk candidates from "S" to ["A0","A1","A2","B0","B1","B2"]
    Then a candidate of type "BITRUNK_VHV" exists
    And every BITRUNK candidate connects all 7 blocks
    And every BITRUNK candidate has no cycles
    And every BITRUNK candidate's root trunk is vertical

  # ---------------------------------------------------------------------------
  # Multi-tap pass-through: a single branch trunk runs down a column of three
  # collinear blocks and taps each (no per-block stub) — the branch's span covers
  # the whole column.
  # ---------------------------------------------------------------------------
  Scenario: A branch trunk passes through a column of blocks (multi-tap)
    Given a block "S"  at (0,0)-(60,60)
    And a block "A0" at (200,100)-(260,160)
    And a block "A1" at (200,300)-(260,360)
    And a block "A2" at (200,500)-(260,560)
    And a block "B0" at (500,100)-(560,160)
    And a block "B1" at (500,300)-(560,360)
    And a block "B2" at (500,500)-(560,560)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multi-trunk candidates from "S" to ["A0","A1","A2","B0","B1","B2"]
    Then some BITRUNK_HVH candidate has a vertical branch spanning column "B0","B1","B2"

  # ---------------------------------------------------------------------------
  # Opt-in guard: without the multi_trunk flag, the new two-level trees are not
  # produced.  (The legacy single-level BITRUNK_H remains on the default path, so
  # default candidate sets are unchanged — only BITRUNK_HVH / BITRUNK_VHV are
  # gated behind the flag.)
  # ---------------------------------------------------------------------------
  Scenario: The two-level BITRUNK trees are opt-in — plain generation produces none
    Given a block "S"  at (0,0)-(60,60)
    And a block "A0" at (200,100)-(260,160)
    And a block "A1" at (200,300)-(260,360)
    And a block "A2" at (200,500)-(260,560)
    And a block "B0" at (500,100)-(560,160)
    And a block "B1" at (500,300)-(560,360)
    And a block "B2" at (500,500)-(560,560)
    And layer M4 is HORIZONTAL with id 4
    And layer M5 is VERTICAL with id 5
    When I generate multicast candidates from "S" to ["A0","A1","A2","B0","B1","B2"] using layers M4,M5
    Then no candidate of type "BITRUNK_HVH" exists
    And no candidate of type "BITRUNK_VHV" exists
