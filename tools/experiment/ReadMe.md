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
| `phantom_charge.py` | Does the same-bundle double charge inflate what *other* bundles see? |

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

Two questions, cheapest first, and the first can refute the whole thing:

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
    affected band charges                 13
    ...landing on a band OVER capacity     0

Four instances in 555 committed bundles, none on an overflowing band. So the
effect on other bundles is bounded to nothing measurable here — and the reason
is the same fact `base_rate_collinear.py` found from the other side: the shape
lives in candidates that LOSE. A duplicate inside a losing candidate is charged
into a scoring overlay and dies with it; it never reaches the committed field.

What is deliberately **not** claimed: the per-instance magnitude. The script
prints a per-band ratio and labels it an upper bound loose in both directions
(the whole charge on one band though `for_each_band_w` spreads it by a weight
≤ 1; the raw cap as denominator though the engine adds a `track_pitch` margin).
It comes out above 1, which means the bound is uninformative — not that a band
is several times over. The incidence is the finding; the ratio is not.

Two honest limits, either of which would need the next step:

- These are **final** committed states. The planner is greedy and widest-first,
  so a band could have been tight mid-run and relaxed by the end; "0 over
  capacity now" is not quite "never mattered".
- Settling that means recomputing STRICT feasibility against `usage − phantom`
  and counting candidates whose verdict flips. The natural lever is blocked:
  `inject_band_demand` guards `amount <= 0.0` and returns, so the phantom cannot
  be subtracted through the existing API. Relaxing that guard (floored at zero)
  is the cheap way in, and is preferable to reimplementing `for_each_band_w`'s
  spreading in Python, where a subtle mistake yields a confident wrong answer.

Not worth it for four instances. Worth knowing where the door is.
