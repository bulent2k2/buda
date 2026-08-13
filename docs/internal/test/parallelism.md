# Parallel test runs (pytest-xdist)

The `mid` tier grew from ~40s (2026-06) to **~7 min** as the viz / ripup / hier
integration suites filled in (429s / 391 tests on a 4-core Linux box;
~5 min on Apple Silicon). This note records why we parallelize rather than
re-tier, and how it's wired.

## Profile the RIGHT thing — rebuild, and mind the markers

> ⚠️ **Always `bin/bb` (rebuild) before profiling, and profile `-m "not slow"`
> (what `bb -m` actually runs), not `-m mid`.** Two easy mistakes skewed the
> first read of this:
> - A **stale build** inflated `test_ripup_reroute`'s big2 tests to ~37s each
>   (~119s for the file); on a current build they are ~2.6s each (~13s total).
> - `-m mid` *selects* the `mid` marker and so **includes** tests that are ALSO
>   `slow` — e.g. `test_nuts_placement_golden[mix.buda]` (62s) is `@slow` and is
>   excluded from `bb -m`. Profile with `-m "mid and not slow"`.

On a current build, `pytest -m "mid and not slow" --durations` shows the mid
tier is **breadth of integration tests**, each a few seconds — mostly viz tests
(every one builds a full matplotlib `BudaVisualizer`) and `build_hier_demo`
(each assembles a hierarchical BDB), 3–16s apiece, with no single giant test.

A symmetric split of `mid` into three sub-tiers (mid-fast / mid-mid / mid-slow)
was still rejected: it yields **five** tiers to remember and forces a
hand-classification of every new test. Two better levers:

1. **Parallelize** — these tests are independent and CPU-bound (this doc).
2. Speed up the heaviest individual tests where it's free (see below).

## Setup: `-n auto --dist loadfile`

`pip install pytest-xdist`, then `bb -m` / `bb -s` auto-parallelize when xdist is
importable (the **fast** tier is serial by default — worker startup barely pays
off at its size — but `bb -t -p` / `bb -p` parallelizes it too). Direct
invocation:

```bash
pytest -o addopts="" -m "not slow" -n auto --dist loadfile      # mid, parallel
```

Measured (4-core Linux, after a clean build): **429s → 133s, 391 passed, 0
failures — ~3.2×.** On an 8–10-core Mac the speedup is larger, bounded by the
heaviest single *file* (see below).

### Why `--dist loadfile` (not the default per-test `load`)

`loadfile` keeps every test in a file on **one** worker. Several tests share a
fixture path next to a common `.buda` — e.g. the viz tests write a selection
sidecar `flow/<stem>.json` beside the flow they build, and many use
`dnuts1.buda`. Under the default per-test `load`, two such tests land on
different workers and **race on the same sidecar/log file**. `loadfile`
serializes within a file, so those never cross workers, while different files
still run in parallel — which is where the long-tail files live anyway.

**Rule for the next person adding a flow test:** `loadfile` only protects
*same-file* sharing. Cross-file safety rests on two invariants that hold today
but are unstated in code — keep them true: (1) **no two test files drive the
same flow via the CLI subprocess** (`bin/buda …` / `subprocess.run`), because
both would write the same `flow/log/<stem>_flow.log` and race across workers;
and (2) **in-process runs don't write flow logs** — a bare `BudaSession` (e.g.
the golden corpus via `nuts_snapshot.run_flow`) never sets `_flow_log` (only
`buda_cli.main` does), so in-process reuse of a flow across files is safe. So:
add a subprocess flow test only for a flow no other file runs that way, or run
it in-process. If you make `run_flow` log-faithful, this invariant changes and
per-flow paths must move under `tmp_path`.

On this 4-core box the default per-test `load` measured the *same* 132.78s (also
0 failures) — with only 4 cores the run is CPU-bound (429s / 4 ≈ 107s + overhead),
so the distribution mode doesn't move wall time, and `loadfile`'s determinism is
free. A single clean per-test run does **not** prove the suite is race-free
(races are timing-dependent), so `loadfile` stays the default.

The trade-off shows up at higher core counts: `loadfile`'s floor is the heaviest
single *file* (`test_ripup_reroute`, ~120s in one file, can't be split), whereas
per-test `load`'s floor is the heaviest single *test* (`nuts_golden[mix.buda]`,
62s). On an 8+-core Mac, per-test `load` could therefore beat `loadfile` — but
only once the viz/flow tests are made sidecar-isolated (unique `tmp_path` per
test) so per-test distribution can't race. That isolation pass is deferred as
future work; until then, `loadfile` is the safe default.

### Gotcha: rebuild first

A parallel run that suddenly shows ~100 failures with `AttributeError: '...'
object has no attribute '...'` is almost always a **stale build**, not an xdist
race — the compiled `buda`/`buda_db` in `build/` lags the source. `bb`
rebuilds before testing; a bare `pytest` does not. Rebuild (`bin/bb`) and re-run
before diagnosing parallelism.

## Controls

