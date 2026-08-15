# `tools/experiment/` — one-question measurement scripts

Instruments written to answer ONE question about the engine and then keep
answering it. They are not part of any flow and nothing imports them: each is a
`python3 tools/experiment/<name>.py` you run when you want the number.

They differ from `tools/` proper in intent. `qor_corpus.py` and `qor_table.py`
are *gates* — they answer "did this change help or hurt" on a fixed metric.
These answer "is the thing I believe about the engine actually true", usually
once, in support of a specific decision, and the value of keeping them is that
the next person can re-run the measurement instead of re-arguing it.

Every script here is **read-only**: it sources flows, inspects the resulting
session, and prints. None mutates a flow, a golden, or a BDB.

| script | question |
|---|---|
| `base_rate_collinear.py` | Is `SELECTED+REDUNDANT == 0` surprising, or is it what chance predicts? |
| `twin_cost_collinear.py` | Could trimming a redundant stub ever have flipped the selection? |
| `phantom_charge.py` | Does the same-bundle double charge inflate what *other* bundles see, and by how much on which band? |

## The collinear-stub pair

Both were written for one hypothesis: that redundant collinear stubs
(`tools/scan_collinear_stubs.py`, entry 5c in
[antenna_repros.md](../../docs/internal/antenna_repros.md)) are never selected
*because* the planner over-charges them — the duplicate metal adds wirelength
and its band demand is charged twice, since `wirelength()` sums segment lengths
with no overlap dedup and `apply_segment` charges each segment independently.

Both mechanisms are real in the code. Neither turned out to be the explanation.

**`base_rate_collinear.py`** measures the null. Selection is rare, so "zero
selected" needs a base rate before it means anything. Two Poisson-binomial
models: uniform over the bundle's pool (**A**), and uniform over the candidates
of the same *class* as the actual winner (**B**), which controls for redundancy
concentrating in classes that lose for structural reasons.

    bundles with a selection : 621
    OBSERVED selected+redundant : 0
    NULL A : expected 4.55 +/- 1.83   z = -2.49
    NULL B : expected 0.00 +/- 0.00

Null B is exactly zero: in no bundle does any candidate sharing the winner's
class carry redundancy. The zero is a placement fact, not a scoring one — and
the by-class table localizes it further, to `TRUNK_H` (109 of 3388) and
`TRUNK_H_OOB` (41 of 595), with every other class at zero.

**`twin_cost_collinear.py`** asks whether the cost could have mattered anyway.
The planner scores `max_over_segments(SegCost.total) + kWL * wl_est`, so
trimming helps through three channels: the WL drop, the argmax falling to the
runner-up, and the vanished double charge. The third can't be read off one
scoring pass, so it is **bounded** rather than estimated — congestion is
non-negative, so

    cost_after >= kWL*(wl_est - len(S)) + ksegs*(n-1) + max_over_remaining(total - cong)

