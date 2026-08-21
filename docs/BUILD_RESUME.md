# Build & Resume Sessions

*The short, practical guide to iterating on a `.buda` flow with
`btcl -b` / `btcl -r`.  The full reference — every rule, every refusal, and
why — is in [TCL_FRONT_END.md](TCL_FRONT_END.md); this page is the two
workflows you will actually type, each with a runnable demo.*

A **build session** (`btcl -b <flow>.buda`) runs your flow verbatim, with a
**checkpoint BDB** armed so every stage persists as it runs — bundles,
candidate topologies, the plan, the routing, and any pins you make at the
prompt afterwards.  A **resume session** (`btcl -r <flow>.buda`) restores
all of that from the checkpoint and re-enters the flow at the **deepest
stage the build recorded** — so an iteration re-plans (or just re-inspects)
instead of rebuilding.  You never invent a filename or a stage name: the
checkpoint is auto-named `<flow_dir>/<stem>.ckpt.bdb`, and `-s
topo|plan|nuts|dnuts` overrides the stage when you want to re-enter higher.

```bash
btcl -b demo/resume_flat.buda           # build; checkpoint auto-armed
btcl -r demo/resume_flat.buda           # resume at the deepest stage
btcl -r -s plan demo/resume_flat.buda   # re-enter at the planner instead
```

The `.buda` suffix may be omitted (`btcl -r resume_flat`) — the same rule
the engine's `source` applies: the bare name means `<name>.buda` when that
file exists and nothing is literally so named.

Both sessions end at the shared **pin/edit prompt**: `topos <bus>` to look
at a bundle's candidates, `pin <bus> <N>` to choose one (1-based), `replan`
to apply, `done` to save and exit (`help` — or `?` — lists the verbs;
anything else goes to the engine).  A pin writes through to the checkpoint at once, so
the next `-b` rerun or `-r` resume keeps it.

Two things to know before the walkthroughs:

* **`-b` pre-flights the flow text** before spending anything on it.  A
  flow whose last `open_bdb` is non-durable would route to the end and
  then discard everything — `-b` refuses that at t=0, naming the file and
  line, except for the one shape it can fix (the read-only input, below).
* **Resume replays the *recorded* build.**  If you edit the flow (or a
  sourced file, or a read-only input) after building, `-r` says so with a
  NOTE and the rebuild remedy; it never silently mixes old recipe with new
  text.

## Walkthrough 1 — flat: `demo/resume_flat.buda`

A small flat SoC (six blocks, seven buses) that opens **no BDB of its
own** — the simplest shape.  `-b` arms the auto-named checkpoint before
the flow, so the whole pipeline persists as it runs:

```text
$ btcl -b demo/resume_flat.buda
resume_flat.buda: -b arming checkpoint …/demo/resume_flat.ckpt.bdb
  …one summarized line per command…
resume_flat.buda: 39 command(s) ran -- FLAT flow
resume_flat.buda: resume trace …/resume_flat.ckpt.bdb.trace -- next session
    can skip the rebuild: …
resume_flat> topos d1        # look at bundle d1's candidates
resume_flat> pin d1 4        # choose candidate 4 (persists to the checkpoint)
resume_flat> done            # re-plans (pins changed), saves, exits
resume_flat.buda: done -- 0 overlaps, 0 unplaced, 0 audit violations
```

Resume — everything above the chosen stage restores, nothing recomputes,
and your pin from last session is in the restored plan:

```text
$ btcl -r demo/resume_flat.buda
resume_flat.buda: resuming at the deepest recorded stage `dnuts`
    (override: -s topo|plan|nuts|dnuts)
resume_flat.buda: RESUMED 8 bundles from …/resume_flat.ckpt.bdb
replay> run_detailed_nuts
replay> check_design dnuts
resume_flat> done
resume_flat.buda: done -- 0 overlaps, 0 unplaced, 0 audit violations
```

`-r -s plan` re-enters at the planner instead — the stage for trying a
different pin, since the planner honors pins when it re-chooses.  A `-b`
rerun of the same flow re-arms the same checkpoint, so pins survive
rebuilds too.

## Walkthrough 2 — hier: `demo/resume_hier.buda`

A two-level design (two instances of one tile cell, two leaves each) read
from a **read-only input**: the flow opens `resume_hier_input.bdb.sql`
*without* `writeback`, the common "read the design, never write it back"
shape.  `-b` gives that shape a checkpoint without touching the input: the
engine's materialized copy of the input — which receives every persist —
simply lands **in** the checkpoint instead of a throwaway temp:

```text
$ btcl -b demo/resume_hier.buda
resume_hier.buda: -b materializing the read-only input
    …/resume_hier_input.bdb.sql into the checkpoint
    …/resume_hier.ckpt.bdb (the input is never written)
  …
resume_hier.buda: 27 command(s) ran -- HIER flow
resume_hier.buda: NOTE -- the input … stays read-only; pins hold across
    `btcl -b` reruns (which reuse this checkpoint), not across a bare rerun
resume_hier> done
```

A hier resume comes in two flavors, and the driver picks the honest one:

```text
$ btcl -r demo/resume_hier.buda            # deepest stage = dnuts
resume_hier.buda: INSPECTION session (hier `dnuts` = post-expansion
    restore): pins, edits and replan are disabled here …
resume_hier> topos                         # look, verdict, explore — read-only
```

