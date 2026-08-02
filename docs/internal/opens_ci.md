# Open items — continuous integration

What `.github/workflows/ci.yml` deliberately does **not** cover yet, ranked by
value/effort. The workflow itself, its cost, and the dependency list are in
[`ci.md`](ci.md); this page is the backlog behind its "What CI deliberately
does NOT do" section.

Snapshot index — last verified against `main`: **2026-08-01**, at the commit
that introduced CI. Each item states what is uncovered, why it was left out
rather than forgotten, and where to start.

---

## 1. Nightly QoR corpus  *(highest value; medium effort)*

**Uncovered:** `tools/qor_corpus.py` sweeps 36 full flows and captures each
one's `(overlaps, unplaced, viol_bundles)` plus wirelength. Nothing runs it
automatically, so a QoR regression reaches `main` unless a human remembers to
sweep — which is exactly the recipe `CLAUDE.md` prescribes for every
topology/planner/NUTS change.

**Why it is not a PR gate.** The corpus is a **comparison** tool. A single run
is meaningless; it only says something against a baseline from the merge-base.
Making it a gate therefore needs baseline artifacts cached per commit, not just
a job that runs the script.

**Why it is worth building anyway — evidence, not speculation.** Two
regressions in the #518 arc were caught *only* by a corpus diff and by nothing
in the test suite. A third (`flow/rnr/mix2_topdown_refine`, 0/0/0 → 3/16/1) was
**missed entirely** and reached `main`, because the hand-captured baseline
predated a corpus row that another PR added while the work was in flight. An
automatically-refreshed baseline is precisely the mechanism that failure mode
has no answer to.

**Where to start.**
- Schedule: nightly on `main`, plus a `run-qor` PR label for opt-in.
- Baseline: cache keyed on the merge-base SHA; on a miss, build the merge-base
  and sweep it first. `--compare base.json branch.json` already exits non-zero
  on regression, so the gate logic exists.
- Runtime: ~2-4 min serial for 36 flows, plus a build. Budget ~10 min; the
  heavy flows (`rnr/mix2_fast_*`, `chip*`) dominate.
- Caution: a couple of corpus flows sit on knife-edge endpoints (`rnr/mix2`'s
  clean result depends on an exact 4-iteration rip-up trace, documented in the
  flow itself), so expect the occasional legitimate churn — the guard should
  report the diff, not auto-fail on noise it cannot distinguish.

## 2. ~~Own the placement goldens in CI~~ — RESOLVED 2026-08-02

CI is now the golden reference host: `BUDA_NUTS_GOLDEN_STRICT=1` is set in the
workflow and `flow/rnr/mix.buda`'s golden was regenerated (QoR-neutral: one
16-bit bus segment M3→M5, identical totals). The other eight goldens were
byte-identical and untouched. See [`ci.md`](ci.md).

**What this cost, recorded because it was not obvious:** the flag is wider than
its name. `BUDA_NUTS_GOLDEN_STRICT` is a repo-wide "I am the generation host"
declaration and also switches
`test_flow_scripts.py::test_10_four_level_scale_one_bundle_per_bus` from
tolerant bounds to a hand-calibrated QoR ratchet (`segs == 209`, `unplaced == 0`,
no culls) that **no tool regenerates** — those numbers are hardcoded, calibrated
on the original generation host under `-march=native`, which is not CI's ISA.

If that ratchet ever fails in CI, the honest options are (a) split the flag so
placement-golden strictness and the flow ratchet can be enabled independently —
they are genuinely different concerns that happen to share a variable — or
(b) re-calibrate the ratchet against CI and say so in its comment. Do **not**
quietly edit the numbers to whatever CI produces without recording that the
calibration host changed.

## 3. Execute the web ports  *(largest effort; independent of the rest)*

**Uncovered:** the display-geometry rule
(`viz_common.snap_endpoint_extents`) has three implementations — the
matplotlib renderer, `src/web/static/index.html` (`computeDisplay`), and
`web/src/main/scala/buda/web/render/DisplayGeom.scala`.
`test_web_displaygeom.py` pins the authoritative *behaviour*, but never
executes the JS or Scala, so drift in either port is caught only by review.

**Why this is a real gap, not a hypothetical.** Issue #554 is the demonstration:
the fix landed in the Python renderer and **both mirrors stayed broken**, and
the parity test stayed green throughout — it held its own private copy of the
rule at the time. The copy is gone (all three now pin one implementation), but
the ports are still synced by hand.

**Where to start.** Run the ports headlessly against the same serialized
`analysis` fixtures the Python test already builds:
- JS: Node + a small harness importing `computeDisplay`; the function is
  already pure over the serialized analysis.
- Scala: Scala.js test, or cross-compile `DisplayGeom` for the JVM — it takes
  `js.Dynamic`, so the JVM path needs a thin adapter.
- Cheaper interim: a lint step asserting the three implementations' rule
  comments stay identical. Catches nothing semantically, but makes divergence
  visible in review — which is where #554 was eventually caught anyway.

---

## Not gaps

Recorded so they are not "fixed" by someone reading the list too eagerly:

- **The suite runs headless.** Tk is not needed and `MPLBACKEND=Agg` is set; no
  display server or xvfb step is required.
- **Silently-skipped modules are covered.** The workflow fails on any skip whose
  reason is `could not import` — the mechanism that had left 28 web tests unrun
  in the first draft of this very workflow.  A new optional dependency will
  therefore break CI loudly instead of quietly shrinking the suite.
- **`-march=native` is still the developer default.** The pin is CI-only, via
  `BUDA_ARCH`. Changing the default would slow every local build for no
  reproducibility gain on a single machine.
- **The 5-minute gate is not a problem yet.** If it becomes one, split
  fast-tier on push / full suite on PR (~10-16s vs ~3m16s) rather than dropping
  tiers.
