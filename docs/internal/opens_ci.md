# Open items — continuous integration

What `.github/workflows/ci.yml` deliberately does **not** cover yet, ranked by
value/effort. The workflow itself, its cost, and the dependency list are in
[`ci.md`](ci.md); this page is the backlog behind its "What CI deliberately
does NOT do" section.

Snapshot index — last verified against `main`: **2026-08-01**, at the commit
that introduced CI. Each item states what is uncovered, why it was left out
rather than forgotten, and where to start.

---

## 1. ~~Nightly QoR corpus~~ — RESOLVED 2026-08-02 (nightly half)

`.github/workflows/nightly-qor.yml` sweeps the corpus at 03:17 UTC and diffs it
against the previous successful nightly. The baseline is promoted **only** on a
clean run, so a regression keeps being reported until fixed rather than becoming
the new normal after one night. See [`ci.md`](ci.md).

Corrected while building it: the corpus is **37** flows and takes **~4m20s**
parallel (jobs=4), not the "36 flows, ~2-4 min serial" this page previously
estimated — that figure predated the corpus growing.

**Still open — the PR half.** The nightly compares `main` against itself over
time, so it catches a regression a day AFTER it lands. What it does not do is
stop one landing. A `run-qor` PR label that diffs a branch against its
**merge-base** would, and that is the shape the evidence argues for: the
`mix2_topdown_refine` regression (0/0/0 → 3/16/1) reached `main` precisely
because a hand-captured baseline predated a corpus row another PR added
mid-flight.

Where to start: build + sweep the merge-base, then the PR head, and
`--compare`. It costs two builds and two sweeps (~12-15 min), which is why it
belongs behind a label rather than on every PR. The nightly's cache cannot be
reused for it — its baseline is a `main` commit, not the branch's merge-base.

## 2. ~~Own the placement goldens in CI~~ — RESOLVED 2026-08-02

CI is now the golden reference host: `BUDA_NUTS_GOLDEN_STRICT=1` is set in the
workflow and `flow/rnr/mix.buda`'s golden was regenerated (QoR-neutral: one
16-bit bus segment M3→M5, identical totals). The other eight goldens were
byte-identical and untouched. See [`ci.md`](ci.md).

**What this cost, recorded because it was not obvious.** The flag was wider
than its name: `BUDA_NUTS_GOLDEN_STRICT` also switched
`test_flow_scripts.py::test_10_four_level_scale_one_bundle_per_bus` from
tolerant bounds to a hand-calibrated QoR ratchet (`segs == 209`,
`unplaced == 0`, no culls) that **no tool regenerates** — hardcoded numbers,
calibrated on the original host under `-march=native`, which is not CI's ISA.

CI measured **206**, the same as an ordinary developer container, so the
coupling would have failed every run for a reason unrelated to golden
enforcement. The ratchet now has its own `BUDA_FLOW_QOR_STRICT`, which CI does
not set; the two are independent concerns that merely shared a variable.

**Still open on that ratchet:** nothing enforces it anywhere now. It is
reachable via `BUDA_FLOW_QOR_STRICT=1` on whatever host it was calibrated for.
If that host no longer exists, the honest move is to re-calibrate it against a
host that does — and say so in its comment — rather than leave numbers nobody
can reproduce. Not urgent: the tolerant branch still asserts real bounds
(segment range, overlap cap, and that every unplaced bit is an accounted-for
keepout cull).

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
