# hb2 / N=8: the metrics behind §11 item 13's two failed notch fixes

These three files are the **only surviving record of two LibreLane runs**.
Everything else about them — `top/runs/hbabs/` and `top/runs/hbm2/`, the step
directories, the ODBs, the DEFs, the `*-grid-errors.rpt` — has been deleted.
`n8/h/top/runs/` now holds `hb`, `hbnt`, `hbrep` and `snapshots` and nothing
else, and the whole `n8/` tree is `.gitignore`d
(`flow/librelane/tier1a/**/n*/`) and about 10 GB, so what is left of these two
runs is here or nowhere.

They are the measurements behind **`docs/internal/librelane_hier_flow.md` §11
item 13**, "Both candidate fixes for the #896 notch FAIL, each differently"
(measured 2026-09-07 on the N = 8 H+B arm, one top run each).  That section
records the findings; these are the artifacts they were read off.

| file | what it is |
|---|---|
| `hbabs.json` | `final/metrics.json` of run tag `hbabs`, 246 keys — the **whole `<cell>.openroad.lef` abstract** fix |
| `hbm2.json`  | `final/metrics.json` of run tag `hbm2`, 284 keys — the **met2-scoped `patch_obs.py` blanket** fix |
| `hbabs.note` | one hand-written line: `125800 power grid violations` |

