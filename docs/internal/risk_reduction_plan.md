# Risk-reduction plan — addressing the Analysis.md "real risks"

Date: 2026-08-08.  Companion to [docs/Analysis.md](../Analysis.md)
(2026-08 rewrite), whose findings section names five current risks.
This plan turns each into concrete, gated workstreams, ordered so the
early phases build the instruments the later ones are validated with.

## R1. Mutable pybind surface → checked invariants + intent writes

The exposure is not `def_readwrite` itself (the healers' pin/restore
architecture and the P2 zero-copy container depend on Python-side
mutation) — it is that nothing checks cross-field consistency, so a bad
write surfaces stages later.  Historical bug classes are all one shape:
`pinned_seg_layers` surviving a repin onto a different-shaped candidate,
`seg_layers` length drifting from the selected topology, locked wrappers
mutated by a pass that should skip them.

1. **Inventory** (half a PR): script-dump every bound writable field
   from `bind_*.cpp`, classified engine-owned / plan-state /
   construction-only; the checked-in table is the contract steps 2-3
   enforce.
2. **Entry-point validator** (1 PR): C++ `validate_wrappers()` asserting
   the cross-field invariants (selected index in range, `seg_layers`
   length matches the selected candidate, `pinned_seg_layers` only with
   a pin, `hier.locked` ⇒ pinned, group-pin indices in range), called at
   engine entry points behind `BUDA_VALIDATE=1` — always-on in the test
   suite via conftest, zero-cost in production.  Acceptance: reproduce
   the historical `unpin_topology` stale-layers bug; the validator fires
   at the NEXT engine call, not at DNUTS.
3. **Intent methods** (1 PR): `w.pin(idx)` / `w.unpin()` /
   `w.select(idx, seg_layers)` keeping coupled fields atomic; migrate
   `buda_session/`; an "allowed writers" test greps session code for raw
   pokes to the coupled fields.

Gate: corpus byte-identity (validation changes nothing) + full tiers.

## R2. Print-identity oracle → structured decision trace

The parallel-path proofs (ripup sweeps, generation batch, planner
scoring) diff raw log lines: the right claim, a brittle encoding.

1. **`decision()` helper** (1 PR): emits the human line exactly as today
   AND, when tracing is enabled, appends a normalized record.  Output
   byte-identical — corpus-guarded.
2. **Migrate the identity tests** to compare traces; keep ONE byte-level
   log diff per area as a formatting canary.
3. **Corpus `--decisions` mode**: decision-record diffs, so prints-only
   changes validate as decision-identical without re-baselining.

## R3. `ripup.py` concentration → carve at the bug seam first

Not a rewrite — the hill-climb is the algorithm.  Extraction order
follows where the bugs have lived:

1. **`rr_state.py` first** (1 PR): snapshot/restore/dirty-tracking with
   an explicit coverage contract — plus the test that pays for the whole
   effort: introspect `BundleWrapper`'s bound fields (R1 inventory) and
   assert every field is classified *snapshotted* or
   *exempt-with-reason*.  Every historical restore bug was "snapshot
   didn't cover X"; growth of the binding without classification becomes
   a loud test failure.
2. **`negotiate.py` + `refine.py`** (1 PR).
3. **`rr_trials.py` / `rr_sweeps.py`** (1 PR); `ripup.py` keeps the
   driver loop.

Each step mechanical, gated on byte-identical corpus + the slow
par-vs-seq agreement tests (trace-based after R2).

## R4. Scale-sensitive runtime → cheap, continuous two-class measurement

Profiles differ qualitatively by design class (persistence dominated the
chip flows; trial marshalling dominated rnr), and nothing forced a
change tuned on one class to be measured on the other.

1. **`tools/runtime_ab.py`** (Phase A, LANDED): automates the
   base-vs-branch worktree A/B — builds both sides, runs named flows N
   times alternating, parses the per-command summaries, reports medians
   + deltas per command.  The measurement rule — any runtime-motivated
   change measures ≥1 rnr AND ≥1 chip vehicle — is now one command:
   `tools/runtime_ab.py flow/rnr/mix2_fast_bottomup_caps.buda
   flow/chip/chip_stack_topdown.buda`.
2. **Per-class runtime rollups in `qor_corpus --compare`** (Phase A,
   LANDED): runtime deltas aggregated by flow family (rnr / chip / big /
   hbundles / …) so class-level drift is visible even when no single
   flow crosses the noise floor.
