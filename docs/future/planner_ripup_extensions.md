# Future Enhancements: Planner Rip-Up & Overflow Handling

The congestion planner treats overflow as a hard constraint with a single-victim rip-up fallback (see [../congestion_planner.md](../congestion_planner.md)). This document records the known limitations of that design and the planned extensions, in priority order.

---

## 1. Multi-Victim Rip-Up

### Problem

The rip-up loop removes **one** earlier-committed bundle at a time and accepts only if both bundles replan overflow-free. Relief that requires *two or more* earlier bundles to move simultaneously is never found:

```
band X (cap 120):  A (60) + B (60)      ← full
band Y (cap 120):  free
bundle C needs 100 in band X (its only window)
```

Ripping A alone leaves 60 + 100 = 160 > 120; ripping B alone likewise. Only moving *both* A and B to band Y frees band X for C. Today this falls through to `ALLOW_OVERFLOW`.

### Proposed enhancement

Bounded k-victim search (k = 2 initially):

- After all single-victim attempts fail, try **pairs** of victims drawn from the bundles whose usage overlaps the failing bundle's contended bands (see §2 — without that ranking, pair enumeration is quadratic over all committed bundles).
- Replan order: current bundle first (STRICT), then victims in their original commit order; accept only if **all** participants end overflow-free, else exact restore.
- Hard budget on total `plan_bundle` calls (e.g. 200) so worst-case runtime stays bounded on large designs.

### Validation

A two-victim variant of `flow/ripup1.buda`: two medium buses split the centre band, a pinned third bus needs the whole band. Assert both victims are replanned and all three plans report `overflow=0`.

**Effort:** medium — the pure-score/commit split (`plan_bundle` / `commit_plan(sign)`) already supports arbitrary rip/restore sequences; the work is victim-set enumeration and budgeting.

---

## 2. Smarter Victim Selection

### Problem

Victims are tried in **reverse commit order** (most recent first). That is a proxy for "cheapest to disturb" but ignores *relevance*: the most recently committed bundle may share no bands with the failing bundle, wasting two full replans per irrelevant victim, while the actual blocker sits earlier in the list.

### Proposed enhancement

When STRICT fails, record the **contended bands**: for each candidate, the (cut, band) pairs where the overflow occurred (already computed inside `score_segment` — needs to be surfaced instead of reduced to a scalar). Rank committed bundles by their `band_usage` contribution to those bands, descending, and try victims in that order. Reverse commit order remains the tie-breaker.

This also gives §1 its candidate pool: only bundles with non-zero contended-band overlap are worth ripping.

### Validation

Construct a flow where the relevant blocker is the *first*-committed bundle among several later irrelevant ones; assert the rip-up log names it directly (and planner runtime/log shows no failed attempts on irrelevant victims).

**Effort:** small-medium — extend `score_segment` (or add a sibling) to return the overflowing (cut, band) set; ranking is bookkeeping.

---

## 3. Negotiated Congestion (PathFinder-Style Iterations)

### Problem

The planner is one-shot greedy with local repair. Order matters: early wide bundles take the cheapest bands with no knowledge of later demand, and rip-up only patches pairwise conflicts after the fact. Classic global-routing practice (PathFinder) instead lets all nets route, then iteratively re-prices shared resources until contention disappears.

### Proposed enhancement

Use the **currently-reserved `run_planner <iterations>` argument**:

- Iteration 1 = today's full ladder.
- After each iteration, for every band that ended over capacity, add a **history cost** `h_band += kHist · overflow/cap`.
- Re-run the whole plan from scratch with `cong + h_band` as the congestion term; bundles that previously won a contended band on greedy order now see it as expensive and detour voluntarily.
- Stop when an iteration ends with zero overflow or `iterations` is exhausted; keep the best iteration (fewest overflowing bands, then lowest total score).

The STRICT gate stays as the *final* arbiter within each iteration; history costs only steer the search. New knob: `kHist` via `set_planner_param`.

### Validation

A flow where greedy order forces `ALLOW_OVERFLOW` today (overflow unavoidable under any single-victim repair) but a different global assignment is clean; assert `run_planner 5` converges to `overflow=0` on all bundles while `run_planner 1` does not.

**Effort:** large — re-running the plan is straightforward (the cut state is rebuilt per `run_planner` already); the work is history-cost plumbing, convergence criteria, and keeping pinned-selection semantics stable across iterations.

---

## 4. Raw-Unit Overflow Pricing in `ALLOW_OVERFLOW` (smaller)

When overflow is genuinely unavoidable, candidates are still compared by `kCong·ov/cap`, which under-weights large overflows in wide bands (16 units over a 120 band prices below 6 units over a 30 band). In the fallback mode, price overflow in **raw units** (`kCong·ov`) so the candidate with the least physical damage — the fewest units NUTS cannot place — wins regardless of band height. One-line change in `cong_cost_segment` gated on mode; validate by asserting the fallback picks the minimum-`overflow=` candidate in a constructed over-subscribed flow.

---

## 5. Layers-Only Replan for Pinned Victims (smaller)

Rip-up replans a victim through the full candidate scan even when its topology is pinned and only its **band/layer choice** can change. A cheap partial replan (skip `ConnTopology` rebuild, rescore only the layer/band loop for the pinned candidate) would make pinned victims nearly free to try, which matters once §1 multiplies the number of replan calls.

---

## 6. Overflow Visualization (smaller)

The planner knows exactly which (cut, band) pairs are over capacity after planning, but the information only surfaces as a scalar `overflow=` per bundle. Expose the post-plan band state (`get_cuts()` already exists) and add a visualizer overlay highlighting overflowing bands in red — making `ALLOW_OVERFLOW` commitments visible on the floorplan instead of only in the console. Pairs naturally with the existing congestion heatmap ([../congestion_heatmap_logic.md](../congestion_heatmap_logic.md)).