| Want | Do |
|---|---|
| Parallel mid/slow (default when xdist present) | `bb -m` / `bb -s` |
| Pin worker count | `BB_JOBS=8 bb -m` |
| Force serial | `BB_JOBS=0 bb -m` (or uninstall xdist) |
| Fast tier | serial by default (`bb -t`); parallel with `bb -t -p` / `bb -p` |
| Stop `-n auto` oversubscribing a hyperthreaded box | `pip install psutil` (see below) |

### `-n auto` needs `psutil` to mean *physical* cores

The floor below is physical cores, but xdist can only find those **through
`psutil`**: `-n auto` asks for the physical count and falls back to
`os.cpu_count()` — the *logical* count — when psutil is not importable (its
`--help` says as much: *"With 'auto', attempt to detect physical CPU count. If
physical CPU count cannot be determined, [fall back]"*).

psutil is **not** in the project's dependency list, so on a hyperthreaded box
the default is a worker per hyperthread. Measured on the 4-physical/8-logical
reference box: `-n auto` (→ 8) **37s** vs `-n 6` **33s** vs `-n 4` 35s.
`pip install psutil` makes `auto` pick 4; `BB_JOBS` overrides either way, and
the per-machine optimum is worth measuring once — it sat at 6 there, between
the physical and logical counts.

(Not reproducible on a runner without hyperthreading: where physical ==
logical, `-n auto` is identical with and without psutil — verified at 9.4s
either way on a 4-core Linux container.)

## `bb -p` is floored, and the fast tier is also the Windows suite

`bb -p` is bounded by two things nothing about xdist can move: the machine's
**physical** cores (hyperthreads add ~nothing to the CPU-bound C++ engine — on
a 4-physical/8-logical box `-n 4`, `-n 6`, `-n 8`, and `--dist load` vs
`loadfile` all measured the same ~34s), and a ~8.6s fixed floor (≈5s collection
+ xdist worker boot). Parallelism is already maxed, so the only lever is **less
work in the tier**. But two things make "just move the slow files to `mid`"
wrong more often than right:

1. **The fast tier WAS the whole Windows validation suite.** Every job in
   `.github/workflows/windows-validate.yml` ran `python -m pytest` with
   pytest.ini's default `-m "not slow and not mid"` — fast tier only, no
   `bb -m`. So marking a file `mid` would have deleted it from Windows
   validation entirely, not just from the inner loop. A test that runs on
   Windows and exercises platform-fragile behavior — `ProcessPoolExecutor`
   **spawn** semantics, `git worktree` path handling — earned its place in the
   fast tier *because* it was the only Windows gate, even when it was slow.
   (Past tense deliberately: the resolution below widened those jobs to
   `-m "not slow"`, so this constraint no longer binds. It is recorded because
   it is what makes the naive "just move the slow files to `mid`" wrong.)
2. **Serial cheap ≠ parallel cheap.** The qor-tooling tests are ~0.1–5s each
   run alone, yet dominate under xdist: they fan out their OWN subprocesses
   (`git worktree`, a `jobs=4` process pool) on top of 8 already-saturated
   workers, so they contend far worse than a CPU-bound engine test. That is a
   reason to make them xdist-lighter, not to hide them from Windows.

The resolution keeps both true. `windows-validate.yml` now runs **`-m "not
slow"`** (fast **and** mid), so `mid` no longer means "gone from Windows" — it
means "out of the developer inner loop." With that in place, the heavy xdist
contenders — the qor-tooling files (`git worktree`, the `jobs=4` pool) and the
`tclsh` + `buda_server` bridge — move to `mid` (2026-08): `bb -p` ~35s → ~25s,
and they still run under `bb -m`/`bb -s` **and** on Windows. (The workflow is
manual-only and explicitly not a gate, so widening it to the mid tier costs no
PR latency.)

The rule for a new test: a subprocess/tooling **integration** test — one that
shells out or measures the toolchain rather than the router — belongs in `mid`;
the inner loop is the engine's. Point 2 still stands as the residual lever: a
`mid` test that spawns its own pool should size it small so a future `bb -m -p`
does not oversubscribe.

## Surgical speedups applied

Two safe, coverage-preserving trims to the heaviest individual tests:

- **`test_build_hier_demo`** — 4 read-only tests each rebuilt the identical
  default demo (`_CELLS`, seed=1, ~4s of BDB assembly). A module-scoped
  `default_demo_bdb` fixture builds it once and shares it read-only (tests that
  run the bundler/planner, which persist into the BDB, still build their own).
  File: ~64s → ~46s.
- **`test_abstract_vias_hidden_in_detailed_mode`** — a `range(3)` detailed
  on/off loop drove ~12 full viz redraws; one leave/re-enter round-trip proves
  the re-gating just as well. 16.5s → ~9s.

Not touched: `test_ripup_reroute` is inherently iterative and each test mutates
a fresh `BudaSession` (no shareable setup); reducing its iteration counts would
change what it validates.

## Regenerate the numbers

```bash
bin/bb                                                   # rebuild first!
pytest -o addopts="" -m "not slow" --durations=30 -q     # serial, per-test times
time pytest -o addopts="" -m "not slow" -n auto --dist loadfile -q   # parallel
```
