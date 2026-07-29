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

**Recommended fix (test-only): delete the redundant pinned test.** The
selection-robust "phantom tail over every segment" invariant this test is really
after is **already implemented in the same file** by
`test_no_phantom_abstract_span_beyond_detailed_extent`
(`test_nuts_span_tighten.py:58-87`): it runs the same `FLOW` and applies the same
40-unit overhang check to **every** selected segment — including all of
bundle 30's realized segments. So `test_bundle30_mp6b_seg7_span_is_tight` adds no
coverage the all-segment test doesn't already give; rewriting it as a
selection-robust invariant would merely duplicate that test (and pay for a second
full-pipeline run). Remove the pinned test.

*Only* if a genuinely distinct property is wanted, redefine it around something
the all-segment invariant does not cover — e.g. bundle 30's topology **type/shape**
rather than a specific `seg_idx` — but note that too will drift with selection.

*(Credit: Codex flagged the overlap with the existing all-segment test.)*

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

**Recommended fix (test-only): re-home to a still-congested vehicle.** The test's
value is the stage-b **mechanism** (re-pin a per-instance wrapper, drive DNUTS
opens to 0), which only runs when the flow **enters** stage-b with opens > 0.
Point it at an input that still carries nonzero DNUTS opens at baseline — a
residual `mix2_fast*` vehicle, or a tighter-grid variant of `06_multipin_stress`
that keeps an open.

A clean-flow "monotonicity / idempotence" variant is **not** a viable fallback:
when the metric is already 0, `_ripup_reroute` prints `metric already 0 — nothing
to do.` and **returns before the hill-climb** (`src/buda_session/ripup.py:~2320`),
so it exercises none of stage-b. It would only duplicate
`test_hier_supported_and_noop_when_clean` (`test_ripup_reroute.py:293-305`), which
already covers the clean-hier no-op, and would leave the uniformity-break path
(#487) untested. So the vehicle **must** start dirty; do not keep this test on a
clean flow.

Retiring the test outright is still not recommended — the stage-b
uniformity-break path deserves a live regression guard; it just needs a dirty
input.

*(Credit: Codex flagged that a clean flow short-circuits before stage-b.)*

---

## Cross-cutting recommendation

All three are the same failure mode: **tests coupled to a specific planner
selection or a specific residual, which legitimately move as the router
improves.** Two mitigations:

1. **Prefer invariants over pinned states.** Assert *properties* (no phantom
   tail on any segment; a bad option is rejected) rather than a hard-coded
   `seg_idx` or a print-vs-raise contract — F1 and F2 would not have drifted
   this way (indeed F2's invariant form already exists). The caveat is F3: an
   invariant survives the flow going clean, but a healer/ripup regression guard
   is only *meaningful* on an input that still exercises the path, so those tests
   additionally need a **live dirty vehicle**, not just a robust assertion.
2. **Run the mid tier before planner/ripup/option-validation merges.** With no
   CI, the mid tier drifts silently. `bin/bb mid` (or `pytest -m "not slow"`)
   before merging anything that can move a topology selection would have caught
   all three at their source PRs (#467 for F1; the ripup line for F2/F3).

None of the three blocks a release or indicates a routing defect; the fixes are
all test-only. This document is the triage; the actual test updates should land
as a separate code PR.
