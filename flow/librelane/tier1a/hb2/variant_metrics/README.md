# hb2 / N=8: two variant runs whose run directories are gone

These three files are the **only surviving record of two LibreLane runs**.
Everything else about them — `top/runs/hbabs/` and `top/runs/hbm2/`, the
step directories, the ODBs, the DEFs — has been deleted.  `n8/h/top/runs/`
now holds `hb`, `hbnt`, `hbrep` and `snapshots` and nothing else, and the
whole `n8/` tree is `.gitignore`d (`flow/librelane/tier1a/**/n*/`) and about
10 GB, so what is left of these two runs is here or nowhere.

That is why they are checked in.  They are **not** benchmark rows, and §"Why
neither is a `results.jsonl` row" below is the part to read before quoting a
number out of them.

| file | what it is |
|---|---|
| `hbabs.json` | `final/metrics.json` of run tag `hbabs`, 246 keys |
| `hbm2.json`  | `final/metrics.json` of run tag `hbm2`, 284 keys |
| `hbabs.note` | one hand-written line: `125800 power grid violations` |

## Provenance

Both are arm **H+B at N=8** on the `hb2` design, run from `n8/h/` by the
recipe in its generated `README.md` (§3a / §3b / §3c).  Both consumed BUDA's
corridors: each leg-3c log names `guided.odb`, exactly as the committed `hb`
and `hbnt` rows' logs do.

Wall times are from the `date +%s` markers in `n8/h/` (`abs3a.start/.end`,
`m2_3a.*`, `m2_3c.*`) — which are inside the ignored tree too, so they are
transcribed here rather than pointed at:

| tag | 3a (to detailed routing) | 3c (route + signoff) | outcome |
|---|---|---|---|
| `hbabs` | 309 s, 2026-09-07 20:59 | *never run* | **failed in 3a** |
| `hbm2`  | 323 s, 2026-09-07 21:06 | 3705 s, 21:12 | **failed in 3c signoff** |
| `hb` (committed row) | 331 s | 4193 s | completed |
| `hbnt` (committed row) | 355 s | 4436 s | completed |

The verdict is the repo's own completion test — `Flow complete` in the leg's
log (`repeat_run.sh` uses the same one).  `abs3a.log` and `m2_3c.log` do not
contain it; `m2_3a.log` does.

What distinguishes the two variants from `hb` is **not recorded anywhere**.
The tags and the marker names (`abs`, `m2`) are all that survive of the
intent, and this file deliberately does not guess.  One measured difference
is worth having in hand: `hbabs` reports `design__instance__count` 39,146 and
`design__instance__area` 1,540,650, against `hbm2`'s 528,415 / 3,314,400 —
and 3,314,400 is exactly the instance area of the committed `hb` and `hbnt`
rows, so `hbm2` shares its geometry with them and `hbabs` does not.

## `hbabs` — a PSM failure, before any routing

LibreLane quit at the end of leg 3a:

    125800 power grid violations (as reported by OpenROAD PSM- you may
    ignore these if LVS passes) found.

which is what `hbabs.note` records.  The split is in the dump:
`design__power_grid_violation__count` 125,800 = VGND 58,968 + VPWR 66,832.

**Trap.**  `timing__setup__ws` and `timing__hold__ws` are both `1e39` — the
LibreLane sentinel, i.e. *no timing was measured*, not a slack of 1e39.
`repeat_run.sh` warns about exactly this shape: a metrics.json carrying the
1e39 sentinel reads as "a plausible, entirely wrong row".  There is no
`route__wirelength`, no `route__drc_errors` and no DRC or LVS key at all,
because the run never reached them; what it does carry is
`global_route__wirelength` 341,356 and `route__wirelength__estimated`
294,003, which are *global*-route figures and not comparable with the
`route__wirelength` of a completed arm.

This is a **failure specimen**.  Its value is the PG-violation count and
its per-net split, which is a quantity the benchmark table has no column for.

Note that 125,800 is two orders of magnitude away from the 512 unconnected
VGND shapes / 144 clips of the PSM-0069 discussed in
`docs/internal/librelane_hier_flow.md` §11 item 8.  **Whether it is the same
failure is not established here** — the metrics alone cannot say, and the
`*-grid-errors.rpt` that could is in the deleted run directory.

## `hbm2` — complete through LVS, failed on Magic illegal overlaps

This run went nearly all the way.  Leg 3a completed; leg 3c routed, ran
signoff, passed LVS, and quit at a deferred check:

    6233 Magic Illegal Overlap errors found.

The last steps reached were `Checker.LVS`, `Checker.SetupViolations`,
`Checker.HoldViolations`, `Checker.MaxSlewViolations`,
`Checker.MaxCapViolations`.

**Trap.**  `route__drc_errors` is **0** and `klayout__drc_error__count` is
**0** — the two keys with "drc" in the name, both present, both clean — while
the check the run died on is `magic__illegal_overlap__count`, which is **6233**
here against **0** in the completed `hb` and `hbnt` runs beside it.  It is in
the dump; it is just not where a reader scanning for DRC looks.

`magic__drc_error__count` is **absent**, and that is *not* a symptom: it is
absent from all 13 rows of `results.jsonl` as well, because `Magic.DRC` is
skipped throughout this study.  LibreLane says so on the line above the error
(`magic__drc_error__count not reported.  Magic.DRC may have been skipped.`),
which reads like a finding and is the normal state here.

The detailed route also worked harder than its neighbours.  Route DRC by
iteration, against the two completed runs in the same directory:

| run | iter 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| `hb`   | 1  | 0 | 122 | 17 | 0 | — |
| `hbnt` | 1  | 0 | 120 | 27 | 0 | — |
| `hbm2` | 85 | 5 | 5   | 1  | 1 | 0 |

The routing, timing and LVS numbers are genuine: wirelength 301,145, setup
WS 0.365, hold WS 0.112, `design__lvs_error__count` 0,
`design__disconnected_pin__count` 0, instance area 3,314,400, utilization
0.3869.  For scale, the committed `hb` and `hbnt` rows at the same instance
area route 300,704 and 300,715 — `hbm2` is within 0.15 % of both.  What is
missing is the **signoff verdict**, and that verdict is a failure.

## Why neither is a `results.jsonl` row

`results.jsonl` is the benchmark table: one row per run, appended by
`runtimes.py … --json`.  Neither of these runs is a point on that benchmark,
and putting them there would plant both traps above inside the file the
write-up quotes:

- `hbabs` would contribute `timing__setup__ws: 1e39` and nulls for every
  routing column.
- `hbm2` would contribute a row whose two "drc"-named fields are both 0,
  beside 12 rows that completed, with its 6233 illegal overlaps carried in a
  key the table does not show.

`runtimes.py` refuses to compute an arm total when `route__wirelength` is
absent, on the stated grounds that it "would be a plausible, incomplete
number" (Codex #878).  The same reasoning applies a level up: a row is a
claim that a run is a measurement, and these two are records of how a run
failed.

The timing columns could not be filled in anyway — `steps`, `total_s` and
the per-stage seconds come from each step's `runtime.txt` in the run
directory, and the run directories are gone.  Only the per-leg wall times
above survive, and they are not the same quantity.

## Regenerating

There is no reproduction recipe for these two, because what made them
`hbabs` and `hbm2` rather than `hb` was not written down.  The arm itself
regenerates from `flow/librelane/tier1a/`: `gen.sh 8` then `harm.sh 8`, then
the steps of the generated `n8/h/README.md`.
