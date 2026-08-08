# `buda` Command-Line Reference

`buda` is the routing-pipeline CLI. It reads a **`.buda` flow script** and executes
it top-to-bottom against the C++ engine, driving the whole pipeline (bundler →
topology → planner → NUTS → detailed NUTS) and, optionally, the interactive
visualizer.

This page documents the **command-line interface** (how you invoke `buda` and its
flags). For the commands you write *inside* a `.buda` script (`add_block`,
`run_planner`, …), see the [BUDA Script Reference](BUDA_SCRIPT_REFERENCE.md); for
the standard flow, see the [User Guide](USER_GUIDE.md).

---

## Synopsis

```
buda [-nv | --no-viz] [-t | --tag TAG] [-l | --log] [-j | --threads N] [--verbose-conn] [--strict-check] [--report-json PATH] [--ipc-verbose] [-h] <script.buda>
```

The CLI itself is `src/buda_cli.py`; you normally invoke it through the `bin/buda`
wrapper, which sets `PYTHONPATH` for you.

```bash
bin/buda demo/comprehensive_demo.buda            # from the repo root
buda demo/comprehensive_demo.buda                # if bin/ is on your PATH
PYTHONPATH=build python3 src/buda_cli.py flow/x.buda   # direct invocation
```

Add `<repo>/bin` to your `PATH`, or `source bin/activate` once per shell, to call
`buda` bare (see the "Wrapper scripts" section of the top-level `CLAUDE.md`).

---

## Positional argument

| Argument | Description |
|---|---|
| `script` | Path to a `.buda` flow script to execute. If the path does not exist and does not already end in `.buda`, the suffix is appended automatically (so `buda flow/x` runs `flow/x.buda`). With **no** script argument, `buda` starts and immediately exits — there is no interactive REPL; a script is how you drive the tool. |

The script is run as if by the `source` command: each non-comment line is one
pipeline command. Relative `source`/`open_bdb` paths inside the script resolve
relative to the sourcing script's directory.

---

## Options

