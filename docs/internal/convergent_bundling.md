# CONVERGENT bundling — does it make sense?

Investigation of the bundler's `CONVERGENT` strategy end-to-end through the flat
routing pipeline. Short answer: **as currently modelled it is unsound** — it
groups nets that the topology generator cannot faithfully route, so all but one
driver of a convergent bundle are silently left unconnected. This note records
the evidence, the root cause, and the options.

Reproduced by `test/tests/test_bundler_convergent_pipeline.py`.

## The two strategies

`Bundler` (stage 1, `bundler.h/cpp`) groups nets by a string *signature*:

| Strategy | Signature | Intent |
|---|---|---|
| `STRICT` | driver instance + sorted receiver instances | nets that share *both* endpoints — a true parallel bus |
| `CONVERGENT` | sorted receiver instances **only** (driver ignored) | nets from *different* drivers that fan in to a common sink |

`CONVERGENT` only differs from `STRICT` when nets share a receiver instance but
have **different driver instances** — a many-to-one fan-in. That is exactly the
case examined here.

## Experiment

Four source blocks at separated rows, each driving one net into a single shared
sink on the right (`test_bundler_convergent_pipeline.py`):

```
add_block src0 0 0   100 80     add_net a0 src0.tx sink.r0
add_block src1 0 200 100 280    add_net a1 src1.tx sink.r1
add_block src2 0 400 100 480    add_net a2 src2.tx sink.r2
add_block src3 0 600 100 680    add_net a3 src3.tx sink.r3
add_block sink 800 250 950 450
```

Driven through `run_bundler {STRICT|CONVERGENT} → generate_topologies →
run_planner → run_nuts`:

| | STRICT | CONVERGENT |
|---|---|---|
| bundles | **4** (one per driver) | **1** (`reason=REC:sink`, nets a0–a3) |
| topology gen | `src0→sink`, `src1→sink`, `src2→sink`, `src3→sink` | **`src0→sink` only**, 6 units wide |
| NUTS horizontal runs (rows) | `[78, 265, 425, 601]` — **all four sources** | `[74]` — **src0 only** |
| `check_connectivity topo` | success | **success** (!) |

The CONVERGENT bundle's eight topology candidates are byte-for-byte the same as
the lone `src0→sink` bundle's candidates in the STRICT run — i.e. the bundle is
modelled purely from `src0`'s geometry, just widened to 4 bits.

## Root cause

`TopologyGenerator` (stage 2) derives **one** `src→dst` pair per bundle. A
bundle whose nets have different drivers therefore picks a single representative
driver (the first, `src0`) and routes *all* bits from there. The wires for
`a1/a2/a3` are drawn from `src0`, not from `src1/src2/src3`, so those three
drivers are never physically connected — the bus never goes near their rows.

`check_connectivity` (`verify.h/cpp`) does **not** catch this: it validates a
topology's *internal* self-consistency (segment continuity, busterm faces, block
coverage) against the bundle's own single-source geometry. It has no view of the
original per-net drivers, so a bundle that dropped three of them still "passes".

This is almost certainly why `src/buda_cli.py`'s `run_bundler` historically
hard-coded `STRICT` and ignored its argument: the `CONVERGENT` path produces
physically wrong routes.

## Verdict

- For nets that share **both** endpoints, `CONVERGENT` == `STRICT` (redundant).
- For genuinely convergent (different-driver) nets — the only case it exists for
  — it is **unsound**: the result silently omits all drivers but one.

So `CONVERGENT` does not make true sense in the present pipeline. Its *intent* is
real (fan-in patterns: multiple masters → one slave, write data → memory), and it
*might* prove useful, but only if topology generation gains **multi-source /
fan-in tree** support so a bundle can root at several drivers and merge toward the
shared sink. Until then it is a foot-gun.

## The same limitation applies to BIDIRECTIONAL

`run_bundler BIDIRECTIONAL` (`Strategy::BIDIRECTIONAL`, `bundler.cpp`
`run_bidirectional`) pairs a net with its reverse — a receiver instance of one
net is the driver of the other and vice-versa (A→B bundled with B→A, e.g. a bus
and its return path). Such a bundle **spans two drivers by construction** (the
forward net is driven by A, the reverse by B), so it hits exactly the same
single-`src→dst` wall: topology generation routes one direction and leaves the
other unrouted. It therefore warns like CONVERGENT and is covered by the same
fix below. See `test/tests/test_bundler_bidirectional.py`.

## What we did about it (for now)

- `run_bundler` now **honours** its `STRICT|CONVERGENT|BIDIRECTIONAL` argument
  (previously only STRICT, and the argument was ignored; default remains
  `STRICT`). Selecting `CONVERGENT` or `BIDIRECTIONAL` prints a warning that the
  multi-driver bundle routes from a single driver and the rest are left unrouted,
  pointing here.
- `test_bundler_convergent_pipeline.py` / `test_bundler_bidirectional.py` lock in
  the above: STRICT routes every driver; CONVERGENT/BIDIRECTIONAL run but reach
  only one driver's direction; the connectivity checker does not flag the gap;
  and the CLI honours the argument + warns.

## If we ever make CONVERGENT real

Topology generation would need to treat a multi-driver bundle as a fan-in tree
(several source busterms, one sink), e.g. an MST/Steiner trunk that each driver
joins, rather than a single `src→dst` spine. `check_connectivity` should then
also verify that every original net driver is actually attached (the missing
fidelity check that let this slip through). Revisit `test_bundler.py`'s
`bundler_logic.feature` "Convergent Bundling" scenario and the pipeline test here
alongside that work.
