# BUDA Script Reference — Stage 3 — Global router / planner

Topology selection and layer assignment: `set_planner_param`, `run_planner` (incl. `hier` and `post_nuts` modes), `select_topology`, `select_topologies`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Stage 3 — Global router / planner

### `set_planner_param`

```
set_planner_param <name> <value>
```

Tune a global planner cost coefficient. Takes effect at the next `run_planner`:
each run seeds a fresh planner from all values set so far, so knobs may be
adjusted between runs to re-plan with different weights.

| Parameter | Default | Description |
|---|---|---|
| `kCong` | `1.0` | Congestion cost coefficient. Multiplies the overflow ratio `max(0, usage+eff_width−cap)/cap` for each channel band (zero when the segment fits). Note: overflow is gated as a hard constraint first (see `run_planner`); `kCong` only arbitrates among candidates when overflow is genuinely unavoidable, or prices residual soft pressure. |
| `kSpan` | `0.001` | Span-mismatch cost coefficient. Multiplies the excess span outside a layer's `[span_min, span_max]` window. Guides long segments to higher metal and short stubs to lower metal. |
| `base_cost_non_top` | `0.5` | Penalty per segment for using a non-`TOP` layer, scaled by segment span (see `base_span_ref`). Keeps the default preference on top layers without hard-blocking lower ones. |
| `base_span_ref` | 25% of the larger Hanan grid extent | Span at which a segment pays the full `base_cost_non_top`; shorter segments pay proportionally less (`× span/base_span_ref`). Short stubs therefore drop to lower layers when TOP bands saturate instead of detouring on TOP — preserving TOP capacity for long trunks. |
| `kWL` | `0.001` | Wirelength cost per layout unit, added to the topology score. Steers equal-congestion choices toward shorter topologies, so a detour wins only when it avoids real congestion. |
| `kBalance` | `0.01` | TOP-layer load-balancing weight. Adds `kBalance × (layer's committed load / max same-direction layer load)` to each candidate `TOP` layer's segment score, biasing an equal-cost segment toward the **less-loaded** of the same-direction TOP layers. Without it, equal-cost ties (e.g. on TOP layers with no span window, where span/base costs are 0) break toward the highest metal, piling every H segment on the top H layer and every V segment on the top V layer — the over-subscription that drives NUTS track overlaps. `LOW` layers don't compete (they carry `base_cost_non_top`). Set `0` to disable balancing and restore the highest-metal tie-break. Effective range is small: the useful plateau is roughly `[0.005, 0.015]`; above it, over-balancing starts pushing buses onto LOW layers. |
| `kHeight` | `0.05` | Layer-height cost for **short** segments on `TOP` layers — the mirror image of the span-scaled `base_cost_non_top`. Adds `kHeight × height_rank × max(0, 1 − seg_span/base_span_ref)` where `height_rank` is the layer's index among the same-direction TOP layers ascending (lowest TOP metal = 0). A short stub pays per rank to climb the stack (each rank up is a taller via stack for no benefit), so it prefers the **lowest feasible TOP layer**; a long trunk (`span ≥ base_span_ref`) pays nothing and keeps the TOP-most trunk preference. Deliberately above the `kBalance` tie-noise (≤ 0.01) so the steering wins ties, and far below `base_cost_non_top` and any real congestion overflow, so it never overrides capacity. Set `0` to restore the legacy highest-metal tie-break for short segments. Measured (corpus): `rnr/mix` abstract WL −5.4% and residual NUTS overlaps 3→1; `tc3a_flat`/`b4_bus_077` WL −0.3/−0.7%; flows with one TOP layer per direction byte-identical. |
| `kPeak` | `0` (**off**) | Routability-aware selection (opt-in; wishlist-planner *"Selection basis"* lever 1). The `kCong` term above is **overflow-only** — zero for any band below capacity — so candidate ranking cannot tell a band others filled to 95% from an empty one until it bursts. `kPeak` adds `kPeak × peak_util`, where `peak_util` is the maximum **existing** fill fraction (`usage/cap`, pre-charge) over the bands the segment would use — and the same term joins the slide-window **band choice** (`best_band_perp`), so the charge itself moves to the emptier band rather than merely pricing the legacy nearest-band pick. A candidate squeezing into an already-loaded corridor now loses to one that detours around it **before** any overflow. Pre-charge deliberately: post-charge utilization was measured and rejected — on an uncongested design it degenerates into an intrinsic "narrow channel" penalty that biases against the column channels the `BITRUNK` datapath trees use (datapath WL regressed across the sweep). `peak_util` also carries an **absolute-supply floor**: `usage/cap` alone is relative, so a band whose track pattern (or a region override — invisible to the width capacity model) leaves too few real SIGNAL tracks to host the bus at all reports util=0 and looks maximally attractive; when the layer has a `def_track_pattern`, the band's span-wide supply (`count_signal_tracks_in_span` — the same override/keepout-aware pool DetailedNUTS places from) is checked against the bundle's bit count and util is clamped to ≥ 1 on a shortfall — an empty-because-unroutable band never ranks better than a full one (works in BOTH capacity modes; the planner receives the routing grid even in width mode). The floor's SHAPE follows the comparison scope: the segment score uses a flat 1.0 (values above 1 would leak into topology/layer competition — measured and rejected), while the intra-segment band choice prices a shortfall proportionally (`needed/supply`), so among bands that ALL fall short the charge and NUTS anchor steer to the least-impossible one instead of tying at the window centre. Measured at `kPeak 0.1` on the congested corpus (with the floor): `channel_stress` heals its 3 keepout-open bits from 0.05 up at **zero** overlap cost; `rnr/mix` pre-heal DNUTS unplaced 190→**86** (−55%); trunk-dominated `tc3a_flat` stays clean at every tested value (the floor removed the old 0.2 regression). **Not a safe default (decided 2026-07-11, stays opt-in)**: on `big2`'s plain pipeline the knob still trades 0 DNUTS opens for **~116** at every tested value. The mechanism is NOT absolute supply (the stranded trunks' windows hold 153/93 real tracks for their 60/56 bits — the floor never fires): it is the **pre-charge horizon** — a wide bundle plans early against a nearly-empty map, later bundles' intervals pile into the same window, and DetailedNUTS's all-or-nothing reservation strands the widest late-processed segment. No per-bundle term evaluated at plan time can price arrivals that come after it; this is intrinsically a feedback problem, and `negotiate_congestion` + `ripup_reroute` indeed heal big2 back to 0 overlaps / 0 opens. Even then the healed endpoint is flow-dependent: `mix` + kPeak 0.1 heals to 0 overlaps / **16** opens vs 1 / **0** for the baseline (with mix's configured negotiate/ripup budget) — so treat the knob as a per-design tuning option to be validated with `check_design`, not a blanket recommendation. Default `0` skips the term (and the floor) entirely — existing flows are bit-identical. Full experimental record: [docs/internal/kpeak_measurements.md](../internal/kpeak_measurements.md). |
| `refine_passes` | `0` flat / **`1` hier** | Post-commit refinement passes — the hier level-ordering synthesis ([congestion_planner.md](../congestion_planner.md) → *"Level ordering"*). Pass 1 of the planner runs exactly as always (top-down, cell-interior reservations protecting later locals); each refinement pass then revisits every committed, unlocked, un-pinned bundle **deepest-first** against the now-REAL usage of everyone else (reservations all released). Acceptance is **strictly-better-than-keeping**: rip up, score the best plan keeping the old topology vs the unrestricted STRICT best on the same state, adopt only on a strict score improvement — otherwise restore exactly (adopting any replan was measured and rejected: hbundles/10 accepted 23 score-equal lateral moves and went 7→78 DNUTS opens). Fixpoint early-out when a pass changes nothing; `[refined]` log tag per adopted move and a per-pass summary line. Measured (hier corpus): `hbundles/01` WL −21% and `02` −32% (phantom-reservation Z detours straighten to I_H), `05` opens 47→32 at 1 pass / **47→8** at 2, `10` heals 1 overlap / 7 opens → **0 / 0**; everything else including `mix2_fast` is a byte-identical no-op. Works in both flat and hier planning. **Defaults (decided 2026-07-12, see [refine_passes_default.md](../internal/refine_passes_default.md))**: `run_planner hier` (and every hier planner site, incl. ripup's fallback replans and the bottom-up template planner) defaults to **1** — the whole hier corpus measured wins or exact no-ops, `mix` heals 1/0 → 0/0 with its heal loops converging 9× faster; the flat `run_planner` stays at **0** (big2's plain pipeline regressed 0 → 60 opens under refinement — the pre-charge-horizon class). An explicit `set_planner_param refine_passes <n>` — including `0` — always wins over the hier default. |
| `nontop_dead_span_gate` | `0` (**off**) | NON-TOP dead-span gate — the planner-side half of the NON-TOP/LOW stub-open bug ([opens.md](../internal/opens.md) item 4, [wishlist-planner.md](../internal/wishlist-planner.md)). `score_segment`'s per-cut capacity samples a non-TOP stub's endpoint-CLAMPED extent (`for_each_band` treats the in-cell tail as pin access on another layer), so a pin-access stub whose in-cell span sits over a leaf keepout on a LOW layer can pass the width/track check yet land on a band with **zero** keepout-clear signal tracks across the span DetailedNUTS places from — a guaranteed open (bigHalf's `b38/b42/b65/…`, flow 10's M7 stubs). When set, a NON-TOP layer whose abstract span has 0 keepout-clear tracks in the chosen band is refused, so STRICT escalates to a TOP layer that can host the bits (uses the exact `count_signal_tracks_in_span` pool DNUTS reads). Measured: **bigHalf no-rr DNUTS unplaced 566 → 135 (−76%)**, no new overlaps. **Off by default**: `span_pool == 0` over the *conservative* abstract span cannot distinguish a genuine cull from a survivor whose final junction-adjusted span clears the keepout, so an always-on gate that helps bigHalf regresses `rnr_mix`'s healed endpoint (0 → 16) by over-escalating survivors onto TOP. The always-on discriminator (keepout-covers-the-whole-routed-extent vs partial — a post-placement-aware predictor) is the follow-on in wishlist-planner. TOP layers are exempt. |
| `charge_pull_target` | `0` (**off**) | Honest-books mode (wishlist-planner *"Charge pulled segments at their predicted pull target"*). The planner charges each segment's demand at a chosen band (`seg_perp`), but NUTS's placement preference chain lets pull/face semantics OUTRANK the charged band for pulled segments — so the books say one place and the metal lands in another (bigHalf: **141/185** pulled segments placed >100 units from their charged band, worst Δ3378; big2 123/148 — the majority, not a corner case; a `[NUTS] books-vs-metal` diagnostic line now reports this after every `run_nuts`). When set: **(1)** a pulled segment's charge and scored bands anchor at its **deterministic predicted pull target** — the slide-window bound in the pull direction, tightened by an in-travel `ConnSeg::pull_break` (the #315 breakpoint), bus-width clamped per layer; **(2)** `ripup_reroute`'s `band_occupants` victim ranking follows the **placed** positions (the session passes an overlay), because contention fallback can move metal off even an honest prediction and a plan-based ranking would miss the bundle physically holding the contended bands. Measured at knob-on (this container): divergence bigHalf 141→22 (−84%), big2 123→53, b44 4→1 (residual = alignment-sibling/contention placements no static prediction sees); endpoints **mempool_tile WL 494422→83223 (−83%) with overlaps 61→27 and opens 2971→1696**, bigHalf WL −6.1% with overlaps 3→2, hbundles/10 −2%, mix/channel_stress ~neutral; plain no-healer pipelines shuffle opens (big2 48→268 pre-heal) and the standard healers absorb everything (big2+negotiate+ripup **0/0**, bigHalf+healers **0/0**). **Off by default**: selection reshuffles can trip residual model gaps on healer-less flows — `comprehensive_demo` picks a WL-better set (−3.3%) whose b3 MST leg lands 1 bit on the keepout via the **junction-anchored** placement preference (`pull=0`, not covered by the charge prediction; the follow-on) — so the mode ships opt-in until the junction-anchored variant is predicted too. **Level 2** (`set_planner_param charge_pull_target 2`, follow-on (a)) adds the junction prediction: **(a1)** a single-rider segment's charged band clamps into its rider's along-extent (the NUTS anchor rule mirrored), and **(a2)** a STRICT **dead-band gate** over junction-extended spans — each segment's span extended to every pulled partner's deterministic predicted track, refused only when the extension crosses a **zero-capacity (keepout-carved)** band the metal physically cannot exist in (`span_hits_dead_band`). Two stronger forms were measured and **rejected**: charging the full extension (mix healed 0→2 overlaps, big2 WL +13%) and gating on any-overflow (mix 2 ov / 21 opens) — only physical impossibility gates, never load pressure. Measured at level 2: **comprehensive_demo heals to 0/0** (the b3 keepout strand — a 35-unit nominal MST stub scored clean while its pulled trunk's predicted track stretched it 200 units across the M4 keepout), big2 plain opens 268→**60**, b44/mempool/hbundles-10/channel_stress unchanged; mix 2 ov / 21 opens and bigHalf 3/560 (vs level 1's 0/26 and 2/392) — per-design trade-offs, validate with `check_design`. Note level 1's own mix endpoint (0 ov / 26 opens vs 0/0 off-knob) is a level-1 property (the occupant-overlay reshuffle), not from level 2. |
| `kWLSpread` | `-1` (**off**) | Realization-risk wirelength penalty (opt-in; the `flow/big_data_test/b44.buda` mis-ranking). The `kWL` term scores each candidate's **nominal** segment-sum, but NUTS realizations wander within the candidate's slide/span DOF envelope `[wl_lo, wl_hi]` (the interval `dump_topologies`/`report_wl` show; corpus: routed WL sits at fill mean ~15%, median 9% of the envelope over 290 bundles). A **wide-envelope** shape — many slide-coupled segments, e.g. a TRUNK+MST tree whose stubs each slide to their own local preference and stretch the trunk between them — realizes far *above* its nominal, while a tight 2-seg shape realizes at or below it: b44's 52-bit multicast picks a 6-seg `TRUNK_H+MST` (nominal 3510, envelope [3510..12160]) that realizes 4510/bit over a 2-seg `TRUNK_V` (nominal 4010, [3510..5010]) realizing 3715/bit — the nominal ranking inverts the true one. When set (recommended `0.125`), the session stamps every candidate's envelope onto the topology (`wl_lo`/`wl_hi`, recomputed fresh each `run_planner`) and the scored WL becomes `nominal + kWLSpread × (wl_hi − wl_lo)` — the base stays the nominal, so same-spread candidates keep today's exact ordering; only realization-risky shapes are demoted. An envelope-point **replacement** (`wl_lo + fill × spread`) was measured and **rejected**: switching the base to `wl_lo` erases genuine nominal differences and reshuffles near-ties corpus-wide (big2 +27% WL, opens 0→252). Measured at `0.125`: **b44 detailed WL −19.6%** (the planner un-pinned picks a better candidate than the flow's hand-pin), **mempool_tile −46.5%** with overlaps 61→27 and DNUTS opens 2971→2038, `mix` −0.1% (clean), `hbundles/10` −0.5%. On the plain no-healer pipelines the selection shuffle can surface DNUTS opens (big2 0→60, bigHalf 288→396 in its no-rr config) — the standard healers absorb it completely: big2 + `negotiate_congestion` + `ripup_reroute` ends **0 opens / 1 overlap** (baseline 0/5) at +2.1% WL, bigHalf + healers ends **0/0**. `0.25` was measured too aggressive (mix's healed endpoint 0→16). Off by default: no annotation pass runs and existing flows are bit-identical. |

**Example:**
```
set_planner_param kCong 2.0          # stronger congestion avoidance
set_planner_param kSpan 0.005        # stronger span preference
set_planner_param base_cost_non_top 0.1
set_planner_param kWL 0.01           # stronger preference for short routes
set_planner_param kBalance 0.0       # disable TOP-layer load balancing
set_planner_param kHeight 0.0        # legacy: short stubs float to the highest metal
set_planner_param kPeak 0.1          # opt-in: steer off already-loaded bands
```

---

### `run_planner`

```
run_planner [<iterations>] [signal_tracks]
```

Runs the global congestion-aware router. Bundles are processed widest-first
(fattest-first greedy). For each bundle:

1. Builds a Hanan-grid congestion map (one cut per channel per layer).
2. Scores every topology candidate — for each segment independently selects
   the best layer from the direction-appropriate set (H layers for H segments,
   V layers for V segments).  Segment score = `kCong·overflow/cap + kSpan·excess
   + base_cost_non_top·min(1, seg_span/base_span_ref) + kBalance·load_ratio
   + kHeight·height_rank·max(0, 1 − seg_span/base_span_ref)`,
   where `overflow = max(0, usage+eff_width−cap)` (zero when the segment fits) and
   the non-TOP penalty scales with segment span so short stubs offload to lower
   layers cheaply while long trunks stay on TOP (see `set_planner_param
   base_span_ref`).  The `kBalance` term spreads load across same-direction TOP
   layers: `load_ratio = layer's committed load / max same-direction layer load`
   (TOP layers only), so an otherwise-tied segment prefers the less-loaded TOP
   layer instead of always the highest metal (see `set_planner_param kBalance`).
   The `kHeight` term is its short-segment complement: a short stub also
   prefers the **lowest** same-direction TOP layer (each height rank up is a
   taller via stack), while long trunks pay nothing and keep the TOP-most
   preference (see `set_planner_param kHeight`).
   With `kPeak` set (opt-in, default 0 = term skipped), each segment
   additionally pays `kPeak·peak_util` — the maximum *existing* fill
   fraction (`usage/cap`) over the bands it would use — so selection steers
   off nearly-full bands *before* they overflow.  `peak_util` is floored at
   1 when the band's real span-wide signal-track supply (pattern overrides
   and keepouts included) cannot host the bundle's bit count: an
   empty-because-unroutable band never ranks better than a full one (see
   `set_planner_param kPeak`).
   The congestion charge goes to the cheapest Hanan band the segment's slide
   interval can host the bus in (slide-aware lookup), not just the band at the
   interval centre.  Band capacity is clamped to the slide window's overlap
   with the band: demand confined to a sub-band window (slide bounds are
   usually not Hanan lines) is not priced against the whole band.
   The effective bus width per layer uses the measured per-bit channel cost
   when a track pattern is defined (`bits × unit_pitch/n_signals`, see
   `def_track_pattern`); otherwise the density model (`width × dilution`).
   Topology score = maximum segment score (weakest-link) `+ kWL·wirelength`.
