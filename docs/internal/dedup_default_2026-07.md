# `set_dedup_loci` default-on — MEASURED, keep opt-in (2026-07-22)

Investigation of the wishlist item ["Promote `set_dedup_loci` to
default-on"](wishlist-topo.md), motivated by the [`set_drop_dangling` modes
corpus sweep](drop_dangling_modes_2026-07.md), which showed `dedup` as a
runtime win at neutral-to-better QoR — but flagged two `hbundles` regressions
as the gate.

**Verdict: do not flip. Keep `set_dedup_loci` opt-in.** Under the real shipped
planner config (the `kSegsRel` 0.02 healer-gated default, active whenever a flow
runs a healer), dedup's apparent wins largely evaporate and its runtime effect
flips sign across flows. The sweep's wins were an artifact of the measurement
harness suppressing `kSegsRel`. This mirrors the `multi_trunk` "measured, keep
opt-in" decision.

## Step 1 — the two regressions are NOT a representative-choice problem

The wishlist hypothesis was that dedup discards the member the planner would
have picked, fixable by a realization-aware representative. **Ruled out by
measurement.** On `hbundles/06` (opt-in dedup, 6 bundles flip their selected
topology), the base-selected candidate **still survives the dedup pool in every
flipped bundle** — the planner had the same candidate and chose differently.

Root cause: the planner charges congestion bands by each candidate's **nominal
perpendicular coordinate**, but dedup's equivalence key is the **slide window** —
a NUTS-*realization* equivalence, not a planner-*charging* one. b44's canonical
group `TRUNK_H@{y10830,y11330,y11830}` (all on window `[10830,11830]`) is three
*different nominals* → three *different bands*. Collapsing them removes distinct
band-spreading options the greedy planner uses; across many bundles that shifts
the whole congestion equilibrium, so even a bundle whose own pick survives gets
crowded onto a worse choice. This is intrinsic — dedup's *value* IS collapsing
different-nominal variants, which are exactly the planner's spreading options, so
**it cannot be both valuable and planner-neutral.** (`charge_pull_target` 1/2,
which charge the realized band, does not cure it either.)

## Step 2 — the regressions heal, suggesting a healer-ahead gate

The collapsed pool converges to the same clean endpoint as base once a healer
runs: `hbundles/06` 4/48→13/58 heals to **0/0** (= base healed); `hbundles/07`
0/11→2/2 heals to **0/0** with `ripup_reroute 20`. So — like `kSegsRel` — dedup
looks worse pre-healer but is absorbed by a healer, suggesting a healer-ahead
default (dedup on iff a healer is in the flow, off for healer-less flows whose
plain endpoint is the deliverable). A prototype gate (`_healers_in_flow`,
env `BUDA_DEDUP_LOCI` override) was built and passed fast+mid (1674) — because
the golden/most-unit harnesses are scriptless, so the gate reads no healer and
dedup stays off, bit-identical.

## Step 3 — the decisive isolation: it's confounded by `kSegsRel`

The prototype's first corpus pass *looked* like a Pareto win. It was a
**measurement confound**: the healer-gated dedup path sets `script_path`, which
ALSO activates the `kSegsRel` 0.02 default and the `run_nuts` dead-span
escalation (both `_healers_in_flow`-gated). Re-running with `script_path` set on
**both** sides — so `kSegsRel`/dead-span are active in both and only dedup
differs — isolates dedup's true effect:

| flow | healer | dedup off | dedup on (gated) | note |
|---|---|---|---|---|
| `bigHalf` | ✓ | 0/0 | 0/0 | endpoint-neutral |
| `slowdown_rnr` | ✓ | 0/0 | 0/0 | neutral (the sweep's 0/32→0/0 was **kSegsRel**, not dedup) |
| `mix` | ✓ | 0/0 | 0/0 | neutral |
| `mix2` | ✓ | 2/42 | 4/20 | **mixed** — opens ↓, overlaps ↑ |
| `mix2_fast` | ✓ | 33/256 | 33/256 | no change |
| `hbundles/06`, `/07`, `big2_noviz` | – | — | identical | gated off |

`ov/un` = NUTS overlaps / DNUTS opens. The dramatic sweep "wins"
(`slowdown_rnr` 0/32→0/0) were `kSegsRel` engaging via `script_path`, not dedup.
Isolated, dedup is endpoint-neutral on 4/5 healer flows and **mixed** on `mix2`
(fewer opens, more overlaps — not a clean improvement on a checked-in flow).

## Step 4 — runtime also flips sign

The remaining case for a default was runtime (smaller pool → faster
planner/healer). Measured under the real config (`script_path` set both sides):

| flow | dedup off | dedup on | Δ |
|---|---|---|---|
| `bigHalf` | 8.1s | 49.2s | **+510%** |
| `mix2` | 28.3s | 4.8s | −83% |
| `mix` | 14.8s | 6.5s | −56% |

Dedup makes `bigHalf` **6× slower**: with `kSegsRel` already cleaning it (base is
8s, not the sweep's 84s), the deduped pool removes band-spreading options and the
healer thrashes far longer to reach the same 0/0. The runtime effect is
flow-dependent, not a consistent win.

## Why the sweep and the reality disagree

The [modes sweep](drop_dangling_modes_2026-07.md) harness builds sessions
without `script_path`, so `kSegsRel` (and dead-span escalation) were **suppressed
on both its base and dedup sides** — a fair dedup-only comparison, but in a
planner regime that does not ship. dedup and `kSegsRel` chase the same
congestion headroom (candidate/selection diversity vs. per-segment pricing), so
once `kSegsRel` is the default, dedup is mostly redundant and occasionally
counterproductive (bigHalf). The sweep's dedup column measured dedup *standing in
for* `kSegsRel`, not dedup *on top of* it.

## Recommendation

- **Keep `set_dedup_loci` opt-in (default off).** No code change; the knob stays
  a per-flow expert tool — genuinely useful on flows like `mix`/`mix2` (faster,
  fewer opens) where the user opts in, harmful on `bigHalf`.
- A default-on flip would need a NEW justification measured *with `kSegsRel`
  active on both sides* (this doc's isolation), not the scriptless sweep — and
  the `mix2` mixed change and `bigHalf` 6× slowdown are blockers, not neutrals.
- If the pool-persist size (not planner QoR) is the real motivation for large
  hier designs, measure that directly against the BDB persist path
  ([`wishlist-bdb.md`](wishlist-bdb.md)) — it is independent of the planner
  regime and may still justify a persist-time (not generation-time) dedup.

Harnesses (measurement host, this session):
`scratchpad/dedup_validate.py` (QoR isolation), `scratchpad/dedup_time.py`
(runtime). Corpus numbers are host-sensitive (`-march=native`); trends, not
absolutes, are the result.
