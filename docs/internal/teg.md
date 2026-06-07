Terminal Equivalence Groups
====

Impl by Claude Code
====

Good. I have all the context needed. Here's the plan:
  1. Add TegMode enum + teg_mode on Busterm and Floorplan in topology.h
  2. Implement bridge generation in add_trunk_h / add_trunk_v in topology.cpp
  3. Add bridge_segments map to Topology
  4. Expose in bindings.cpp + update CLI add_block parser
  5. Build and test

  Starting with the header:

⏺ Update(src/topology.h)
  ⎿  Added 7 lines
       6  #include <optional>
       7  #include "bundler.h"
       8  namespace interconnect {
       9 + 
      10 +// How a multi-rect block handles a trunk that falls in the gap between 
         +its rects. 
      11 +enum class TegMode { 
      12 +    THRU,  // default: connect only the nearest rect; block's internal r
         +outing joins 
      13 +    OVER,  // connect both rects with V stubs + an explicit bridge segment over the block top 
      14 +}; 
      15 + 
      16  struct Point { int x, y; };
      17  struct Rect {
      18      int x1, y1, x2


Drawing (by antigravity cli)
====

Now that we have multi-rect support in add_block, we can handle not only
rectilinear polygons, but also disjoint "busterms". They are called to be
in a terminal equivalence group. The topology needs to connect to only one.
It can pick the best one. If we want the topology to connect to both, then 
use two separate add_block definitions.

[Figure: Options](../../fig/teg1_options.png)

  A — Dashed bounding box: The union bbox becomes a ghost outline. Familiar
  (like a "group selection" rectangle), but can enclose a lot of empty space
  for widely separated rects, which may itself be misleading about block
  geometry.

  B — Center connector line: A dotted line joins rect centers. Always visible
  regardless of how far apart the rects are, and doesn't add any spurious
  filled region. Downside: with many rects or crossing connectors it can look
  like routing wires.

  C — Per-block unique colour: Each block gets its own pastel fill; the colour
  is the grouping signal. Instantly readable at a glance with no extra
  geometry, and works cleanly at any scale. Downside: needs a colour palette
  and could clash with the wire/layer colours used for routed buses.

  My recommendation: C is the clearest visually and the easiest to parse at a
  glance. It's also composable — you can layer it with B (dotted connector) for
   the rare case where rects are so far apart they fall off-screen together.
  Want me to implement one (or a combination)?

✻ Baked for 1m 11s

※ recap: Improving BUDA's block visualizer for multi-rect blocks. Three
  color-scheme options (A/B/C) were presented for grouping disconnected
  equivalent-busterm rects. Waiting for the user to pick one to implement.

❯ I like A, these equivalent busterms come from within blocks anyways. 

✻ Churned for 1m 29s

❯ Do we need the bbox outline when the rects are touching or overlapped 
  already? 
 
⏺ Good point — for touching/overlapping rects the individual outlines already
  show the shape clearly; the dashed box is only needed when there's a gap
  between them. I'll add a connectivity check.
  
See [`def _rects_disconnected(rects_raw)`](../../src/buda_viz.py)

※ recap: Added multi-rect block support end to end: CLI syntax, C++ API, and 
  visualization. The viz now draws individual rects with a dashed grouping box 
  for disconnected equivalent-busterm blocks. Next: run teg1.buda to verify the
   full pipeline.

❯ Let's also update the feature file and visualize teg1.buda 
