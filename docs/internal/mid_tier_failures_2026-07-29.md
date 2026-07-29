# Mid-tier test failures — triage (2026-07-29, main @ 34a35ec)

Running the **mid** tier (`pytest -m "not slow"`, or `bin/bb mid`) on current
`main` surfaces **3 failures**. All three are **stale-test drift** — assertions
pinned to a specific planner selection or routing state that has since changed
for legitimate reasons. **None is an engine regression**; two of them are a test
failing *because the routing got better*. The mid tier is not gated by CI (the
repo has none), so these drifted unnoticed across several planner-evolution
merges.

```
FAILED test/tests/test_hier_bidirectional.py::test_hier_run_bundler_rejects_bad_strategy
FAILED test/tests/test_nuts_span_tighten.py::test_bundle30_mp6b_seg7_span_is_tight
FAILED test/tests/test_ripup_reroute.py::test_hier_stage_b_clears_opens
```

Fast tier is green (`pytest`: 1839 passed on the same build minus the 3 mid
cases); slow tier not run here.

---

## 1. `test_hier_run_bundler_rejects_bad_strategy` — behavior contract changed

**File:** `test/tests/test_hier_bidirectional.py`

**Symptom.** The test runs `run_hier_bundler depth 1 SOMETHING` and asserts the
captured stdout contains `"error"`:

```python
sess.do_command("run_hier_bundler depth 1 SOMETHING")
assert "error" in capsys.readouterr().out.lower()
```

It now raises `SystemExit` out of `do_command` before the assertion runs, so the
test errors instead of asserting.

**Root cause.** PR #467 ("reject unknown options across all fixed-vocabulary
commands") made an unknown strategy a **hard error that stops the flow**
(`reject_unknown_options` → `sys.exit(1)`), replacing the old print-and-return.
This is the intended, documented behavior. The fast-tier twin
(`test_hier_bundler_combined.py::test_hier_bundler_rejects_bad_strategy_and_accepts_new_ones`)
was updated to `pytest.raises(SystemExit)` in that PR; **this mid-tier test in a
different file was missed** because the mid tier wasn't run during that change.

**Not a regression** — the command does reject the bad strategy; it now also
stops the script, which is the whole point of #467.

**Recommended fix (test-only).** Mirror the fast-tier update:

```python
import pytest
...
out = io.StringIO()
with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
    sess.do_command("run_hier_bundler depth 1 SOMETHING")
assert "unknown option" in out.getvalue().lower()
```

(The `capsys` fixture also works, but `pytest.raises(SystemExit)` is the load-
bearing change — without it the `SystemExit` propagates and fails the test.)

---

## 2. `test_bundle30_mp6b_seg7_span_is_tight` — selection moved off the pinned segment

**File:** `test/tests/test_nuts_span_tighten.py`
**Flow:** `flow/hbundles/06_multipin_stress.buda`

**Symptom.**

```python
seg7 = [ts for ts in s.nuts_result.segments
        if ts.bundle_id == 30 and ts.seg_idx == 7]
assert seg7, "bundle 30 seg7 not placed"      # AssertionError: []
```

Bundle 30 (`mp6_b`) no longer has a `seg_idx == 7` in its **selected** topology.

**Root cause.** The test asserts an anti-phantom-tail invariant on **one specific
segment index of one specific bundle's specific selected topology**. Planner /
ripup evolution on `main` (the measured-release + uniformity-break line #486/#487,
and earlier BITRUNK anchoring/gating work) changed bundle 30's topology
**selection** — the new winner simply has a different segment layout with no
`seg7`. The test's own docstring already records one such prior selection shift
("the legacy-BITRUNK anchoring gate legitimately changed bundle 30's topology
SELECTION … its seg7 now routes a different (longer) but still tail-free span"),
so this is the *second* time the pinned index drifted.

**Not a regression.** `06_multipin_stress` is fully clean at the corpus snapshot
(`qor_table.md`: `0/0/0`, 448 net segments), so the design routes correctly — the
test's chosen probe point just no longer exists.

**Recommended fix (test-only), preferred → fallback.**
1. **Make it selection-robust:** assert the phantom-tail invariant (abstract span
   within realization noise of the segment's own bit-wires' extent) over **every**
   placed segment of bundle 30 (or every trunk segment), rather than hard-coding
   `seg_idx == 7`. This is what the test's docstring says it's really checking, and
   it survives future selection changes.
2. **Or** re-anchor to the current winner: dump `bundle 30`'s selected topology on
   `main`, pick the trunk segment that carries the long span, and update the index
   — accepting it will drift again on the next selection change.

Recommendation: option 1 — the invariant is the real intent; the index is an
incidental coupling.

---

## 3. `test_hier_stage_b_clears_opens` — the vehicle no longer starts dirty

**File:** `test/tests/test_ripup_reroute.py`
**Flow:** `flow/hbundles/06_multipin_stress.buda`

**Symptom.**

```python
base = s.detailed_result.num_unplaced
assert base > 0, "06_multipin_stress should start with DNUTS opens"   # 0 > 0 fails
```

The flow now reaches **0 DNUTS opens before ripup stage-b runs**, so the test's
precondition (that there are opens for ripup to clear) is false.

**Root cause.** The healer/ripup improvements this test was written to guard
(bottom-up healer templates #472, ripup measured-release #487, dead-span
escalation, etc.) now clear `06_multipin_stress` earlier in the pipeline — it is
clean at baseline (`qor_table.md`: `06_multipin_stress → 0/0/0`). The test picked
this flow precisely because it *used* to carry a residual `8 → 0` that stage-b
healed; that residual is gone.

**Not a regression** — this is the routing getting strictly better. The test is
guarding a scenario that no longer occurs on this vehicle.

**Recommended fix (test-only), in priority order.**
1. **Re-home the test to a still-congested vehicle** — one that still enters
   ripup stage-b with DNUTS opens > 0 (e.g. a `mix2_fast*` residual vehicle, or a
   trimmed variant of `06_multipin_stress` with a tighter grid). The test's value
   is the *mechanism* (stage-b re-pins a per-instance wrapper and drives opens to
   0), so it needs an input that still exercises it.
2. **Or** convert it to a monotonicity/idempotence check: assert stage-b never
   *increases* opens and leaves a clean flow clean (a weaker but still meaningful
   guard that survives the vehicle going clean).
3. Retiring it outright is **not** recommended — the stage-b uniformity-break
   path (#487) is exactly the kind of code that benefits from a regression guard;
   it just needs a live input.

---

## Cross-cutting recommendation

All three are the same failure mode: **tests coupled to a specific planner
selection or a specific residual, which legitimately move as the router
improves.** Two mitigations:

1. **Prefer invariants over pinned states.** Assert *properties* (no phantom
   tail on any segment; stage-b never worsens opens; a bad option is rejected)
   rather than a hard-coded `seg_idx`, a specific bundle's residual count, or a
   print-vs-raise contract. Failures 2 and 3 would have survived if written this
   way.
2. **Run the mid tier before planner/ripup/option-validation merges.** With no
   CI, the mid tier drifts silently. `bin/bb mid` (or `pytest -m "not slow"`)
   before merging anything that can move a topology selection would have caught
   all three at their source PRs (#467 for F1; the ripup line for F2/F3).

None of the three blocks a release or indicates a routing defect; the fixes are
all test-only. This document is the triage; the actual test updates should land
as a separate code PR.
