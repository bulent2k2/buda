❯ Now, let's improve DetailedNutsEngine to use tracks that are closest to the
  abstract segment location. But, watch for neighboring bus segments (running
  in adjacent or nearby tracks) and bus segments that share the same tracks.
  We want to keep the overall "topological" relationships between bus segments
   within the same or adjacent Hanan Grids. In a way, we need
  DetailedNutsEngine to work hand in hand with the abstract older brother.
  Give me some options.

⏺ This is a non-trivial placement problem. Let me sketch the three natural
  design points so you can pick the right one:

  ---
  Option A — Center-nearest window (per-bus, no inter-bus coordination)
  
  For each BusSegment independently, instead of taking the first/last N signal
  tracks, find the contiguous N-track window whose centre is closest to 
  track_position. No awareness of other buses.

  Available signal tracks: [458, 462.5, 464, 465.5, 467, 471.5, 473, 474.5,
  ...]
  Abstract track_position: 470   (bus abstract centre)
  Bit-width: 4
  Windows of 4:  [458–465.5] centre=462.5   dist=7.5
                 [462.5–467] centre=464.75  dist=5.25  ← pick this
                 [464–471.5] centre=465.75  dist=4.25  ← pick this
                 ...

  What it fixes: The "wrong end" problem (seg0 starting at 620 instead of 600)
  is gone because the window is anchored to the abstract centre, not lo/hi
  boundary.
  
  What it doesn't fix: If two buses A and B are abstractly adjacent (A at 464,
  B at 510) and both greedily pick windows centred on their abstract positions,
   their windows might overlap when the interval is densely packed.
  
  Effort: ~30 lines. Change the track-selection loop inside run().

  ---
  Option B — Ordered-anchor: sort competing buses, assign windows in abstract 
  order
  
  Identify "competing" bus segments — those sharing the same layer whose
  routing-direction spans overlap (they run through the same Hanan column/row
  and thus contend for the same perpendicular signal tracks). Within each
  competing group:
  
  1. Sort by abstract track_position (low → high).
  2. For each bus in sorted order, find the best N-track window centred on its
  abstract position subject to: window must start above the last assigned track
   + 1 gap.

  This guarantees:
  - No two buses share a signal track within the same routing column.
  - Topological order is preserved: the bus abstractly higher remains higher in
   detailed tracks.
  - Each bus is as close as possible to its abstract position, drift only
  accumulates when packing is tight.
  
  Requires grouping by (layer, span overlap) before entering the per-bus loop —
   analogous to what abstract NUTS already knows from its sweep-line. The
  NUTSResult span data plus BusSegment.interval_lo/hi contains everything
  needed.
  
  Effort: ~80–100 lines. Needs a pre-pass in run() to build competing groups,
  then per-group sorted assignment.

  ---
  Option C — Signal-track mini-NUTS (full fidelity)     
  
  Treat the signal tracks themselves as the resource and re-run a NUTS-style
  1.5-D packing, but now the "segments" are contiguous N-track windows and the
  "track axis" is the signal-track index axis within the interval. Each bus
  gets a target centre (its abstract track_position mapped proportionally into
  the signal-track index space) and a window width (= bit_width tracks). The
  same sweep-line + first-fit algorithm from abstract NUTS runs on these
  windows.

  This handles everything Option B does, plus:
  - Cross-interval coordination: buses whose intervals only partially overlap
  (e.g. in adjacent Hanan cells at a Z-trunk crossing) get consistent track
  usage on the overlap region.
  - No drift accumulation: the solver minimises total displacement from
  abstract positions globally rather than greedily left-to-right.
  
  Essentially NUTSEngine runs twice — once for bus widths (Stage 4), once for
  individual signal tracks (Stage 9). The data types already exist; the main
  additions are a target-centre concept and the mapping from abstract
  perpendicular position to signal-track index.
  
  Effort: ~150–200 lines. Reuses the NUTS algorithm structure but needs a new
  "signal-track segment" type and target-centre cost function.

  ---
  Recommendation                                        
  
  Start with Option B. It is the right semantic: respect abstract NUTS ordering
   within each competing group, anchor each bus's window near its abstract
  position. Option A is a quick improvement but silently breaks when intervals
  are densely occupied. Option C is the correct long-term design — worth
  pursuing after Option B is working and you can see where it falls short on
  real layouts (cross-interval drift at Z-trunk crossings is the most likely
  failure mode).

  The key new data run() needs from the caller for Option B: the set of
  competing-bus groupings. The NUTSResult + BusSegment data is already
  sufficient — the grouping pass is just a span-overlap sweep on
  BusSegment.span_lo/span_hi within each layer.
                                                        
✻ Cooked for 1m 16s

