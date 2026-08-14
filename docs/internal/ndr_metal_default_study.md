# Should METAL be the default reading for an absolute NDR value?

**Measured 2026-08-14 against `main`. Verdict: NO — keep `metal` opt-in.**
A negative result, recorded with its numbers so the next person does not
re-derive it from the argument alone.

`opens_ndr.md` §2 landed metal-shaped quantization as the opt-in `def_ndr …
metal` token and owed the default flip its own measurement, the path
`kSegsRel`, `spine_relays` and `band_span_charge` took. This is it.
Reproduce with `tools/ndr_metal_study.py` (both halves) and
`BUDA_NDR_METAL=1` for any flow by hand.

---

## The question

`def_ndr r width W` says a governed bit should be W units wide. Path A
realizes that as a whole number of SIGNAL slots, and there are two honest
ways to count:

```
channel:  k = ceil(W / bit_pitch),   bit_pitch = unit_pitch / n_signal_slots
metal:    smallest k with (sum of k slot widths + the k-1 gaps between them) >= W
```

They share no term. `bit_pitch` amortizes the power rails across the signal
slots — the right question for the planner's books, and exactly what
`eff_bus_width` charges, which is why rule and width model agree by
construction. Metal counts neither the rails nor the gaps outside the run —
the right question for a width declared for EM, current density, sheet
resistance or RC.

Metal asks for **≥** as many slots as channel wherever they differ, so the
flip costs demand; and where no run in the period is long enough it is an R3
**refusal**.

## Method, and the one choice that decides the numbers

**Half 1 — patterns.** Every `def_track_pattern` / `add_grid_override` in
`flow/`, `demo/` and `test/`, deduplicated **by content** (the corpus
declares the same stack in many flows; counting once per file would weight a
popular fixture as if it were many technologies) — **35 distinct patterns** —
swept against six declared widths each.

The widths are multiples of **that pattern's own minimum signal slot**, i.e.
the technology's minimum wire. This anchor is the measurement. A physical
rule says "this net needs N× minimum width"; nobody declares a width as a
fraction of the track PITCH, which is a routing-resource quantity that
happens to include the power rails. Anchored to pitch instead, the two
readings disagree almost everywhere **by construction** — metal counts no
rails and pitch does — and the sweep measures the existence of power rails
rather than the choice under study. (Measured both ways: pitch-anchored says
the readings agree on 1.0% of pairs, which is true and useless.)

**Half 2 — flows.** Every vehicle declaring an absolute value — the only
designs the flip can move, since a multiplier is pattern-independent — run
under both readings, with the QoR triple taken from `qor_corpus.run_flow`
so the cost is quoted in the currency the corpus gate uses.

## Half 1 — how often, and by how much

| | pairs | |
|---|---:|---|
| the readings **agree** | 46 | 21.9% |
| metal costs **more slots** | 145 | 69.0% |
| metal is **unrealizable** (R3) | 19 | 9.0% |

Where metal costs more it is **+1 slot per bit in 141 of 145** cases, +2 in
the other 4. It is never cheaper, as the arithmetic requires.

**At 1× minimum wire the readings are identical on all 35 patterns**, with no
refusals. That is the actionable half of this study: a design whose absolute
rules are minimum-width can adopt `metal` for free, and only wider rules pay.

What the flip would buy: under channel, **164 of 210 pairs (78.1%) deliver
less metal than declared**, the worst 5.00× under.

### The refusals are not working designs

"Metal refuses 9% of declarations that route today" is only damning if those
declarations were being honoured today. They are not. Channel accepts every
one of the 19 and delivers **1.15×–5.00× under the declared width (median
3.00×)**. So the flip does not break working designs there; it refuses
silently-wrong ones. R3 exists to say the tool never silently degrades an
NDR, and on those pairs the channel reading does exactly that.

## Half 2 — what it costs a real flow

Five vehicles. **None hit an R3 refusal** — every one still exits 0.

| vehicle | demand | QoR (ov/unpl/viol) | shortfall lines | no-op verdicts |
|---|---|---|---|---|
| `ndr_abs_divisor` | w8 12→15, w8sp inactive→9 | 0/0/0 → **0/6/2** | 4 → 0 | 1W/2I → 0W/0I |
| `ndr_abs_um` | space5 13→20 | 0/0/0 → 0/0/0 | 2 → 0 | 0W/1I → 0W/0I |
| `ndr_abs_shared_bottomup` | absw 8→12 | 0/0/0 → **0/16/4** | — | 0W/1I → 0W/0I |
| `ndr_noop_rule` | alive 8→16, partial 16→20, dead inactive→12, spconly inactive→14 | 0/0/0 → **0/8/2** | 3 → 0 | 2W/2I → 0W/1I |
| `ndr_per_layer_em` | — | 0/0/0 → 0/0/0 | — | — |

**Three of five vehicles go from clean to stranded.** `ndr_per_layer_em` is
unchanged because it already declares `metal`; `ndr_abs_um` absorbs the extra
slots.

Everything the flip is FOR also shows up: every metal-shortfall line
disappears (4→0, 2→0, 3→0), and silent no-ops largely vanish — `w8sp`,
`dead` and `spconly` were inactive specs routing as ordinary buses and become
real rules (BUDA-1913 count 1→0 and 2→0).

### Read the wirelength column with care

Detailed WL **falls** on exactly the vehicles that strand bits (−57.7%,
−18.6%, −40.0%). That is not a win: a stranded bit lays no wire, so the
metric improves by losing the nets it was measuring. The study tool labels
these `<- bits went MISSING, not shorter` rather than printing a number that
reads like a free improvement.

### Healers recover about half

The vehicles are healerless, so the endpoints above are raw. Adding
`negotiate_congestion 20` + `ripup_reroute 20` to `ndr_abs_divisor` under
metal: negotiate accepts nothing (metric 6→6), ripup takes **6 opens → 3**.
Better, not clean — and clean is what the same design is under channel.

## Verdict

**Keep `metal` opt-in.** The flip is semantically right and physically
expensive: on the measured population it strands bits on three of five
vehicles, and healers only halve that.

The honest way to state the trade is that the flip does not CREATE the
problem, it REVEALS it. Those designs asked for more metal than their grid
can host at that bus width; under channel they got a narrower wire and the
tool reported success. So the flip converts a silent **quality** failure into
a visible **routing** failure. Which of those you prefer is a methodology
decision, and that is precisely the kind of decision that belongs in a
per-rule token rather than in a default.

The repo's own bar settles it independently: a semantic default moves only
when it is net-positive on measured QoR, and this is net-negative.

### Migration guidance, if you want the metal reading

1. **Minimum-width rules are free** — 35/35 patterns agree at 1× minimum
   wire. Add `metal` and nothing moves.
2. **Above that, expect +1 slot per bit** and check the seat has it. The
   `dump_ndr` demand line after `run_planner` names the real per-layer charge.
3. **A refusal is information.** R3 naming a layer whose longest contiguous
   signal run cannot deliver the width means that layer cannot host the rule
   — widen the grid, restrict the rule with `layers`, or lower the value.
   The alternative is not a wider wire, it is a narrower one you were not
   told about.

## What would change this verdict

A design corpus that leans on absolute widths for a physical reason. Every
vehicle here is one we authored to demonstrate the semantics, and none has an
EM or resistance requirement behind its numbers — so this study measures how
the two readings differ, not how much a real methodology would care. If
absolute widths acquire a real consumer, re-run `tools/ndr_metal_study.py`
and weigh the stranding against a requirement that actually exists.