3. Selects the topology with the lowest score. Ties broken by candidate index
   (shortest wirelength first, since candidates are sorted).
4. Commits the winning topology's per-segment layer choices to the running
   congestion state so subsequent bundles see the correct congestion.
5. Applies any architect-pinned selections from the `.json` sidecar file
   (see `visualize_topologies`): for a pinned bundle, only that one topology
   is scored (layer assignment is still computed).

**Overflow is a hard constraint** — an overflowing band cannot physically host
the bus, so NUTS would emit a real overlap. Each bundle walks an escalation
ladder (see [congestion_planner.md](../congestion_planner.md) for the full design):

1. `STRICT` — only candidates that fit their slide windows **and** are
   overflow-free compete on the soft costs above.
2. **Rip-up & replan** — if no candidate is overflow-free, earlier-committed
   bundles are ripped up one at a time, ranked by the demand they hold on the
   failing bundle's contended bands (the actual blocker first; zero-overlap
   victims skipped), and the pair is replanned; accepted only if both end up
   overflow-free.
3. `ALLOW_OVERFLOW` — overflow truly unavoidable: the least-cost candidate is
   committed with a `WARNING`.
4. `BEST_EFFORT` — no candidate even fits its slide windows (e.g. stale sidecar
   pins): committed anyway with a `WARNING` rather than dropping the bundle.

