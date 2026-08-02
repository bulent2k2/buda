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

## 2. Own the placement goldens in CI  *(small effort; needs a decision first)*

**Uncovered:** `test_nuts_placement_golden.py` xfails the four
`HOST_SENSITIVE_FLOWS` off their generation host rather than failing.
`BUDA_NUTS_GOLDEN_STRICT=1` turns those into hard failures, and the test's own
docstring suggests setting it "on the golden-generation host's CI". It is
**not** set, because CI is not currently that host.

Concretely: under the workflow's pinned `-march=x86-64-v2`, three of the four
match exactly and **`flow/rnr/mix.buda`'s large golden is unenforced**. The
five stable small flows are always enforced.

**Why it was left out.** Enabling it requires regenerating the goldens on the CI
image first, and that is a real decision rather than a workflow tweak: it
changes what the committed goldens *mean* (CI's ISA becomes the reference) and
who can reproduce a failure locally (a developer on `-march=native` would then
see mismatches CI does not, inverting today's situation).

**Where to start.** Decide whether CI is the reference host. If yes: regenerate
via `tools/nuts_snapshot.py` on the CI image, commit the diff for review, then
set `BUDA_NUTS_GOLDEN_STRICT=1` in the workflow. If no: leave as is and accept
that one large golden is advisory — but say so in the golden docstring, which
currently implies CI *should* enforce.

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
- **`-march=native` is still the developer default.** The pin is CI-only, via
  `BUDA_ARCH`. Changing the default would slow every local build for no
  reproducibility gain on a single machine.
- **The 5-minute gate is not a problem yet.** If it becomes one, split
  fast-tier on push / full suite on PR (~10-16s vs ~3m16s) rather than dropping
  tiers.
