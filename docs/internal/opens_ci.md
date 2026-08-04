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

Corrected while building it: the corpus is **37** flows, not the "36 flows,
~2-4 min serial" this page previously estimated — that figure predated the
corpus growing. Corrected again after the first real run: the sweep takes
**6m34s** on the hosted runner (jobs=4), not the ~4m20s measured in a developer
container. The nightly also refreshes `qor_table.md` (see [`ci.md`](ci.md)),
which adds a second sweep — item 3 below.

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

## 3. The nightly sweeps the corpus twice  *(cheap to fix; deliberately not fixed)*

The nightly runs `qor_corpus.py` (the regression gate) **and**
`qor_table.py` (the snapshot table), each doing its own full corpus sweep —
~6.5 min apiece, ~16 min for the job.

They measure the same pipeline. Both `run_flow`s source the flow the same way
and call the same `_check_design_bundles` / `_wirelengths`; they differ only in
the keys they return (`ovl`/`unpl`/`viol` vs
`overlaps`/`unplaced`/`viol_bundles`) and in the extra size/WL columns the table
carries. One sweep could feed both with a rename.

**Left as-is on purpose.** Sharing a sweep means the regression *gate* — the
nightly's primary job — starts consuming the other tool's row-builder, and the
cached baseline's key schema is invalidated in the process. Six minutes of
unattended runner time is not worth putting the gate's fidelity in play. If
this is ever taken on, the safe direction is to make `qor_table.py` derive its
extra columns from a `qor_corpus` sweep (gate unchanged, table follows), not
the reverse.

## 4. Execute the web ports — **JS half RESOLVED 2026-08-03; Scala half open**

The display-geometry rule (`viz_common.snap_endpoint_extents`) has three
implementations — the matplotlib renderer, `src/web/static/index.html`
(`computeDisplay`), and `web/src/main/scala/buda/web/render/DisplayGeom.scala`.
`test_web_displaygeom.py` pins the authoritative *behaviour* but executes no
port, so drift was caught only by review.

**Why this is a real gap, not a hypothetical.** Issue #554 is the demonstration:
the fix landed in the Python renderer and **both mirrors stayed broken**, and
the parity test stayed green throughout — it held its own private copy of the
rule at the time.

### JS — resolved

`test_web_js_port.py` extracts `computeDisplay` from `index.html` (brace-matched,
so it does not depend on a marker comment), runs it under **node** over the same
b44 fixtures, and diffs it elementwise against `snap_endpoint_extents`.

Verified by reintroducing both historical bugs into the JS: last-match-wins
(the #554 shape) and extend-only `min`/`max` with no endpoint gate (the original
port bug). Each fails the suite with numbers; `index.html` restored byte-identical
after.

No CI toolchain step — hosted runners ship node. A node-less runner would
*skip*, which is the silent-shrink failure mode that once removed 28 web tests
from a green run, so the workflow's skip guard greps for this test's reason
(`JS port unexecuted`) as well as `could not import`.

Scope: `computeDisplay` only. It is pure over the serialized analysis and is the
piece that has actually drifted; SVG element creation is a separate concern and
is not what #554 was about.

### Scala — resolved, and OPTIONAL by design

`test_web_scala_port.py` compiles the **real** `DisplayGeom.scala` unmodified
against a small JVM stand-in for the sliver of `scala.scalajs` it uses
(`web/src/test/jvm/JsShim.scala`) and runs it over the same b44 fixtures, via
`web/src/test/jvm/Harness.scala`.

Verified by reintroducing both historical bugs into the Scala — last-match-wins
(the #554 shape) and extend-only `min`/`max` — plus a deliberate syntax error,
which must surface as *"the Scala port does not COMPILE"* rather than a skip.
`DisplayGeom.scala` restored byte-identical after each.

**No sbt.** The compiler jars alone suffice:

```
mvn dependency:get -Dartifact=org.scala-lang:scala3-compiler_3:3.3.4
```

which populates `~/.m2`, where the test looks; or point `BUDA_SCALA_CP` at a
classpath. Marked `mid` (compile ≈ 5-10 s), so the fast tier is untouched.

**`BUDA_SCALA_PORT_TEST`** — unset/`auto` runs it when a toolchain is present
and skips otherwise; `1`/`on` makes a missing toolchain a **failure**; `0`/`off`
disables it outright. The auto-skip reason deliberately does not match the
workflow's dependency-skip grep, so a gate with no Scala stays green — unlike an
absent pip package, an absent Scala toolchain is an accepted configuration.

**What it does not cover:** the *linked* Scala.js artifact. A divergence arising
from Scala.js semantics rather than from the source — JS numerics being
uniformly `Double`, say — is invisible to a JVM run. Covering that needs sbt +
the scalajs plugin + a JS runtime. This buys the logic, which is the part that
actually drifted; it does not claim to buy the link step.

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
