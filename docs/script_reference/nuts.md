# BUDA Script Reference — Stages 4 & 9 — Track assignment (NUTS)

Abstract bus-level track placement and bit-level detailed placement, plus the feedback re-route: `run_nuts`, `run_nuts_on_layer`, `run_detailed_nuts`, `ripup_reroute`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Stage 4 — Abstract NUTS

### `run_nuts`

```
run_nuts [<track_pitch>]
```

Runs the Non-Uniform Track Sharing (NUTS) 1.5-D rectangle packing solver.
Assigns a concrete perpendicular `track_position` to every bus segment on
every layer, guaranteeing no physical overlaps (within capacity).

The algorithm sweeps segments by span start, placing each new segment at the
lowest feasible position within its Hanan-grid-cell interval constraint using
a first-fit strategy. Each layer is solved independently and in parallel.

| Argument | Type | Default | Description |
|---|---|---|---|
| `track_pitch` | float | stored pitch | Minimum gap between the upper edge of one segment and the lower edge of the next, in layout units. When omitted, reuses the pitch previously set by `set_track_pitch` (or `1.0` if never set). |

**Output:** Prints segment count, interval violations, and track overlap counts
per layer. Writes a detailed overlap report to `<script>_nuts.log`.

**Pitch consistency warning:** If an explicit `track_pitch` argument is given
that differs from the pitch `run_planner` was called with, a warning is printed
reminding you to call `set_track_pitch` before `run_planner` (or re-run
`run_planner`) so the planner's band reservations and NUTS's packing gap agree.

**Notes:**
- Must be called after `run_planner` (or after `generate_topologies_for_bundle`
  if skipping the planner).
- The track pitch used here is remembered and reused by `run_nuts_on_layer`.
- An *interval violation* means a segment could not fit within its Hanan-cell
  interval; it is placed at the interval centre as a best-effort fallback and
  counted.
- A *track overlap* means two segments on the same layer have overlapping
  spans and overlapping perpendicular extents — a physical short. The overlap
  report details each collision.

**Example:**
```
run_nuts 2.0
```

---

### `run_nuts_on_layer`

```
run_nuts_on_layer <layer_name>
```

Re-solve NUTS for a single named layer, leaving all other layers untouched.
Useful for iterative refinement after inspecting the overlap log for a
specific layer.

| Argument | Type | Description |
|---|---|---|
| `layer_name` | str | Layer name as declared in `def_layer`, e.g. `M3` or `M5`. |

**Requires:** `run_nuts` must have been called first; `run_nuts_on_layer`
updates the existing `NUTSResult` in place.

**Output:** Prints per-layer violation and overlap counts for the re-solved
layer. Appends a timestamped section to the existing `<script>_nuts.log`.

**Example:**
```
run_nuts 2.0
run_nuts_on_layer M3     # re-solve only M3 after reviewing the log
run_nuts_on_layer M5     # then re-solve M5 if needed
```

---


## Stage 9 — Detailed NUTS

Stage 9 snaps each abstract bus segment (from Stage 4) to concrete signal-track positions (from Stage 8). See [docs/detailed_nuts.md](../detailed_nuts.md) for the full design.

### `run_detailed_nuts`

```
run_detailed_nuts [lo_hi|hi_lo]
```

For each bus segment in the NUTS result, calls `signal_tracks_in()` on its layer's routing grid and assigns one signal track per bit-wire.

| Argument | Type | Default | Description |
|---|---|---|---|
| `lo_hi` / `hi_lo` | keyword | `lo_hi` | Bit ordering: `lo_hi` assigns bit 0 to the lowest available signal track, increasing upward; `hi_lo` assigns bit 0 to the highest track, decreasing. |

**Algorithm:**

1. Convert each `TrackSegment` from `run_nuts` to a `BusSegment` (layer, span, interval, bit_width from rounded abstract width).
2. For each `BusSegment`, enumerate signal tracks inside `[interval_lo, interval_hi]` using the layer's `RoutingGrid`.
3. Select tracks according to `bit_order`:
   - `lo_hi`: take the first `bit_width` signal tracks.
   - `hi_lo`: take the last `bit_width` signal tracks.
4. If a bus is marked `timing_critical` (not yet settable from `.buda`; API available in Python): find the first contiguous window of `bit_width` signal tracks — a window where no POWER/GROUND/CLOCK track centre lies between any adjacent pair.
5. If fewer signal tracks are available than `bit_width`, or no valid contiguous window exists, the entire bus is **unplaced** (all-or-nothing; no partial placement).