The variant each tag names is §11 item 13's two bullets, and `notch.sh`'s
header repeats the mapping ("the whole abstract cost 125,800 PDN violations,
a met2 blanket 6,233 Magic overlaps").  The four tags of that experiment are:

| tag | abstract the top consumed | where its metrics are |
|---|---|---|
| `hb`    | Magic's LEF (baseline)            | `results.jsonl` row, run dir survives |
| `hbabs` | the whole `<cell>.openroad.lef`   | **here** — run dir deleted |
| `hbm2`  | `patch_obs.py`'s met2 blanket     | **here** — run dir deleted |
| `hbnt`  | `notch_obs.py` (the accepted fix) | `results.jsonl` row, run dir survives |

The two kept here are **not** benchmark rows — see "Why neither is a
`results.jsonl` row" below.

## Provenance

Both are arm **H+B at N=8** on the `hb2` design, run from `n8/h/` by the
recipe in its generated `README.md` (§3a / §3b / §3c).  Wall times are from
the `date +%s` markers in `n8/h/` (`abs3a.start/.end`, `m2_3a.*`, `m2_3c.*`),
which are inside the ignored tree too and so are transcribed here rather than
pointed at:

| tag | 3a (to detailed routing) | 3c (route + signoff) | outcome |
|---|---|---|---|
| `hbabs` | 309 s, 2026-09-07 20:59 | *never run* | **failed in 3a** |
| `hbm2`  | 323 s, 2026-09-07 21:06 | 3705 s, 21:12 | **failed in 3c signoff** |
| `hb` (committed row, the baseline) | 331 s | 4193 s | completed |
| `hbnt` (committed row) | 355 s | 4436 s | completed |

The verdict is the repo's own completion test — `Flow complete` in the leg's
log, the same one `repeat_run.sh` uses.  `abs3a.log` and `m2_3c.log` do not
contain it; `m2_3a.log` does.

**Only `hbm2` consumed BUDA's corridors.**  They go into the ODB at step 3b,
between a *successful* 3a and the 3c resume from `guided.odb` (`harm.py`
§4a/4b/4c).  `hbabs` failed in 3a, so it has no 3b and no 3c: its metrics
describe a run that never reached the arm's third contribution.  `m2_3c.log`
names `guided.odb`, exactly as the committed `hb` and `hbnt` logs do.

The dumps and §11 item 13 agree line for line, which is worth stating because
it is what makes them usable as evidence: 125,800 PG violations; KLayout DRC
2 → 0; top wire 300,704 → 301,145 µm; Magic illegal overlaps 0 → 6,233.

## `hbabs` — the whole abstract: a PSM failure before any routing

§11 item 13's first bullet.  `<cell>.openroad.lef` does close the notch, but
it carries blanket OBS on met4 and met5 where Magic's LEF has none, and those
are the top's PDN layers; pdngen drops every strap crossing an obstruction, so
the macros lose their supply.  LibreLane quit at the end of leg 3a:

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
`global_route__wirelength` 341,356 and `route__wirelength__estimated` 294,003,
which are *global*-route figures and not comparable with the
`route__wirelength` of a completed arm.

Its `design__instance__count` is 39,146 and `design__instance__area`
1,540,650, against `hbm2`'s 528,415 / 3,314,400 — and 3,314,400 is exactly the
instance area of the committed `hb` and `hbnt` rows.

**What this dump cannot settle**: whether these 125,800 violations are the
PSM-0069 of §11 item 8 (512 unconnected VGND shapes, 144 clips).  The
`*-grid-errors.rpt` that would say went with the run directory.

## `hbm2` — the met2 blanket: complete through LVS, failed on Magic overlaps

§11 item 13's second bullet, and the informative failure.  `patch_obs.py`'s
met2-scoped blanket clears the PDN gate and fixes the DRC, but the top ROUTES
met2 — that is where `emit_pin_def` puts the N/S bus pins — so declaring the
whole layer obstructed and then routing on it is precisely the overlap Magic
counts.  Leg 3a completed; leg 3c routed, ran signoff, passed LVS, and quit:

    6233 Magic Illegal Overlap errors found.

every one of them `Illegal overlap between obsm2 and metal2 (types do not
connect)` (§11 item 13).  The last steps reached were `Checker.LVS`,
`Checker.SetupViolations`, `Checker.HoldViolations`,
`Checker.MaxSlewViolations`, `Checker.MaxCapViolations`.

**Trap.**  The two **aggregate** DRC fields are both 0 — `route__drc_errors`
(the final detailed-route iteration) and `klayout__drc_error__count` — while
the check the run died on is `magic__illegal_overlap__count`, **6233** here
against **0** in the completed `hb` and `hbnt` beside it.  It is in the dump;
it is just not one of the two a reader scanning for a DRC total lands on.
(The per-iteration `route__drc_errors__iter:N` keys are not all zero either —
see the table below — but those are the router converging, not a verdict.)

`magic__drc_error__count` is **absent**, and that is *not* a symptom: it is
absent from all 13 rows of `results.jsonl` too.  `RUN_MAGIC_DRC` is `False` on
every top run in this tree, and the overlap check rides Magic's stream-out
rather than its DRC deck — §11 item 13 states both, and makes the same
distinction this section turns on.  LibreLane prints
`magic__drc_error__count not reported.  Magic.DRC may have been skipped.` on
the line above the error, which reads like a finding and is the normal state
here.

The detailed route also worked harder than its neighbours.  Route DRC by
iteration, against the two completed runs in the same directory:

| run | iter 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| `hb`   | 1  | 0 | 122 | 17 | 0 | — |
| `hbnt` | 1  | 0 | 120 | 27 | 0 | — |
| `hbm2` | 85 | 5 | 5   | 1  | 1 | 0 |

The routing, timing and LVS numbers are genuine and are the ones §11 item 13
quotes: wirelength 301,145 (+0.15 % on `hb`'s 300,704), KLayout DRC 0 against
the baseline's 2, setup WS 0.365, hold WS 0.112, `design__lvs_error__count` 0,
`design__disconnected_pin__count` 0, instance area 3,314,400, utilization
0.3869.  What is missing is the **signoff verdict**, and that verdict is a
failure.

## Why neither is a `results.jsonl` row

`results.jsonl` is the benchmark table: one row per run, appended by
`runtimes.py … --json`, and all 13 of its rows carry a routed wirelength and a
signoff DRC count.  These two runs are not points on that benchmark — they are
two rejected fixes — and rows would plant both traps above inside the file the
write-up quotes:

- `hbabs` would contribute `timing__setup__ws: 1e39` and nulls for every
  routing column.
- `hbm2` would contribute a row whose two aggregate DRC fields are both 0,
  beside 12 rows that completed, with its 6,233 illegal overlaps carried in a
  key the table does not show.

`runtimes.py` refuses to compute an arm total when `route__wirelength` is
absent, on the stated grounds that it "would be a plausible, incomplete
number" (Codex #878).  The same reasoning applies a level up: a row is a claim
that a run is a measurement, and these two are records of how a fix failed.

The timing columns could not be filled in anyway — `steps`, `total_s` and the
per-stage seconds come from each step's `runtime.txt` in the run directory,
and the run directories are gone.  Only the per-leg wall times above survive,
and they are not the same quantity.

## Regenerating

Both variants are defined, so both are reproducible.  Build the arm from
`flow/librelane/tier1a/` (`gen.sh 8 -PEPAD 100`, `pins.sh 8`, `harm.sh 8
--pins pins`), then run the top by the generated `n8/h/README.md` with the
macro abstracts replaced:

* **`hbabs`** — point the top's `MACROS` at each cell's whole
  `<cell>.openroad.lef`.  §11 item 13 notes that file does not exist by
  default: `OpenROAD.WriteViews` is not in the Classic flow, so it has to be
  produced from a block's final ODB (`write_abstract.tcl`).
* **`hbm2`** — `patch_obs.py`'s met2 blanket over each abstract, in place of
  `notch.sh`'s surgical patch.

Neither is worth re-running for its own sake: both were rejected, and the fix
that replaced them is `notch_obs.py` — the boolean difference between the
macro's real met2 GDS and what its LEF claims, "the metal the abstract omits,
and nothing else" — which is what `notch.sh` runs today.