| Flag | Default | Effect |
|---|---|---|
| `-h`, `--help` | — | Print usage (auto-generated from the flags below) and exit. |
| `-nv`, `--no-viz` | off | Skip `visualize` / `visualize_topologies` commands so no GUI window opens. Use for batch runs, tests, and CI. The full pipeline still runs and all logs are written. |
| `-t`, `--tag TAG` | none | Insert `TAG` into every log file name for this run, immediately before the suffix: `log/<cell>_<TAG>_flow.log`, `…_<TAG>_nuts.log`, etc. (with `-l`, `log/<cell>/<timestamp>/<TAG>_flow.log`). The default log names are overwritten by every re-run of the same script; a tag gives parallel experiments on one script (say two planner-knob settings, or the two `ripup_reroute` calls) their own log files instead. `TAG` is sanitized to a filename-safe token — anything outside `[0-9A-Za-z._-]` (spaces, slashes, …) becomes `_`. |
| `-l`, `--log` | off | **Archive the run.** All logs go to a fresh `log/<cell>/<timestamp>/` dir (never overwriting a previous run's) together with a copy of the **exact scripts executed** — the top-level `.buda` and every `source`d file, snapshotted the moment each is sourced, with a `MANIFEST` mapping each copy to its origin path (same-basename collisions are uniquified; a re-sourced file is archived once, but every distinct origin path gets its own copy and `MANIFEST` line, even when byte-identical). Made for exploratory tweak-and-re-run loops: each run keeps its logs *and* the script text that produced them. The dir is printed at start (`Run archive → …`). Without the flag, logging is unchanged (`log/<cell>_flow.log`, overwritten per run). |
| `--strict-check` | off | **Make a dirty design fail the run.** Exit `3` when any `check_design` reported violations, or could not run at all (a missing prerequisite — "did not demonstrate a clean design" is the question a harness asks). Without it a design with violations still exits `0`, so a CI/regression harness cannot gate on quality: the tool fails loudly on a malformed *script* but silently on a broken *design*. An explicit `exit <code>` in the script always wins — that is the author stating intent. |
| `--report-json PATH` | off | **Write the run's outcome as data.** JSON with per-command runtime and diagnostic counts, every `check_design` verdict (stage, violation total, counts by kind such as `UNPLACED`/`BUSTERM_OPEN`/`KEEPOUT_CROSS`), and end-of-run metrics (NUTS overlaps, DNUTS unplaced bits). For flow harnesses that should not have to parse terminal prose — the terminal summary is deliberately lossy and truncated. Written even when the flow ends in `exit`. |
| `--verbose-conn` | off | Make `check_design` (alias `check_connectivity`) print **every** per-bit violation individually. By default per-bit violations are collapsed into one line per (bundle, topology, kind, locus) group with a total — on a large design this turns tens of thousands of lines into a few hundred. |
| `-j N`, `--threads N` | max/2 | Worker threads for the **parallel pipeline stages**: the planner's candidate scoring, NUTS's per-layer solvers, and the healers' trial sweep. Results are **identical at any thread count** — each stage's parallelism is decision-identical by construction. Min 1; max = the number of logical CPUs available to this process (affinity- **and** CFS-cpu-quota-aware — on hybrid CPUs that is exactly performance-cores × SMT + efficiency-cores); an out-of-range request is clamped with a warning, never an error. Default (flag absent): **half the machine maximum**. Precedence: an explicit `--threads` overrides the three per-engine env vars (`BUDA_PLAN_THREADS` / `BUDA_NUTS_THREADS` / `BUDA_SWEEP_THREADS` — explicit semantics, which also bypass the engines' small-work gates, as a hand-set per-engine var always did); the flag-absent default sets only the **`BUDA_THREADS`** governor, a **ceiling** the engines' auto paths honor with their small-work gates intact — so ordinary runs never spawn pools for tiny candidate/layer sets, and per-engine env overrides keep working without the flag. |
| `--ipc-verbose` | off | Surface the `buda_viz` ↔ `def_viz` IPC socket **status** chatter (`[viz_ipc] listening on …`, `[buda_viz] IPC session=… connected=…`, `[buda_viz] IPC timer started …`). These are debugging lines and are hidden by default. Socket **errors** are always printed regardless of this flag. |

Flags may appear before or after the script path.

---

## Terminal output vs. the flow log

`buda` splits its output so the terminal stays scannable while the log keeps
everything:

- **Terminal** — one abstract summary line per command (a status marker, the
  command, its runtime, and a headline), followed by a **Runtime summary** table
  (per-command timings, total, slowest stage). Silent, instant setup commands
  (`add_block`, `def_layer`, …) are elided.
- **Flow log** (`<dir>/log/<script-stem>_flow.log`) — the full detail of every
  command (planner decisions, NUTS metrics, warnings, and C++ output), each under
  a `━━━ <command> ━━━` header with a trailing `[runtime] …` line. The path is
  printed at the end of the run.

With `-l`/`--log`, every log above instead lands in the per-run archive dir
`log/<cell>/<timestamp>/` (as `flow.log`, `nuts.log`, …), next to the archived
script copies and their `MANIFEST`.

Redirecting the terminal to a file (`buda … > out.log`) keeps its lines in
**chronological order**: `buda` line-buffers both the Python and the C++
(`std::cout`) sides at startup, so a mixed stream flushes per line instead of
letting a block-buffered C++ chunk strand until program exit (which, on some
platforms, displaced an early `[Planner]` block to the very end of the file — see
issue #31). No flag or `stdbuf` wrapper needed.

Other artifacts (`<stem>_nuts.log`, the `<stem>.json` topology sidecar, etc.) are
listed in the [Output files](BUDA_SCRIPT_REFERENCE.md#output-files) table.

---

## Exit status

| Code | Meaning |
|---|---|
| `0` | The script ran to completion (or hit an explicit `exit` with no code / `exit 0`). |
| `1` | A hard error aborted the run: an **unknown command** (a typo like `add_layer` for `def_layer` — a closest-match suggestion is printed), a **missing `source` file**, or invalid arguments. |
| `3` | `--strict-check` only: the run completed but the **design audit failed** (violations reported, or an audit that could not run). Distinct from `1` so a harness can tell "the tool worked, the design is dirty" from "the tool broke". |
| `N` | The script called `exit N` with a non-zero code. |

The tool fails fast rather than silently skipping a bad line, so a typo cannot
leave the design half-configured (e.g. no layers loaded).

---

## Environment

- **`PYTHONPATH`** must include `build/` (the compiled `buda` extension) and,
  for BDB `*.bdb.sql` fixtures and floorplanner helpers, `tools/`. The `bin/buda`
  wrapper and `bin/activate` set this for you.
- **matplotlib backend** — on macOS the CLI forces `TkAgg` for stability; set
  `MPLBACKEND=Agg` for a headless run (pairs naturally with `--no-viz`).

---

## Examples

```bash
# Full interactive run (opens the visualizer at the script's `visualize` line)
bin/buda demo/comprehensive_demo.buda

# Batch run, no GUI — useful in CI; inspect the flow log afterwards
bin/buda --no-viz flow/rnr/mix.buda
cat flow/rnr/log/mix_flow.log

# Exploratory tweak loop: archive each run's logs + the exact scripts it ran
bin/buda -l flow/rnr/mix.buda
ls flow/rnr/log/mix/            # one <timestamp>/ dir per run
cat flow/rnr/log/mix/20260712-153042/MANIFEST

# Tag the logs so two experiments on one script don't overwrite each other
bin/buda --no-viz -t baseline  flow/rnr/mix.buda   # → log/mix_baseline_flow.log
bin/buda --no-viz -t kseg500   flow/rnr/mix.buda   # → log/mix_kseg500_flow.log
diff flow/rnr/log/mix_baseline_flow.log flow/rnr/log/mix_kseg500_flow.log

# Full per-bit connectivity detail on the terminal
bin/buda --no-viz --verbose-conn flow/rnr/mix.buda

# Debug the buda_viz ↔ def_viz IPC link (show the socket status lines)
bin/buda --ipc-verbose demo/comprehensive_demo.buda
```

---

## See also

- [BUDA Script Reference](BUDA_SCRIPT_REFERENCE.md) — every command you can put in a `.buda` script.
- [User Guide](USER_GUIDE.md) — prerequisites and the standard flow, for novices.
- [Key Bindings](KEY_BINDINGS.md) — interactive shortcuts in the visualizer.