is a floor no double-charge relief can go under. The WL term's components are
separated by a per-bundle least-squares fit rather than assumed: taking `kWL` as
`wl_term/wl_est` folds the segment-count penalty into the WL rate, which
overstates the post-trim cost for a short stub and stops the floor being a lower
bound at all (Codex #745 — it moved 2 of 150 across the line). Where the floor
still exceeds the winner's cost, trimming provably cannot flip the candidate:

    128 of 150 : flip PROVABLY IMPOSSIBLE
    median gap to the winner 2.24  vs  median saving 0.67

The saving closes about a third of the gap it would have to. `seg_cost` being a
**max** is why: a short stub is almost never the argmax, so the double charge is
largely muted in the candidate's own score — it inflates the committed usage
*other* bundles see, which is a different effect.

Costs are read in the final committed state, not the greedy decision-time one.
That does not weaken a "provably impossible" verdict (the final-state winner
cost is ≥ its decision-time cost, so a floor clearing it clears the earlier one
too), but it does mean a negative gap is **not** evidence of a mis-selection —
it is ordinary greedy ordering plus healer movement.

Where it did lead: the by-class result pointed at `add_trunk_h`, which passes
`suppress_stubs=false` while `add_trunk_v` passes `true` — so `TRUNK_V` carries
no redundant pairs because its suppressor already removes them. That gap is what
`set_trim_trunk_stubs` closes.


## The double charge, and who it actually reaches

`twin_cost_collinear.py` closes with a loose end: the double charge is muted in
the candidate's *own* score because `seg_cost` is a max, but it still inflates
the committed usage **other** bundles read. That is a different question, and
`phantom_charge.py` is the one that answers it.

The stakes are higher than a cost nudge. A committed bundle's charge persists in
`cuts_`, and later bundles read it through `cong_cost_segment`, `kPeak`, and —
above all — OVERFLOW, which is a hard STRICT constraint. Phantom demand can make
a band look full and push somebody else's candidate out of the STRICT tier
entirely, which is a discrete harm, not a matter of degree.

Three questions, cheapest first, and the first can refute the whole thing:

**P0, the premise.** NUTS *may* let same-bundle segments share a track; that is
not the same as it doing so. Measured:

    co-placed (one wire charged twice)      3   (75%)
    placed APART (charge correct)           1   (25%)

The mechanism is real — but that fourth pair matters just as much. NUTS put it
on two different tracks, so two charges was the *right* answer. A blanket
"dedup same-bundle charge" fix would be wrong.

**P1, the census.** How often does this reach the geometry other bundles can
actually read?

    committed bundles                    555
    committed segments                  1566
    coincident pairs                       4   (0.2554% of segments)

Four instances in 555 committed bundles — and the reason is the same fact
`base_rate_collinear.py` found from the other side: the shape lives in
candidates that LOSE. A duplicate inside a losing candidate is charged into a
scoring overlay and dies with it; it never reaches the committed field.

**P2, the magnitude.** Rarity is the whole defence, so it is worth knowing what
one instance costs when it does land:

    flow          cut band   M   phantom       cap     usage  fill%   eaten  bundle
    big.buda      247   44   5    127.00    310.00    254.00  81.9%   69.4%  8
    big.buda      250   44   5    127.00    310.00    282.00  91.0%   81.9%  8
    big2.buda     112    2   5     64.00   1250.00    374.00  29.9%    6.8%  64

Three bands. **None over capacity, and none that the phantom pushes over** — so
the STRICT harm the question was about does not occur, and there is nothing to
fix. But it is not negligible where it lands: cut 250 reads 91% full when the
metal is really at 50%, and the phantom eats 82% of the room a later bundle
would otherwise have found. So the finding is "three instances, not a class of
problem" — not "the effect is small". One more co-placed duplicate on a tighter
band is all it would take.

Only CO-PLACED pairs reach P2. Where NUTS put the twins on separate tracks the
metal really is two wires, so folding that pair in would manufacture a phantom.

### Where the per-band numbers come from

`CongestionPlanner::committed_charges()`, which reports `charge_log_` — the
record `commit_plan` keeps so a rip-up can subtract exactly what it added.
Nothing is re-derived, and that is the point.

A first cut *did* re-derive, mapping each duplicate onto the cut and band it
charges, and got it wrong four ways (Codex #754): the grid of the **wrong axis**
(`for_each_cut_` passes `is_vcut = is_h`, so an H segment's bands index
`y_grid_` and a V segment's `x_grid_` — the opposite of the obvious reading), no
filter on cut **direction**, an inclusive band test where `find_band` is
**half-open**, and the topology's nominal perp where `commit_plan` charges
through `plan.seg_perp` and may **spread** one segment across several weighted
bands. Those numbers ("13 affected band charges", "0 landing on a band over
capacity") were withdrawn.

Under the greedy `band_span_charge` modes no re-derivation could have been right
at all — they read live occupancy that has moved on by the time anyone asks —
which is why the fix was to ask the engine rather than to patch the Python.
`inject_band_demand`'s `amount <= 0.0` guard was the other candidate door; the
record is better, because it is exact for every mode and mutates nothing.

The accessor is pinned to the engine by an identity: summed per (cut, band) it
must reproduce `GlobalCut::usage` exactly, in both directions (no charge
unaccounted, no band unexplained). `test_phantom_charge_scan.py` holds it on a
**healing** flow as well as a plan-only one, so the rip-up erase and the
`recharge_committed` rebuild are covered rather than just the append. A 0.01%
drift fails it.

`band_phantoms` does only set arithmetic on those records. Its one judgement
call: a group of N coincident segments charging one band pays once, at the
**largest** amount (`sum − max`), which is the conservative reading and counts a
triple stack as two phantom charges rather than three.

Merging is licensed by two facts, one per axis. The shared **placed track**
fixes the position across the span; the shared **cut** fixes it along the span
(a cut lies at one coordinate, so every segment charging it reaches that
coordinate). Same layer, same track, same place along the wire — one piece of
metal.

Keying the class on the placed track rather than the topology's nominal
perpendicular is the fix for Codex #762: being one wire is a *placed* fact, and
the two can disagree. Four segments can share a nominal perpendicular while NUTS
seats them as two pairs on two different tracks — two wires each charged twice,
a phantom of two charges, which a nominal key reports as three. Not reachable on
the current corpus (the three co-placed pairs are in different bundles), so the
table above is unchanged; it is the predicate that was unsound, and the count is
what the whole finding rests on.

One honest limit stands: these are **final** committed states. The planner is
greedy and widest-first, so a band could have been tight mid-run and relaxed by
the end; "0 over capacity now" is not quite "never mattered".
