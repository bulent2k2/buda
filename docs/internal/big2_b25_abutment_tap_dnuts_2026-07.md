# big2 bundle 25 — face-tap/pass-through mis-assignment → `blk_03` open (2026-07)

## What this is

A diagnosed design open in `flow/big_data_test/big2/big2.buda`, surfaced by the
`viol_bundles` column added to `tools/qor_corpus.py` (PR #432): the flow routes
**overlap-free and fully placed** (`overlaps=0`, `unplaced=0`) yet `check_design`
reports

```
Bundle 25: Block 'blk_03': 56 bit(s) — no pass-through/busterm connection
Total: 56 violation(s) in 1 group(s) across 1 bundle(s).
```

i.e. an electrically broken route the `overlaps/unplaced` view reads as clean.
Bundle 25 is `DRV:blk_10 → REC:blk_01, blk_03, blk_11` (56-bit `bus_045`); the
selected candidate is `TRUNK_V@x4145`.

## The shape: two segments carrying four busterm connections

A busterm connection to a block is either a **face-tap** — a segment ENDPOINT
lands on one of the block's faces — or a **pass-through** — a segment crosses the
block's interior with no endpoint on a face. (A pass-through is a busterm too;
the only distinction is the absence of a face-tap.)

`TRUNK_V@x4145` has just **two** segments for **four** endpoint blocks:

```
seg0 V (4145,1425)->(4145,2020) L7   # the trunk
seg1 H (2000,1790)->(4145,1790) L6   # the stub; junction with seg0 at y=1790
```

Block geometry (repo convention **y1=bottom=south, y2=top=north**):

| block | rect (x1,y1,x2,y2) | role |
|---|---|---|
| blk_10 | 3500,1425,4870,2020 | driver |
| blk_01 | 4145,1000,6100,1425 | receiver |
| blk_03 | 3500,2020,4870,2570 | receiver |
| blk_11 | 1250,1790,2000,2720 | receiver |

(Rects are the `add_block` lines in `flow/big_data_test/big2/tc3b_flat_x5.buda`
— the circuit `big2.buda` sources — `blk_01`:4, `blk_03`:6, `blk_10`:13, `blk_11`
alongside; `big2_b4_b24.buda` lists the same rects inline.)

The **intended** assignment for this shape is:

