# Open items — continuous integration

What `.github/workflows/ci.yml` deliberately does **not** cover yet, ranked by
value/effort. The workflow itself, its cost, and the dependency list are in
[`ci.md`](ci.md); this page is the backlog behind its "What CI deliberately
does NOT do" section.

Snapshot index — first written **2026-08-01**, at the commit that introduced CI;
the nightly items (1, 3, 5) re-verified against the workflow **2026-08-13**.
Each item states what is uncovered, why it was left out rather than forgotten,
and where to start.

---

## 1. ~~QoR corpus in CI~~ — RESOLVED (nightly 2026-08-02, PR gate 2026-08-04)

`.github/workflows/nightly-qor.yml` sweeps the corpus at 03:17 UTC and diffs it
against the previous successful nightly. The baseline is promoted **only** on a
clean run, so a regression keeps being reported until fixed rather than becoming
the new normal after one night. See [`ci.md`](ci.md).

Corrected while building it: the corpus is **37** flows, not the "36 flows,
~2-4 min serial" this page previously estimated — that figure predated the
corpus growing. Corrected again after the first real run: the sweep takes
**6m34s** on the hosted runner (jobs=4), not the ~4m20s measured in a developer
container. It has grown twice more since (41, then **48**), and at 48 the sweep
measures **7m41s** on the runner. The nightly also refreshes `qor/qor_table.md`
(see [`ci.md`](ci.md)), which adds a second sweep — item 3 below.

**Resolved 2026-08-04 — the PR half.** `.github/workflows/pr-qor.yml` sweeps the
corpus on a `run-qor`-labelled PR and diffs it against the PR's own
**merge-base**, so a regression is caught before it lands. The merge-base, not
current `main`: comparing against a `main` that moved since branching would
attribute other people's changes to the PR.

Label-gated because it is two builds + two sweeps — **measured 19m 39s** on the
first real run, base side alone 9m 44s. The merge-base
sweep is cached by its commit SHA, so pushing again to the same PR re-sweeps only
the head (~10 min), and the key can never serve a different base.

Clean builds on both sides deliberately: incremental across a checkout risks a
stale object making the two sides incomparable, which is the exact failure this
job exists to detect. Both sweeps carry the nightly's errored-row guard — here it
matters doubly, since an errored merge-base sweep would be *cached* and poison
every later run.

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

**Resolved 2026-08-04.** The ratchet is now a REGENERABLE golden
(`test/tests/data/flow_qor_golden.json`), rebaselined with
`BUDA_FLOW_QOR_REGEN=1` on the reference host, and CI sets
`BUDA_FLOW_QOR_STRICT=1` so it is actually enforced.

The blocker was never the numbers being strict — it was that they were frozen in
the test body, measured under `-march=native` on a host that no longer exists,
and rebaselinable by no tool. The only honest options were to guess or to
enforce it nowhere, and nowhere is what happened. Making it regenerable removes
that dilemma: it is now owned exactly the way the placement goldens are.

Calibrated at `x86-64-v2` and cross-checked: this container measures
`segs=206`, the same as CI, and the corpus agrees with CI to the digit — so the
golden holds on the gate. Verified in all four directions: strict passes on the
committed golden, a moved golden **fails** strict, a missing golden **fails**
rather than skipping, and the tolerant default still passes off the reference
host.

## 3. The nightly sweeps the corpus twice  *(cheap to fix; deliberately not fixed)*

The nightly runs `qor_corpus.py` (the regression gate) **and**
`qor_table.py` (the snapshot table), each doing its own full corpus sweep —
**~7.7 min apiece at 48 flows, ~18 min for the job** (measured on the runner
2026-08-12; it was ~6.5 min apiece at 37).

They measure the same pipeline. Both `run_flow`s source the flow the same way
and call the same `_check_design_bundles` / `_wirelengths`; they differ only in
the keys they return (`ovl`/`unpl`/`viol` vs
`overlaps`/`unplaced`/`viol_bundles`) and in the extra size/WL columns the table
carries. One sweep could feed both with a rename.

**Left as-is on purpose.** Sharing a sweep means the regression *gate* — the
nightly's primary job — starts consuming the other tool's row-builder, and the
cached baseline's key schema is invalidated in the process. Eight minutes of
unattended runner time is not worth putting the gate's fidelity in play. If
this is ever taken on, the safe direction is to make `qor_table.py` derive its
extra columns from a `qor_corpus` sweep (gate unchanged, table follows), not
the reverse.

## 5. ~~No way to accept a deliberate metric change~~ — RESOLVED 2026-08-13

Promote-on-clean (item 1) has a corollary nobody wrote down: a metric that moves
for a **legitimate** reason wedges the gate permanently. `viol_bundles` counts
the bundles `check_design` faults, so a new audit raises it by reporting a fault
that was always there — better *detection* is indistinguishable from worse
*routing* by that number alone.

Not hypothetical, and predicted in the commit that caused it. The per-bit
antenna audit's own A/B (`2c1d7fd`) measured `+1 viol_bundles` on two chip flows
with overlaps, unplaced and both wirelengths **byte-identical**, and said in as
many words that `--compare` would keep calling it WORSE and that accepting it
means re-baselining. There was no way to re-baseline. The nightly went red on
2026-08-11 and stayed red, still diffing against 08-10 — so 08-12's report
carried the detector delta *plus* a corpus that had grown 41 → 48 *plus* real
routing movement in the chip bottom-up family, all in one pile. A restore counts
as a cache access, so the baseline would not have aged out either.

The fix is a `promote_baseline` **dispatch input**: the compare still runs and
still prints, the sweep is promoted anyway, and the run is annotated
`::warning::` with the acceptance written into the job summary. Deliberately
narrow — empty on a schedule run, a no-op on a clean sweep, and downstream of the
errored-sweep rejection (a hard failure of its own), so it cannot bank a broken
sweep.

What is still owed: **nothing automatic**. A human decides that a delta is
instrumentation rather than quality, and the discriminator is the columns that
did *not* move. Teaching the gate to recognise that itself — an audit-version
field in the row schema, say, so a detector change is a separate axis from a QoR
change — is a real design and is not attempted here.

## 4. Execute the web ports — **RESOLVED 2026-08-03/04; only the Scala.js LINK step remains**

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

### The Scala.js link step — attempted, blocked, recorded

Worth writing down so nobody repeats the dead end. Linking without sbt looked
feasible: Scala 3 compiles to Scala.js IR via a plain `-scalajs` flag (no
compiler plugin), and `scalajs-linker` is fetchable from Maven. It does not
work in practice — `scalac -scalajs` dies with

```
java.lang.ClassCastException: Symbols$NoSymbol$ cannot be cast to Symbols$ClassSymbol
    at dotty.tools.dotc.core.Definitions.UnitClass
```

i.e. the compiler cannot resolve its own standard library, from a hand-assembled
`scala3-library_sjs1` + `scalajs-library` + `scalajs-scalalib` + `scalajs-javalib`
classpath. Several pinned combinations were tried. **If this is taken on, use
sbt** — the project already has `web/build.sbt` and `web/project/plugins.sbt`,
and reproducing what sbt resolves by hand is the part that fails.

Amusing footnote: the first attempt failed because `mvn dependency:get` had
cached *three* versions of `scalajs-library`, and the ad-hoc classpath included
all of them — the identical pollution bug Codex had flagged in the test itself an
hour earlier.

**What the JVM run does not cover:** the *linked* Scala.js artifact. A divergence arising
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