**Output:** Prints the total number of net segments placed and the number of bits unplaced.

**Leaf-cell keepouts (Gap 2):** Before solving, `run_detailed_nuts` automatically
installs keepout zones on every non-TOP layer's routing grid for each solid
(non-container) leaf-cell block. This prevents signal tracks from routing over
cell interiors on LOW layers — matching the constraint the planner and abstract
NUTS already enforce. Blocks marked `container` (hierarchy envelopes) are
excluded from this automatic keepout. The keepouts are installed once per routing
grid object and are transparent to repeated `run_detailed_nuts` calls.

**Requires:** `run_nuts` must have been called first. At least one `def_track_pattern` must cover the layers used by the NUTS result.

**Example:**
```buda
run_nuts 2.0
def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0
run_detailed_nuts
```

With HI_LO ordering:
```buda
run_detailed_nuts hi_lo
```

---

### `ripup_reroute`

```
ripup_reroute [max_iter] [use_edge_candidates]
```

Feedback-driven rip-up & re-route. The congestion planner's band-capacity model is
layout-width based, so it can report `overflow=0` for a band that NUTS (or
DetailedNUTS) later finds contended — and re-running `run_planner` re-derives the
same plan. `ripup_reroute` closes that loop: it reads the **actual** overlaps/opens,
greedily re-routes a contending bundle to an alternate topology candidate, re-runs
the pipeline, and keeps only moves that reduce the metric.

| Argument | Type | Default | Description |
|---|---|---|---|
| `max_iter` | int | `10` | Maximum number of outer hill-climb iterations (each commits at most one re-route). |
| `use_edge_candidates` | flag | off | Also try the per-edge MST L/Z **flip** move-source (below) on contended MST candidates. Off by default. The two tokens are order-independent — `ripup_reroute 20 use_edge_candidates` and `ripup_reroute use_edge_candidates 20` are equivalent. |

**Two stages, auto-detected from pipeline state:**

| Run it after… | Stage | Metric driven down |
|---|---|---|
| `run_nuts` | a | NUTS abstract track overlaps (`num_overlaps`) |
| `run_detailed_nuts` | b | DetailedNUTS opens / unplaced bits (`num_unplaced`) |

**Algorithm (greedy hill-climb, first-improving-contender):** each iteration
snapshots per-bundle state, then scans the contending bundles in priority order
(the open bundles in stage b, then the bundles on either side of each NUTS
overlap) and **commits the first contender whose best alternate candidate lowers
the metric** — re-pinning it and re-running planner→NUTS(→DNUTS) silently. It
stops when the metric reaches 0, no contender improves, or `max_iter` is hit. It
is a **no-op** when the metric is already 0 (prints `metric already 0 — nothing
to do`).

> Each trial is a full pipeline re-run, so committing the *first* improving
> contender (rather than the single best move over *all* contenders) keeps an
> iteration cheap on large designs. A flushed per-contender heartbeat makes
> progress visible. On a big design the default `max_iter` may be reached while
> the metric is still falling — the command says so and suggests re-running or
> raising `max_iter`.

Output logs each contender tried and each committed move, e.g.:

```
[ripup_reroute] stage b (DNUTS opens): start metric=236, max_iter=10, 25 contenders
[ripup_reroute] iter 1: contender 1/25 bundle 102 improves 236->174 (topo 4->6)
[ripup_reroute] iter 1: COMMIT bundle 102 topo 4->6, metric 236->174
...
[ripup_reroute] reached max_iter=10 while still improving — re-run ripup_reroute or raise max_iter (e.g. `ripup_reroute 50`) to continue.
[ripup_reroute] done: metric 236->46 after 10 move(s), 96 trial(s).
```

**Flat and hier flow:** Works after both `run_planner` and `run_planner hier`. In
hier flow `self.bundles` is the expanded per-instance list, so a re-route re-pins a
single **instance** wrapper and re-plans the expanded set in place — the right
granularity for relieving a local congestion hot-spot without disturbing the cell's
other instances.

**Move sources (what a contender is allowed to try):**
- **Index alternates (always on).** Re-pin the bundle to one of its *other*
  candidate topologies, tried relevance-first (candidates farthest from the
  measured contention first).