Below the planner, a hier checkpoint restores the *expanded* per-instance
view — perfect for a quick look at a long flow's routed result, but not a
place to change the design (a pin there would overwrite the templates the
checkpoint stores).  To change the design, resume at the planner:

```text
$ btcl -r -s plan demo/resume_hier.buda
resume_hier.buda: RESUMING at `plan` … 17 setup command(s),
    2 held by the checkpoint, 6 to replay
resume_hier> pin b_lohi 2
Pinned bundle 2 (b_lohi_t0_0) to topology 2 (2 expanded instances)
resume_hier> done
```

Two things this walkthrough shows that the flat one cannot: the
**construction commands are held** (`derive_busterms`,
`add_blocks_from_bdb`'s BDB side live in the checkpoint — replaying them
would be a duplicate-construction error), and the intra-tile buses are one
cell-local **template** — the single `pin b_lohi 2` re-routes *both* tile
instances.

## Pins made in the visualizer

`explore`/`show` open the topology explorer, and a pin made THERE (`s` on a
candidate) is a **preview**: the window re-routes what you see, but the
checkpoint deliberately does not change while you explore.  The pin's
durable form is the **sidecar** `.json` beside the flow, and a sidecar is
applied where the *planner* runs.  The session keeps you honest at every
seam:

* the moment the explorer saves, the prompt says so — a PREVIEW, with the
  two ways to make it real;
* `replan` commits it now: the flow's own routing tail re-runs (**healers
  included** — on a healing flow that is the expensive part, which is why
  it is never spent silently at `done`), applies the sidecar, and persists;
* exiting without a replan prints what was **not** committed — the pins
  still apply at a `-s plan` resume or a rebuild;
* a later `-r` that lands below the planner (`nuts`/`dnuts`) NOTEs the
  sidecar with the `-s plan` remedy, since a below-plan resume restores the
  checkpoint's plan as-is.

A typed `pin` at the prompt is different on purpose: it writes the
checkpoint at once, and `done` re-plans for it (the same healer-included
tail) so the checkpoint stays coherent — you asked this session to change
the design, so the session finishes the job.

The explorer itself keeps you honest too, because `s` is a **toggle**: a
second press on an already-pinned candidate UNPINS it (as does `x`), and
that used to happen silently — the only console evidence was one more
"Saved N selection(s)" line, which is how a session's cell-local pin
vanished with nobody the wiser.  Now every `s`/`x` says `PINNED bundle N
-> topo K (type)` or `UNPINNED bundle N` out loud.  The rerun button (↺)
re-runs the pipeline under the **existing** pins — it no longer pins
whatever candidate happened to be displayed (paging through a bundle to
compare and pressing ↺ used to create a pin nobody asked for); it NOTEs
when the displayed candidate is unpinned, and `s` remains the one way to
pin.

**Committed GUI pins are durable without the `.json`.**  Once a sidecar
pin reaches the planner (`replan`, a `-s plan` resume, the flow's own
`run_planner`), the checkpoint learns the whole choice: `is_pinned` on the
candidate row — the cell-local TEMPLATE row included, so a hier
plan-resume re-fans the pin to every instance — and any custom per-segment
layers as meta (`pinned_layers:<bundle>`).  Deleting or losing the sidecar
after that no longer costs the pin: `load_pipeline` restores both, and a
resumed planner keeps the choice and the layers.  Pinning a cell-local
template in the GUI also says what the commit will do —
`(cell-local template -- applies to every instance)` — since the preview
window only ever re-routed the one instance the pin was made on.

Two prompt verbs round this out: **`pins`** prints the live pin inventory
(one line per pinned bundle — candidate number, type, forced layers,
bottom-up copies marked), the same inventory a `-r` resume prints right
after `RESUMED` so a session starts by saying which choices it carries;
and **`save <path>`** snapshots the current state to a named file
(`.sql` = diffable text, else binary) — bare `save` keeps writing the
`.sql` beside the checkpoint.

## The rules, in one place

| Flow shape (`-b`'s pre-flight verdict) | What `-b` does |
|---|---|
| No `open_bdb` at all | Arms the auto checkpoint before the flow; a rerun re-arms the same file (pins re-attach) |
| Own durable checkpoint (binary `.bdb`, or `.sql writeback`) | Uses it — arms nothing, and says so |
| Only open is a read-only `.sql` input | **Redirects**: the materialized copy becomes the checkpoint; the input is never written |
| Anything else non-durable (`:memory:`, several opens, missing input) | Refused at t=0, naming the file and line |

* `-r` finds the checkpoint itself (the auto name, the flow's own durable
  checkpoint, or any build trace naming this flow); zero found is a
  refusal with the remedy (`btcl -b`), two or more is a question.
* `-s <stage>` alone implies `-r`.  `-b` takes no stage (a build runs the
  whole flow).
* Staleness is NOTEd, never silent: the build stamps the flow's whole
  source tree (and a read-only input) into the trace, and a resume whose
  text changed since says so, with the rebuild remedy.
* A session whose pins have nowhere durable to land (bare `btcl -i`, no
  checkpoint) prints them at exit as **flow text** paste lines
  (`select_topology d1 4`) — the experiment's outcome survives either way.

Everything here also works with explicit names — `btcl -i <flow> <ckpt>
<stage>` is the same machinery spelled out — and from PowerShell on
Windows (`btcl.ps1`, same flags).