3. **Nightly corpus** (merges with the standing
   [opens_ci.md](opens_ci.md) item): pinned `BUDA_ARCH`, history
   retained, alert on class-level runtime drift >10% or any QoR
   movement.

## R5. Doc staleness → CI guards, not reader archaeology

1. **Link checker** (Phase A, LANDED): every repo-relative markdown link
   in `README.md` / `CLAUDE.md` / `GEMINI.md` / `docs/**` resolves
   (`test_docs_guards.py`; found and fixed 2 broken links on landing).
2. **Command-coverage guard** (Phase A, LANDED): every command in the
   CLI registry (`buda_cmds.COMMANDS`, 92 today) appears in
   `CLAUDE.md` / `BUDA_SCRIPT_REFERENCE.md` / `docs/script_reference/` —
   an undocumented new command fails CI with its name.
3. **As-of stamps** on assessment-class docs (Analysis.md carries one);
   an age advisory stays optional — hard-failing on age would breed
   noise.

## Phasing

| phase | items | PRs | risk | buys |
|---|---|--:|---|---|
| A | R5 doc guards + R4 tooling | 1 | minimal | instruments; stops the staleness class |
| B | R2 decision trace + test migration | 2 | low | de-brittles the oracle before the refactor needs it |
| C | R1 inventory + validator + R3 snapshot-coverage test | 2 | low | turns the dominant historical bug classes into loud early failures |
| D | R3 extraction series | 3 | moderate, mechanical | shrinks the concentration point under B+C's net |

**Deliberate non-goals**: wholesale immutable bindings (would break the
healer architecture and zero-copy design for a win the validator gets
cheaper); a `.buda` grammar rewrite (fail-fast parsing covers the real
hazard); a log-format freeze (R2 removes the need).  Every phase keeps
the standing gates — corpus compare, full tiers, byte-identity wherever
a change claims neutrality.

## Status

- **Phase A: landed** (PR #612): `test/tests/test_docs_guards.py`
  (links + command coverage), `tools/runtime_ab.py`, per-class runtime
  rollups in `tools/qor_corpus.py --compare`.
- **Phase B: landed** (PR #614): `BudaSession._decision(text, tag,
  **kv)` — prints the human line verbatim (output byte-identical,
  verified against a main-build decision-line diff on the caps vehicle)
  and appends a normalized `(tag, kv)` record when tracing is on.
  Converted ripup's core decision sites
  (improver/heartbeat/stall-sweep/divergence/done, sequential AND
  parallel paths incl. the sequential deferred fallback); the fast
  par-vs-seq identity tests compare TRACES, the slow mix2 end-to-end
  test stays the byte-level formatting canary.
  `BUDA_DECISION_TRACE=<path>` dumps the run's records as JSON lines.
  Remaining B item: extend coverage to the passes the canary alone
  covers (global/class/release/negotiate lines) as they come up.
- **Phase C: landed** (PR #615): `validate_wrappers` in
  `buda_session/util.py` — the cross-field invariants of the historical
  bug classes (selection range, seg_layers shape, the unpin hazard as a
  SHAPE check since edit_commit legitimately forces layers pin-free,
  group-pin range) — raised LOUD at stage entries (`run_nuts` + both
  healer entries) under `BUDA_VALIDATE=1`, which conftest turns on for
  the whole suite.  v1 is Python at stage boundaries (the C++
  engine-entry twin remains open if a violation class ever needs
  sub-stage granularity).  Plus R3's snapshot-coverage contract
  (`test_wrapper_invariants.py`): every writable wrapper field must be
  classified snapshotted-or-exempt — it caught 7 unclassified hier
  fields on landing, exactly its job.
- **Phase D-1: landed** (this PR, stacked on #614): `rr_state.py` —
  `_rr_wrapper`/`_rr_snapshot`/`_rr_restore` extracted VERBATIM from
  RipupMixin as `RRStateMixin` (registered in the MIXINS disjointness
  guard), with the coverage contract documented at the seam and
  cross-referenced to `test_snapshot_coverage_contract`.  Gated: caps
  vehicle decision lines byte-identical (152 lines), fast tier + slow
  par-vs-seq agreement green.
- Phase D-2 (negotiate/refine extraction) and D-3 (trials/sweeps): not
  started.