- **seg0 (trunk)** → 3 busterms: face-tap **blk_03** (south face y=2020, top end),
  face-tap **blk_01** (north face y=1425, bottom end), and **pass-through blk_10**
  (the trunk runs up through blk_10's interior at x=4145, y 1425→2020).
- **seg1 (stub)** → 1 busterm: face-tap **blk_11** (east face x=2000). Nothing else.

## Root cause — `annotate_endpoints` picks the wrong block at a shared face

The actual generation annotation (`topo.seg_busterms`, from
`annotate_endpoints`, `topology.cpp:1350`) is:

```
seg0 V (4145,1425)->(4145,2020): start_tap=blk_10  end_tap=blk_10
seg1 H (2000,1790)->(4145,1790): start_tap=blk_11  end_tap=None
→ connected blocks WITHOUT a busterm tap: blk_01, blk_03
```

The driver **blk_10 captured BOTH trunk-endpoint face-taps**, and the two
receivers whose faces those same endpoints land on — blk_01 and blk_03 — got
**no face-tap at all**.

`annotate_endpoints` assigns each endpoint to the **first block, in iteration
order, whose face the endpoint coincides with** (`if (!ep.first.has_value() &&
on_face(...)) ep.first = bt;`), with **no discrimination** between a block the
segment merely abuts from outside (a genuine face-tap) and a block the segment
crosses *into* (a pass-through). At the shared edges:

- endpoint `(4145,1425)` lies on blk_10's south face **and** blk_01's north face
  → blk_10 (iterated first) wins; blk_01 loses.
- endpoint `(4145,2020)` lies on blk_10's north face **and** blk_03's south face
  → blk_10 wins; blk_03 loses.

So blk_10 — which the trunk actually passes *through* — grabs the two face-taps
that belong to the receivers, and having busterm conns it is then booked as a
face-tap instead of the pass-through it really is.

**How the open then manifests (the DNUTS symptom).** With no face-tap, blk_01
and blk_03 survive only as **graze pass-through** coverage — the trunk endpoints
riding their boundary faces (the inclusive `spans_rect` test in
`tighten_passthrough`). `check_topo`/`check_nuts` accept that (the abstract
segment reaches the boundary), but it is **zero-margin**: DetailedNUTS realizes
blk_03 only if the per-bit wires happen to reach exactly y=2020. In the failing
trajectory `seg0`'s 56 bits stop at `span_hi ∈ [1853,1914]` — ~106–167 units
short — so all 56 open; in a luckier trajectory they reach `[2233,2292]` and it's
clean. A real face-tap would have *anchored* the bits to the face (a busterm
along-reach constraint), removing the fragility.

**Secondary — the stub's spurious blk_10 pass-through.** Because the trunk locus
x=4145 is *inside* the driver blk_10, the stub's junction with the trunk sits
inside blk_10 and the stub crosses blk_10 to reach it. blk_10 ends up "covered"
twice — a genuine strict crossing by the trunk **and** a spurious pass-through by
the stub, whose only job is blk_11→trunk.

## The `blk_03` tap-face distribution (why some candidates are robust)

`blk_03` is boxed in on all four faces, so no face is geometrically "free"; what
distinguishes the robust candidates is that they tap `blk_03` on a face adjoining
a block the trunk does **not** pass through, so `annotate_endpoints` assigns the
tap to `blk_03` cleanly (no rival coincident face):

| face tapped | adjoining block | candidates | |
|---|---|---|---|
| y=2570 (top) | blk_09 | 9 | robust |
| x=3500 (left) | blk_18 | 4 | robust |
| x=4870 (right) | blk_22 / blk_29 | 2 | robust |
| pass-through / unset | — | 6 | |
| **y=2020 (bottom, shared with blk_10 — the trunk host)** | blk_10 | 5 | fragile |

## Reproduce

```bash
tools/qor_corpus.py --flows flow/big_data_test/big2/big2.buda    # -> 0/0/1
bin/buda --no-viz flow/big_data_test/big2/big2.buda              # check_design at the end
```

Then inspect bundle 25's `TRUNK_V@x4145`: `seg_busterms` shows both trunk
endpoints tapped to blk_10, blk_01/blk_03 with no busterm; `seg0`'s bits land
with `span_hi` short of y=2020 in the failing trajectory. (The open is
healer-trajectory-dependent — the no-healer `tools/render.py` pipeline places the
bits reaching the face, so it does not reproduce it; only the full flow does.)

## Fix plan

1. **Interior-side discrimination in `annotate_endpoints`** (primary, `topology.cpp`).
   When a segment endpoint `P` lies on a face of block `B`, treat `B` as a
   face-tap **only if the segment's body lies OUTSIDE `B`** — `B`'s interior is on
   the opposite side of the face from the segment (the segment abuts `B` from
   without). A block whose interior the segment *enters* across `P` is being
   crossed → it is a pass-through, not a face-tap, and is left for the
   pass-through pass to cover. Concretely, compare the sign of
   `(other_endpoint − P)` against which side `B`'s interior lies on:
   - `(4145,1425)`, trunk body upward → blk_10 interior above (crossed → skip),
     blk_01 interior below (abut → **tap**).
   - `(4145,2020)`, trunk body downward → blk_10 interior below (crossed → skip),
     blk_03 interior above (abut → **tap**).

   This gives `seg0` the two receiver face-taps and leaves blk_10 to the
   pass-through pass — it strictly crosses blk_10, so it stays covered, now
   correctly as a pass-through. blk_01/blk_03 gain real face-taps, which anchor
   the bits to the face and remove the DNUTS shortfall.

   **Corner case — an endpoint on a block CORNER lies on two faces at once**
   (an x-face and a y-face of the same rect), so the "which side is the segment's
   body" test is ambiguous: the segment is axis-aligned, so it abuts one face
   from outside while running *along* the other. Resolve with an explicit
   tie-break rather than iteration order: prefer the face **perpendicular to the
   segment's travel** (the one the endpoint actually terminates against) — a V
   stub landing on a rect's bottom-left corner taps the *bottom* (y) face, not
   the *left* (x) face it merely grazes. When two *distinct* blocks each abut
   from outside on the same relevant face (genuinely rare), fall back to today's
   iteration order.

2. **Pass-through attribution cleanup** (secondary). A block strictly crossed by
   the trunk should not additionally be booked as a pass-through of a stub that
   only reaches a junction inside it; attribute coverage to the strict coverer.
   Mostly cosmetic once (1) lands — but it can itself shift a block's
   pass-through coverage bookkeeping (and thus `connected_block_names` /
   `check_topo` coverage counts), so it must ride the **same** `qor_corpus
   --compare` sweep as (1), not land unmeasured.

**Alternatives** (weaker; keep as fallbacks): a DNUTS-side fix that guarantees a
graze-covered endpoint's per-bit span reaches the face; or a generation gate that
demotes a candidate tapping a receiver only on a trunk-host-shared face when
alternatives exist. (1) is preferred because it fixes the *cause* — the taps are
simply on the wrong blocks.

**Validation & risk.** `annotate_endpoints` feeds `seg_busterms`, which drives
every downstream slide/anchor/coverage decision, so (1) changes many candidates
corpus-wide. It MUST be measured with `tools/qor_corpus.py --compare` (now
carrying the `viol_bundles` column): the target is bundle-25-class opens dropping
with neutral overlaps/unplaced. Gate behind the sweep; land only if net-positive.

Filed 2026-07-24 (fix deferred to a measured follow-up).

## Measured attempt — interior-side discrimination is NET-NEGATIVE as a blanket rule (2026-07-26)

Fix (1) was implemented and swept. `annotate_endpoints` was changed to a
two-pass per-endpoint assignment: **pass 1** taps a block the segment *abuts from
outside* (endpoint on the block's face perpendicular to travel AND the body
`P→other` points away from the block's interior); **pass 2**, only if nothing
abuts from outside, falls back to the old first-coincident-face rule so a terminus
that merely lands on a face (with no adjoining block) still taps — coverage is
never lost.

**It fixes b25 exactly as designed.** `seg0`'s taps move `blk_10/blk_10 →
blk_01/blk_03` (the crossed driver no longer steals the receivers' taps; it is
left for the pass-through pass), and `big2.buda` heals `0/0/1 → 0/0/0`.

**But the blanket rule is net-negative on the corpus** (`tools/qor_corpus.py
--compare`, 35 flows):

| flow | base | branch | |
|---|---|---|---|
| `big2/big2.buda` | 0/0/1 | **0/0/0** | BETTER (the target) |
| `rnr/slowdown_rnr.buda` | 2/8/1 | **0/0/0** | BETTER |
| `big2/b24_bus_056.buda` | 0/0/0 | 0/48/1 | WORSE (was clean) |
| `big2/big2_b4_b24.buda` | 0/0/0 | 0/108/2 | WORSE (was clean) |
| `big2/big2_noviz.buda` | 2/28/2 | 6/48/1 | WORSE |
| `big2/tc3b_flat.buda` | 2/28/2 | 6/48/1 | WORSE |

→ **2 better, 4 worse, 2 previously-clean flows broken.** Fails the doc's own
gate ("land only if net-positive"), so it was **reverted** — `main` stays
byte-identical.

**Root cause of the regressions — the re-attribution is UNCONDITIONAL.** It
changes *correct* attributions too, not just b25's pathological one. In the
`b4/b24` family (the `io_pad_tr` / `x≈5772` flexible-trunk geometry, see
`test_topo_flexible_trunk.py`), demoting a face-tap on a crossed block to a
pass-through **unlocks pure-pass-through trunk candidates** that were previously
pinned-and-dropped: e.g. the `TRUNK_V@x5772` candidate now passes
`check_topo`/`check_nuts` (segs=2, no violation, not pinched) and the planner
picks it — but it **opens at DetailedNUTS** (`big2_b4_b24`: topo/nuts clean, then
60 bits unplaced). Trading b25's open for new DNUTS-fragile selections elsewhere.

**Refinement direction (for a future surgical attempt).** The blanket
abut-vs-cross rule is too broad. The b25 pathology is specifically *a receiver
left with NO tap because a crossed block stole it*. A surgical fix should
redistribute a tap **only when doing so rescues a would-be-stranded busterm** —
i.e. demote the crossed block's endpoint tap only if a competing block abuts that
same endpoint from outside AND that competitor would otherwise have no face-tap /
pass-through coverage. That keeps b25's win while leaving the already-correct
`b4/b24`-family attributions (and their pinch-dropped pass-through trunks)
untouched. Until such a targeted gate is built and measured net-positive, b25
stays a documented `viol_bundles=1` open. The implementation + baseline/branch
`qor_corpus` JSONs from this attempt are in the issue session notes for reuse.