- **Per-edge MST L/Z flip (`use_edge_candidates`, off by default).** When the
  bundle's *selected* candidate is an MST type (incl. `TRUNK+MST` hybrids, whose
  legs are edge-tagged), also flip the L/Z bend of that candidate's **contended**
  edges in place — a targeted local change that keeps the same candidate index.
  Only contended edges are tried, so the cost stays ~linear in the number of
  overflows rather than exploding to `2ᴺ`. This source is **opt-in** because on
  the current corpus a flip is only ever *tried* on real contended MST edges — an
  index alternate always wins the commit — so leaving it off changes no routes.
  Enable it only when you want to explore edge flips. See
  [MST edge realization](../internal/mst_edge_realization.md).

**Notes:**
- It is an explicit congestion-fix pass, so it may re-route any contended bundle —
  including one pinned earlier by `select_topology` (its pin is replaced).
- The base flow is unchanged unless an improving move is found; the command is
  opt-in and additive.
- A stage-b `hi_lo` bit-order selection is preserved across the re-route.

**Requires:** `run_planner` (or `run_planner hier`) and at least `run_nuts` to have
run first.

**Recommended usage — run it in both places.** The two stages clear *different*
causes of unplaced bits, so chaining them clears the most:

```buda
run_planner 5            # or: run_planner hier 5
run_nuts
ripup_reroute            # stage a: drive NUTS overlaps toward 0
run_detailed_nuts
ripup_reroute            # stage b: drive the residual DNUTS opens toward 0
```

Stage a removes the opens that stem from abstract track **contention** (when two
buses overlap on a band, DetailedNUTS cannot fit all their bits) — these are the
cheap, high-yield wins, and clearing them gives DetailedNUTS a much better
starting point. The opens that survive stage a (with `overlaps=0`) are **not**
contention-driven; they are signal-track **capacity** shortfalls — a band whose
abstract width fit but whose discrete SIGNAL-track count is short of the bit count
— which only stage b can re-route around. Measured on
`flow/rnr/mix.buda` (a hier design): baseline 21 overlaps / 236 opens → stage a
**21→0 overlaps, 236→150 opens** → stage b **150→30 opens**, versus 236→46 for
stage b alone.

> The residual opens that neither pass can clear are the pure capacity-mismatch
> cases. Predicting them inside the planner (charging band capacity in
> signal-track count rather than layout width) is a planned follow-on — see
> *Gap A part 2* in `docs/internal/wishlist-planner.md`.

---

## Wirelength reporting

### `report_wirelength` (alias `report_wl`)

```
report_wirelength
```

Report routed wirelength per bundle + a design total, so a change to topology
generation / planning / NUTS can be compared for interconnect quality. Prints the
**abstract** bus-level WL (one length per placed bus segment — the metric topology
decisions move) once `run_nuts` has run, and adds the **detailed** bit-level WL
(every bit-wire) once `run_detailed_nuts` has run, each with a per-layer metal
breakdown. The full per-bundle table is captured to the flow log; the terminal
shows the totals.

**DOF envelope `[lo..hi]`.** Each bundle's routed WL is shown against the interval
its selected topology's *degrees of freedom* permit — computed from the segments'
slide ranges and min/max span (`ConnTopology`): a segment joined to a trunk at a
junction can slide over that trunk's `[perp_lo, perp_hi]`, and a flexible trunk
end contracts toward its coverage floor. Per bundle the table shows:

| Column | Meaning |
|---|---|
| `lo` / `hi` | Lower / upper bound of the WL the topology's DOF permit |
| `WL` | Routed **topology-segment** wire (the portion the envelope brackets) |
| `jog` | NUTS-inserted dogleg-jog wire (extra metal *outside* the topology; excluded from `WL`) |
| `fill` | Where `WL` sits in `[lo..hi]` — 0 % at the lower bound, so **lower = tighter**, i.e. more of the topology's slide freedom was spent shortening the route |

The envelope is a **valid OUTER bracket** — the routed non-jog WL always lands
inside — but a **loose** one: the per-segment extremes are not simultaneously
realizable (a shared trunk cannot sit at both ends of its slide at once), so `lo`
is a true lower bound and `hi` a true upper bound, not a tight prediction. The
detailed section brackets the bit-level total against the **bit-scaled** envelope
(`[lo..hi] × bit count`); per-bit jogs/vias add wire beyond a flat scale, so the
detailed WL can ride high in — or slightly above — that scaled envelope.

Every total carries its **unplaced** count on the same line: WL sums only placed
wire, so a lower number that comes from dropped segments/bits is flagged rather
than silently rewarded.

---
