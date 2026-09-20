@future
# STATUS: spec ahead of implementation — nothing here is bound or built.
# The design is docs/internal/fixed_pin_design.md (convergence-ladder build
# item 7); its published twin is the Fixed Pin Primitive page in
# docs/artifacts.md.  Scenarios are written against ADMITTED and REFUSED
# LANDINGS and against what the audit reports — never against the `fix_pin`
# token grammar — so a change to the spelling does not invalidate them.
# feedthru.feature is this repository's record of what happens otherwise.
# Flip to @landed and bind per PR as the design's three changes land
# (PR 1a: the two-geometry split, byte-identical; PR 1b: strips + command;
# PR 2: layer, order, per-bit targets, from_pins).
Feature: Fixed pins — a busterm restricted to a set of admissible landings
  As a chip planner holding a partner's pin assignment
  I want to constrain where a bus may land on a block
  So that the route meets an interface I do not control.

  #   +--------+      the block; its east face is the edge at x = x2
  #   |  core  |###   ### the admissible strip, tagged with its face edge
  #   +--------+      ===  a wire; x  a landing
  #
  # A fix is the SET OF LANDINGS it still admits.  Every knob defaults to
  # "anything"; adding a token narrows the set.

  Background:
    Given a two-block design with a 4-bit bus from "drv" to "core"

  # ── the core semantics ──────────────────────────────────────────────────

  Scenario: An orientation fix admits both faces of that axis
    When I fix "core.d_in" to horizontal landings
    Then the landing is on the east face or the west face
    And no landing is on the north face or the south face

  Scenario: A horizontal fix admits a horizontal pass-through
    When I fix "core.d_in" to horizontal landings
    Then a trunk crossing "core" horizontally is a landing

  Scenario: A point pin admits no pass-through
    When I fix "core.d_in" to one point on the east face
    Then no trunk crossing "core" is a landing
    And a horizontal trunk at the point's own coordinate is refused
    And the pass-through clamp does not let a trunk slide across the block

  # ── third round, finding 3: a pass-through is two landings ──────────────

  Scenario: A single-face fix admits no pass-through
    When I fix "core.d_in" to the east face
    Then a wire ending on the east face is a landing
    And no trunk crossing "core" is a landing, since it would leave by a face the fix does not admit

  Scenario: A window is a landing range along one face
    When I fix "core.d_in" to a window on the east face
    Then every landing is on the east face inside the window
    And the placement pass slides the landing only inside the window

  # Levels 2 and 3 differ only on a shape with more than one face of a
  # direction, so a rectangle cannot tell them apart — which is why the
  # rectilinear block is the fixture for both.  "Exposed" is the load-bearing
  # word: the edge where two rects of one block meet is not a face.

  Scenario: A face fix admits every exposed face of that direction
    Given "core" is a rectilinear L whose shape has two exposed east faces
    When I fix "core.d_in" to the east face
    Then a landing on either exposed east face is admitted
    And a landing on the edge interior to the union is refused

  Scenario: A precise-face fix admits one named rect's face
    Given "core" is a rectilinear L whose shape has two exposed east faces
    When I fix "core.d_in" to the east face of its second rect
    Then a landing on the second rect's east face is admitted
    And a landing on the first rect's east face is refused

  # ── first round, finding 1: the face tag ────────────────────────────────

  Scenario: A stub ending on a strip's inner edge is not a landing
    When I fix "core.d_in" to the east face
    Then a stub ending on the strip's inner edge is refused
    And a vertical stub ending on the strip's short end is refused

  Scenario: A wire may arrive over the cell but must end on the tagged face
    Given the approach layer crosses leaf cells
    When I fix "core.d_in" to the east face
    Then a wire arriving from the west that ends on the east face is a landing
    And a wire arriving from the west that stops at the inner edge is refused

  # ── second round, finding 1: two geometries ─────────────────────────────

  Scenario: A fixed pin does not open a lower-layer path through the block
    Given "core" is a leaf block that blocks the lower layers
    When I fix "core.d_in" to a window on the east face
    Then the lower-layer obstruction over "core" is unchanged
    And no lower-layer segment routes through the block body

  Scenario: A fixed pin does not change the block's footprint keepout
    Given "core" is a leaf block that blocks the lower layers
    When I fix "core.d_in" to one point on the east face
    Then the footprint keepout for "core" is unchanged

  # ── second round, finding 2: the physical-face spelling ─────────────────

  Scenario: A margined block admits no landing on its physical face outside the strip
    Given "core" carries a corner margin of 4
    When I fix "core.d_in" to a window on the east face
    Then a stub ending on the physical east face outside the window is refused

  # The twin, and the reason the scenario above is not the whole story: the
  # physical spelling is accepted on purpose, so that a hand-built or restored
  # endpoint on a margined block keeps its tap.  Refusing the physical face by
  # DELETING that path would satisfy the scenario above and silently break
  # every checkpoint holding such a segment.  Both must hold at once.

  Scenario: A restored endpoint inside the window keeps its tap on the physical face
    Given "core" carries a corner margin of 4
    And a checkpoint holds a segment landing on the physical east face inside the window
    When I fix "core.d_in" to a window on the east face
    And the checkpoint is restored
    Then that landing is admitted

  # ── second round, finding 3: margin versus window ───────────────────────

  Scenario: A declared window is not narrowed by a corner margin
    Given "core" carries a corner margin of 4
    When I fix "core.d_in" to the window 20 to 60 on the east face
    Then the admissible window reported for "core.d_in" is 20 to 60

  # ── second round, finding 4: the die-port relation ──────────────────────

  Scenario: A die port declared with every face admissible routes identically
    Given a DEF die port "p" routed today
    When I declare "p" with every face admissible
    Then the route is byte-identical

  Scenario: A single-face fix on a die port admits a subset of its landings
    Given a DEF die port "p"
    When I fix "p" to the east face
    Then every admitted landing is one the unfixed port also admitted

  # ── second round, finding 5: the automatic bit cap ──────────────────────

  Scenario: A narrow window is not silently under-capped
    Given the bundle bit bound is automatic
    When I fix "core.d_in" to a 40-unit window for a 32-bit bus
    Then the bundle is split for the window, or the shortfall is reported

  # ── second round, finding 8: a mixed bundle is split ────────────────────

  Scenario: A bundle whose bits carry different fixes is split, loudly
    Given "core.d_in" bits 0 to 3 carry one fix and bits 4 to 7 another
    When the bundler runs
    Then the bundle is split into fix-uniform parts
    And the split is reported naming both fixes

  # ── layer: the approach layer, not the pin's metal ──────────────────────

  Scenario: A compatible approach layer is honoured
    When I fix "core.d_in" to the east face on a horizontal layer
    Then the landing stub is on that layer
    And no layer-direction violation is reported

  Scenario: A layer that runs the wrong way for the face is refused
    When I fix "core.d_in" to the east face on a vertical layer
    Then the declaration is refused naming the face and the layer

  Scenario: A pin drawn on a crossing layer is reached by an endpoint via
    Given "core.d_in" is a LEF pin on a vertical layer on the east face
    When I consume the block's pins
    Then the landing stub is on an adjacent horizontal layer
    And each bit carries one via between the stub and the pin metal

  # ── third round, finding 1: the endpoint via is its own kind ────────────

  Scenario: An endpoint via is not a shield bond strap
    Given "core.d_in" is a LEF pin on a vertical layer on the east face
    And the bundle's rule bonds its shields
    When I consume the block's pins
    Then each bit's endpoint via survives the bond-strap pass
    And it is persisted on the bit's own net
    And a bottom-up copy of the instance carries it

  # ── third round, finding 2: the layer set binds the landing stub only ───

  Scenario: A layer set binds the landing stub and nothing else
    When I fix "core.d_in" to the east face on either of two horizontal layers
    Then the landing stub is on one of the two
    And the vertical leg of a bent candidate may take any vertical layer

  # ── order: per landing, not per segment ─────────────────────────────────

  # Third round, finding 4: a fix precedes bundling, so no segment exists
  # at declaration to ask whether two landings share one.  The check is per
  # candidate, at generation.

  Scenario: Opposite orders drop the straight candidate and keep the bend
    When I fix two pins facing each other with opposite orders
    Then the declaration is accepted
    And a candidate joining them with one straight segment is dropped naming both pins
    And a bent candidate joining them survives

  Scenario: Orders may differ across a bend
    When I fix one leg of an L to index order and the other to reverse order
    Then each leg takes its own landing's order

  Scenario: Index order puts bit 0 at the lowest coordinate
    When I fix "core.d_in" to a window on the east face in index order
    Then bit 0 lands at the lowest coordinate in the window
    And bit 3 lands at the highest

  # ── templates ───────────────────────────────────────────────────────────

  Scenario Outline: A cell fix transforms with its instance
    Given "core_cell" is instantiated with orientation <orient>
    When I fix "core_cell.d_in" to a window on the east face in index order
    Then the admissible face on that instance is <face>
    And bit 0 is at the <end> end of the window

    Examples:
      | orient | face  | end  |
      | N      | east  | low  |
      | S      | west  | high |
      | FN     | west  | low  |
      | FS     | east  | high |
      | E      | north | low  |
      | W      | south | high |

  Scenario: A bottom-up template's copies inherit the fix
    Given "core_cell" is a bottom-up template with congruent instances
    When I fix "core_cell.d_in" to a window on the east face
    Then every copied instance lands inside its own transformed window

  # ── failure is loud and never strands ───────────────────────────────────

  Scenario: A fix no candidate can honour is reported and the bus still routes
    When I fix "core.d_in" to a face no candidate reaches
    Then generation reports the pin and the fix it could not honour
    And the bundle keeps its best candidate
    And the design audit reports a PIN_FIX violation for that landing

  # ── the invariant ───────────────────────────────────────────────────────

  Scenario: A design with no fix is byte-identical
    When I declare no fix
    Then the route equals the route before fixed pins existed