| Argument | Type | Default | Description |
|---|---|---|---|
| `iterations` | int | 5 | Reserved for planned PathFinder-style negotiated-congestion iterations (see [future/planner_ripup_extensions.md](../future/planner_ripup_extensions.md)); currently unused beyond the first pass. |
| `signal_tracks` | keyword | off | Charge band capacity in **signal-track count** instead of layout width (see below). |

**Signal-track band capacity (`signal_tracks`).** By default a Hanan band's
capacity is its geometric layout width. But DetailedNUTS places each bit on a
discrete SIGNAL track from the layer's `def_track_pattern` (power/ground/clock
slots are not usable), and the placeable count is a quantized fraction of the
width that also depends on the pattern's origin/phase and any `add_grid_override`.
So the planner can commit a bundle `overflow=0` while the band is actually short
of signal tracks → a silent DetailedNUTS open. Adding the `signal_tracks` keyword
makes the planner count the discrete SIGNAL tracks each band holds (honouring the
grid's keepouts, exactly as DetailedNUTS will) and charge capacity in track units,
so the shortfall surfaces as **overflow at planning time** and the escalation
ladder below (STRICT → rip-up/replan) avoids it up front.

- Works on both `run_planner` and `run_planner hier`, e.g. `run_planner 5 signal_tracks`
  / `run_planner hier 5 signal_tracks`.
- **Opt-in:** without the keyword the plan is byte-identical to before. Requires at
  least one `def_track_pattern` — requesting `signal_tracks` with none defined is a
  **hard error** (exit 1), not a silent fall-back to the width model, so a
  misconfiguration can't quietly plan with a different capacity model than asked
  for. Layers *individually* without a pattern keep the width model even when the
  keyword is on (only a stack with **no** pattern at all is the error).
- `set_planner_param track_cap_slack <n>` grants `n` extra signal tracks per band
  as a quantization slack (default 0 = exact integer accounting).
- Measured on `flow/rnr/mix.buda` (hier): the width plan yields 236 DetailedNUTS
  opens; `run_planner hier 5 signal_tracks` yields **162** with no `ripup_reroute`,
  because the planner stops over-filling capacity-short bands.
- The slide-window sub-band clamp counts tracks within the clamped interval too,
  so it is exact end-to-end. See `docs/internal/planner_signal_track_capacity.md`.

**Output:** Prints per-bundle selection: topology type, assigned per-segment
layers, ` [pinned]`/` [replanned]` tags, and the raw overflow in layout units
(0 unless a fallback mode committed). Rip-ups print
`[Planner] Rip-up: replanned bundle <P> to free capacity for bundle <B>:`
followed by the victim's new selection line; fallback modes print
`[Planner] WARNING: Bundle <id>: no overflow-free candidate (even after
rip-up); …` or `…: no candidate fits its slide windows …` respectively.

**Side effects:**
- Creates a `GlobalRouter` object accessible to `visualize` for congestion
  overlay drawing.
- Reads and applies `<script>.json` sidecar if it exists.

**Example:**
```
run_planner 5
```

---

### `run_planner hier`

```
run_planner hier [<iterations>] [signal_tracks]
```

Hierarchy-aware variant of `run_planner` for the HBundle pipeline. Requires an
open BDB, `run_hier_bundler`, and `generate_hier_topologies`. Steps:

1. Applies architect-pinned sidecar selections to the pre-expansion bundles.
2. **Expands** each cell-level HBundle into one wrapper per cell instance, with
   candidates offset to absolute coordinates by the instance origin.
3. Assigns each wrapper `priority = -(level·10000 + n_candidates)` and runs the
   congestion planner sorted by `(priority DESC, width DESC)` — depth-0 globals
   first, then within each level the least-flexible bundles first.

Two mechanisms manage the local-vs-global competition this ordering creates
(see [HIER_PLANNER.md](../HIER_PLANNER.md) §7 and
[congestion_planner.md](../congestion_planner.md)):

- **Cell-interior demand reservation.** Each expanded cell-local wrapper parks
  its effective bus width as virtual usage on the TOP-layer bands inside its
  instance bbox before planning starts; the reservation is released right
  before the bundle's own turn. Earlier globals avoid a cell-interior band
  only when it cannot hold both bundles — a "leave room" constraint, not a
  keep-out.
- **Per-level summary.** When bundles span multiple depth levels, the planner
  prints a per-level report after planning:

  ```
  [Planner] Level summary:
    D0: 1 bundles  strict:1  layers{M6:1}
    D1: 1 bundles  strict:1  layers{M5:1 M6:1}
  ```

  Stage counts other than `strict:` (`ripup:` / `overflow:` / `best_effort:`)
  flag levels losing the competition or under-capacity regions.

The span-scaled non-TOP penalty (`base_span_ref`) complements both: short
cell-local stubs drop to lower layers when TOP saturates instead of detouring
on TOP, preserving TOP capacity for long global trunks.

**Side effects:** Replaces the session bundle list with the expanded
per-instance wrappers (see `dump_hbundles expanded`); subsequent `run_nuts` /
`check_design` / `visualize` operate on the expanded set.

**Example:**
```
run_hier_bundler depth 1
generate_hier_topologies
run_planner hier 5
```

Demonstrated end-to-end by `flow/hbundles/08_cross_level.buda` and
`flow/hbundles/09_local_global_compete.buda`.

---

### `select_topology`

```
select_topology <bundle_id> <topo_id>
```

Manually pin a specific topology candidate for a given bundle by its numeric bundle ID and topology candidate ID (1-based index). This manually overrides the planner's selection.

If the planner has already run, layer assignment is automatically re-run with
the pin in place (logged with a `[pinned]` marker), so per-segment layers
always describe the pinned topology's segment list. Pins set before
`run_planner` are honored when it runs.

| Argument | Type | Description |
|---|---|---|
| `bundle_id` | int | Numeric ID of the bundle (e.g. `2`). |
| `topo_id` | int | 1-based ID of the topology candidate to pin (e.g. `2` for the second topology). |

**Example:**
```buda
# Pin topology candidate 2 for bundle 2
select_topology 2 2
```

---

### `select_topologies`

```
select_topologies <bundle_ids> <topo_id> [<bundle_ids> <topo_id> ...]
```

Batch pin multiple bundles to specific topology candidates. Bundle IDs within a group can be comma-separated or specify ranges (e.g. `5-9`).

| Argument | Type | Description |
|---|---|---|
| `bundle_ids` | string | Comma-separated list and/or ranges of numeric bundle IDs (e.g. `1,5-9,11`). |
| `topo_id` | int | 1-based ID of the topology candidate to pin for the preceding group. |

**Example:**
```buda
# Pin bundles 1, 5 through 9, 11, and 15 through 19, and 22 to topology 3
# Pin bundles 2 through 4, 10, 12 through 14, 20, 21, and 23 through 30 to topology 1
select_topologies 1,5-9,11,15-19,22 3 2-4,10,12-14,20,21,23-30 1
```

---

---

## Stage 4c — Post-NUTS stub layer reassignment

### `run_planner post_nuts`

```
run_planner post_nuts [top] [V [<short_v> [<long_v>]]] [H [<short_h> [<long_h>]]]
```

Runs a second planner pass **after** `run_nuts` that resolves **channel pin
conflicts** — local stub-on-stub overlaps at block faces that the global
planner cannot predict before concrete track positions are known.

Both V and H directions can be reassigned in a **single invocation**; only one
NUTS re-run is performed regardless of how many directions are specified.

#### The channel pin conflict problem

The global planner (Stage 3) assigns every bundle to a single vertical and
horizontal layer and selects a topology, but it cannot see how many stubs from
adjacent blocks will compete for the same narrow perpendicular interval on the
same layer. When many blocks line up along a channel wall, their stubs pack
into the same Hanan-cell column on M5, exceeding its capacity and causing NUTS
violations.

#### Resolution strategy

For each requested direction, stubs are redistributed across the available
layers for that direction using stub span length as a proxy for routing
distance:

| Stub span (routing-direction extent) | Target layer |
|---|---|
| `< short_thresh` | Lowest layer in scope (e.g. M3, or M5 in `top` mode) — short stubs close to the block face stay on the nearest in-scope metal |
| `> long_thresh`  | Highest layer in scope (e.g. M7) — long stubs crossing the full channel use the highest available metal |
| Between thresholds | Unchanged — stays on the planner-assigned layer |

The set of layers in scope is **all** layers of that direction by default, or
only the `TOP` layers when the `top` modifier is given (see below).

After all reassignments, a single full NUTS re-run makes all layers consistent
with the new assignments.

#### Syntax

| Token | Description |
|---|---|
| `top` | Optional leading modifier. Restrict reassignment to the **`TOP`** layers of each direction, so short stubs land on the next-highest TOP layer rather than a `LOW` escape layer. Applies to every direction in the command. |
| `V` | Enable V-stub reassignment. Up to two numeric thresholds may follow. |
| `H` | Enable H-stub reassignment. Up to two numeric thresholds may follow. |
| `<short>` | Stubs shorter than this move to the lowest layer in scope. |
| `<long>` | Stubs longer than this move to the highest layer in scope. |

**`top` modifier.** Without it, a direction's lowest layer may be a `LOW`
layer, so short stubs can be pushed down onto it — and a `LOW` layer cannot
route over a cell, so its bands are often track-starved. `top` keeps the
redistribution within the TOP layers (e.g. V → M5 short / M7 long, H → M4 short /
M6 long), reserving the over-subscribed top metal for long hauls while spreading
short stubs onto the next TOP tier. If a direction has **fewer than two TOP
layers**, that direction is skipped (it never falls back to a `LOW` layer).

**Default thresholds** (used when a letter is given without explicit values):

| Direction | short | long |
|---|---|---|
| V | 80.0 | 200.0 |
| H | 150.0 | 400.0 |

**Bare `run_planner post_nuts`** (no direction letter) → V with defaults (80 / 200). Backward compatible with the previous two-argument form.

#### Notes

- Requires `run_nuts` to have been called first.
- Bundles are classified by the **longest** segment span within the bundle for
  each direction, so all stubs in a bundle move together to the same new layer.
- A single NUTS re-run is performed after all direction reassignments; any
  previous `run_nuts_on_layer` overrides are superseded.
- Thresholds are in layout units. Inspect the NUTS log or use `visualize` to
  estimate typical stub lengths for your floorplan.

#### Examples

```buda
# V only — backward-compatible forms
run_planner post_nuts               # V defaults (80 / 200)
run_planner post_nuts V             # same
run_planner post_nuts V 100 280     # custom V thresholds

# H only
run_planner post_nuts H             # H defaults (150 / 400)
run_planner post_nuts H 120 350     # custom H thresholds

# Both directions in one pass (single NUTS re-run)
run_planner post_nuts V 80 200 H 150 400
run_planner post_nuts V H           # both with defaults

# top mode — keep stubs on TOP layers (no LOW fallback)
run_planner post_nuts top V H               # V → M5/M7, H → M4/M6
run_planner post_nuts top V 100 280 H 150 400
```

#### Typical script pattern (congested channel)

```buda
run_planner 5
run_nuts 2.0
run_planner post_nuts V 100 280 H 150 400   # redistribute stubs to M3/M5/M7
visualize
```

---
