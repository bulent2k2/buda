# Customizing Topologies — pins, group pins, and hand edits

How to take manual control of BUDA's routing decisions: inspect the candidates
the tool generated, **pin** the one you want, **edit** a candidate's geometry or
**force its segment layers**, and keep those choices across sessions — from a
`.buda` script, the interactive explorer, or a Tcl flow (`btcl`).

This is a beginner's *how-to* with the best-known methods. The per-command
reference lives in [script_reference/planner.md](script_reference/planner.md)
(`select_topology`, `unpin_topology`, `dump_pins`) and
[script_reference/topologies.md](script_reference/topologies.md) (the TopoEdit
session); the explorer's keys in [KEY_BINDINGS.md](KEY_BINDINGS.md); the Tcl
front end in [TCL_FRONT_END.md](TCL_FRONT_END.md).

---

## The mental model — five things to know

1. **Every bundle has a pool of candidate topologies.** Stage 2
   (`generate_topologies`) creates them; stage 3 (`run_planner`) *selects* one
   per bundle and assigns its segments to layers. Left alone, the planner
   re-chooses freely on every run.

2. **A pin is a durable selection.** `select_topology` (or the explorer's `s`,
   or an `edit_commit pin`) marks one candidate so the planner must use it. A
   pin binds every later `run_planner` — this session's and, with a file-backed
   BDB open, the next session's too (`topology.is_pinned` is written through at
   once).

3. **Candidate numbers are 1-based; edit-session segment numbers are 0-based.**
   The candidate id you see anywhere — `dump_topologies`' `topo` column, the
   explorer title's `topo N/n`, the planner's `topo N of M` — is the 1-based
   number you type back into `select_topology` / `edit_topology`. But *inside*
   an edit session, `edit_set_layer <seg#> …` and friends take the **0-based**
   segment index, as printed by `edit_status` and `dump_topologies --conn`.

4. **A pin does nothing until you re-plan.** Pinning only marks the input;
   `run_planner` (or the prompt's `replan`) is what re-routes with it. Same for
   forced layers from an edit session.

5. **A layer *hint* is a suggestion; a *pinned* layer is a command.** Editing a
   segment's layer sets its `layer_hint`, which the planner may override. Only
   `edit_commit pin` promotes the session's layer edits into forced
   per-segment overrides (`pinned_seg_layers`) the planner must honor.

---

## Step 0 — see what you have

```
dump_topologies                    # every bundle's candidate list
dump_topologies bus_033            # one bundle, by net-name prefix
dump_topologies 8 --conn           # + per-segment connectivity for the selection
dump_topologies bus_044 --grouped  # collapse near-identical locus families
dump_topologies --problems         # only bundles with suspicious candidates
dump_pins                          # the current pin inventory
```

- The selector is the **same** everywhere it appears (`dump_topologies`,
  `select_topology`, `visualize_topologies`, the prompt's `pin`): a bare
  integer is a bundle **ID**, a bare non-numeric token is a **net-name
  prefix**, and `id:<N>` / `net:<prefix>` force one reading when a bus name
  starts with a digit.
- `--conn` is what you run **before an edit session**: it prints each segment's
  index (0-based), layer, slide range, and what it connects to — the numbers
  `edit_set_layer` / `edit_add_stub` take.
- Visually: `visualize_topologies bus_033` opens the explorer (step candidates
  with `a`/`d`, segments with `j`/`k`); add `debug` to step in **planner-cost
  order** with the cost breakdown in the title.

> **BKM — pin by name, not by ID.** Bundle IDs are assigned at bundling and
> shift when the netlist or bundling strategy changes; a net-name prefix
> (`bus_033`) survives both. The one place a numeric ID is *required* is
> `edit_topology`.

---

## Pinning a candidate

```
select_topology bus_033 29        # pin bus_033 to its candidate 29 (1-based)
select_topology 8 29              # the same, by bundle ID
select_topologies 1,5-9,11 3      # many bundles at once (lists + ranges)
unpin_topology bus_033            # let the planner re-choose (keeps selection)
unpin_topology *                  # clear every pin
run_planner 5                     # nothing moves until you re-plan
```

The number `check_design` prints (`Bundle 8: …`) is the same number the
selector takes — audit a violation, pin the fix, re-plan, re-audit.

### Group pins — pin the family, not the member

Many candidates are the *same topological choice* at slightly different trunk
positions (a "nominal-locus family"). Hand-picking one member freezes a
degree of freedom the planner is better at optimizing:

```
dump_topologies bus_044 --grouped        # see the families (`family:+K@lo..hi`)
select_topology bus_044 group:12         # restrict the planner to the family
                                         # CONTAINING candidate 12 — it still
                                         # refines WHICH member wins
```

In the explorer, `S` (shift-s) is the group pin, `s` the single pin, `x`
unpins either.

> **BKM — prefer the group pin** when your intent is "this *shape*, not that
> one" (e.g. "use the TRUNK_H family, not the MST tree"). Use a single pin only
> when the exact trunk position matters.

---

## Editing a candidate — the TopoEdit session

When no generated candidate is right, open a transactional working copy, edit
it, and commit it into the pool as a `USER` candidate:

```
dump_topologies 8 --conn      # learn the segment indices first (0-based)
edit_topology 8               # open a copy of bundle 8's SELECTED candidate
edit_topology 8 3             #   … or of candidate 3 (1-based)
edit_topology 8 new           #   … or start from an empty topology
edit_status                   # segments + a live verdict, any time
edit_commit                   # append to the pool (uid-deduped), no selection
edit_commit pin               # append AND pin it (and force layer edits — see below)
edit_abort                    # discard everything
```

The edit verbs, each printing a verdict (`check_topo` violations, zero-slide
pinches, wire-graph islands) after every step:

| Command | What it does |
|---|---|
| `edit_add_trunk <H\|V> <perp> [<lo> <hi>] [layer <id>]` | Add a trunk on a Hanan line (default full-span) |
| `edit_add_stub <block> <seg#> [layer <id>]` | Stub a block to segment `seg#` |
| `edit_set_span <seg#> <lo> <hi>` | Override a segment's along-axis span |
| `edit_set_layer <seg#> <layer_id>` | Set the segment's layer (a *hint* until the commit pins it) |
| `edit_set_slide <seg#> <lo> <hi>` \| `clear` | Constrain (or free) the NUTS slide window |
| `edit_connect <i> <j>` / `edit_disconnect <i> <j> <retract_to>` | Join / split perpendicular segments |
| `edit_remove_segment <seg#>` | Delete a segment |

Coordinate arguments accept **block/face references** resolved against the
session's floorplan, so you rarely type raw numbers:

```
edit_add_trunk H blk_a.top+20          # 20 above blk_a's top edge
edit_set_span 2 blk_a.left blk_b.right
```

(`<block>.<left|right|top|bottom|cx|cy>[±N]` — and the explorer's `[edit-cmd]`
log emits the same form, so a GUI edit is replayable as script text.)

Two commit rules worth memorizing:

- **A not-ok verdict is a WARNING, not a rejection** — your candidate stays in
  the pool and visible to `check_design`, like generation's never-strand rule.
- With a BDB open the commit also stores the **op-log provenance** (the exact
  `edit_*` lines applied, on which base candidate); `dump_user_ops <bundle_id>`
  prints it back as a replayable script.

The GUI equivalent: in the explorer, `e` opens an edit session on the shown
candidate (`E` an empty one), `T`/`Y` arm trunks, `S` stubs, `+`/`-` cycle a
segment's layer — same engine, same op-log.

---

## Recipe — change the segment layers of a pinned topology

The question this guide grew out of. The key: `edit_set_layer` alone only sets
a *hint*; **`edit_commit pin`** is what turns the session's layer edits into
forced overrides the planner must honor.

```
dump_topologies 8 --conn      # 1. find the segment index (0-based) + current layer
edit_topology 8               # 2. open the pinned/selected candidate
edit_set_layer 2 6            # 3. segment 2 → layer 6 (M6)
edit_commit pin               # 4. commit + pin — prints
                              #    "Pinned N segment layer(s) …"
run_planner 5                 # 5. re-plan: the forced layers now bind
check_design                  # 6. verify (LAYER_DIR etc. still audit)
```

- A **bare** `edit_commit` after `edit_set_layer` prints a warning that the
  layer edits were **not** pinned — the planner would treat them as
  suggestions.
- `unpin_topology` deliberately clears the forced layers too: they are indexed
  by the pinned candidate's segments, and a re-chosen topology must not
  inherit another shape's H/V layers.
- **Hier designs:** the edit session opens in the bundle's own frame (a
  cell-local template edits in the cell's floorplan), and a pin on a template
  — layers included — fans out through the expansion map to **every instance**
  at the next `run_planner hier`.

> **BKM — check the bulk knob first.** If your goal is systematic ("short
> stubs should drop to cheaper layers", "this cell must stay under M4"), a
> policy beats hand edits: `run_planner post_nuts [V [short long]] [H …]`
> reassigns stub layers in bulk, and `set_cell_layer_cap` / `reserve_top_layers`
> govern whole cells. Hand-pin layers for the exceptional net, not the rule.

---

## Keeping your choices — sessions and persistence

Pins and committed edits are only as durable as the store behind them:

- **File-backed BDB open** (`open_bdb design.bdb` in the flow, or a checkpoint
  armed by the launcher): pins, USER candidates, and forced layers persist;
  the next session restores them via `load_pipeline` — or simply re-running
  the flow (a rebuild re-attaches pins to the regenerated pool by content uid).
- **No BDB**: pins die with the session, and the tool says so honestly.

The fast iteration loop is **`btcl -b` / `btcl -r`** (build / resume — the
short, no-filenames-to-invent spellings; the full workflow guide is
[BUILD_RESUME.md](BUILD_RESUME.md)):

```
btcl -b flow/my.buda            # BUILD: run the flow with a checkpoint
                                #   auto-armed (<flow_dir>/<stem>.ckpt.bdb),
                                #   then the pin/edit prompt
btcl -r flow/my.buda            # RESUME at the deepest stage the build recorded
btcl -r -s plan flow/my.buda    # …or re-enter at the planner — the stage for
                                #   trying a different pin
```

(The general form underneath is `btcl -i flow.buda [ckpt.bdb [stage]]` — an
explicit checkpoint path and stage; `-b`/`-r` are the ergonomic spellings of
the same machinery.)

At the prompt: `topos <sel>` · `pins` · `explore <sel>` · `pin <sel> <N>` ·
`unpin <sel>` · `replan` · `done` (re-plans if pins changed, saves, exits).
Any engine command passes through verbatim — including the whole
`edit_topology … edit_commit pin` sequence — and the scripted form
(`echo "pin d1 4\ndone" | btcl -b …`) is the same code path.

Two rules of that world:

- **An explorer pin is a preview.** A pin made inside `explore`'s GUI does not
  write the checkpoint until you `replan` (or `done`, which re-plans for you
  when pins changed).
- **Inspection sessions refuse pins.** A hier `nuts`/`dnuts` stage resume is a
  post-expansion *read-only look* at a routed result; pins/edits/`replan` are
  guarded there (their persist would clobber the checkpoint's template rows).
  Make your edits in a `topo`/`plan` resume or a build session — the guard's
  message says exactly that.

From a plain Tcl flow (no `-i`), every command above is `buda::<name>`:

```tcl
buda::select_topology bus_033 29
buda::run_planner 5
buda::edit_topology 8
buda::edit_set_layer 2 6
buda::edit_commit pin
buda::run_planner 5
```

---

## Worked example — end to end on `demo/custom_topo.buda`

Everything above, on a real (checked-in) vehicle. The flow is a small design
whose interesting bundle — `a[8]`, cpu→dsp — has a block (`blk`) sitting in
the middle of its diagonal, so generation produces a rich pool: two L shapes,
two Z shapes, four U detours. The flow itself is the plain baseline (no pins),
so all the customizing happens interactively on top. Every transcript below
is the tool's real output.

```bash
btcl -b demo/custom_topo.buda      # BUILD session: run the flow, get the prompt
```

```
custom_topo.buda: -b arming checkpoint …/demo/custom_topo.ckpt.bdb
```

`-b` arms an auto-named checkpoint **before** the flow, so everything we pin
persists — no filename to invent.

### 1. Inspect the pool

```
custom_topo> topos a_0
── bundle 1  nets=8 (a_0…)  width=12.0  sel=1  cands=8
   topo type                  wl        wl[lo..hi] segs pass  mslide  notes
      1 L_HV@x600@y200       600       [600..1000]    2    0     200  *SEL
      2 L_VH@y400@x200       600       [600..1000]    2    0     200
      3 Z_HVH@x400@y200      600       [600..1360]    3    0     200
      4 Z_VHV@y300@x200      600       [600..1160]    3    0     160
      5 U_VHV@y-60           920       [840..2400]    3    0     200
      …
```

The planner picked candidate 1, the L through the x=600 column. Suppose we
want that column kept clear (the `d` bus's mem traffic lands there) and prefer
the Z through the middle channel — candidate **4**, same nominal wirelength.

### 2. Pin it and re-plan

```
custom_topo> pin a_0 4
Pinned bundle 1 (a_0) to topology 4
custom_topo> replan
[Planner] Bundle 1 (12 units wide) -> topo 4 of 8: Z_VHV@y300@x200 [pinned]  [V→M5 H→M4 V→M5]  overflow=0
…
  check_design    Success: no violations found.
```

The planner now *must* use candidate 4 (`[pinned]`) and is free only in what
the pin leaves open — layers, and NUTS's slide within the windows.

### 3. Look inside the selection — `--conn`

```
custom_topo> dump_topologies a_0 --conn
   conn detail — topo 4: Z_VHV@y300@x200
     seg0  V M5  along[200,300] perp=200  slide=[0..200]    pull=→hi(1)
        busterms: cpu@face=200(mid)
     seg1  H M4  along[200,600] perp=300  slide=[220..380]  pull=none(0)
        segs:     seg0@200(end), seg2@600(end)
        otc-over: blk
     seg2  V M5  along[300,400] perp=600  slide=[600..800]  pull=→lo(-1)
        busterms: dsp@face=400(mid)
```

Three segments, **0-based**: two short vertical stubs (`seg0` at cpu, `seg2`
at dsp) on TOP M5, and the horizontal trunk (`seg1`) on M4, flying over `blk`
(`otc-over` — normal over-the-cell routing, not a feedthru). The stubs are
100 units each; they don't need a TOP layer. Let's drop them to LOW M3 and
keep M5's capacity for long wires.

### 4. Force the stub layers in an edit session

```
custom_topo> edit_topology 1                  ← numeric bundle ID (not a_0!)
[edit] session opened on bundle 1: copy of candidate 4 (Z_VHV@y300@x200) (3 segment(s)).
custom_topo> edit_set_layer 0 3
[edit] seg 0 layer -> 3
custom_topo> edit_set_layer 2 3
[edit] seg 2 layer -> 3
custom_topo> edit_commit pin
[edit] committed as candidate 9 of bundle 1 (type USER, WL=600, uid 682f9025…).
[BDB] op-log provenance stored (2 op(s)) — dump_user_ops 1 shows it.
  Pinned bundle 1 to it.
  Pinned 3 segment layer(s) from the session's edit_set_layer edits.
custom_topo> replan
…
  run_detailed_nuts    [DetailedNUTS] 32 net segments placed, 0 bits unplaced.
  check_design         Success: no violations found.
```

Note the three things `edit_commit pin` did that a bare `edit_commit` would
not: selected the committed candidate, **forced** the session's layers
(`Pinned 3 segment layer(s)…`), and — with the BDB open — stored the op-log
so `dump_user_ops 1` can replay how it was built. `--conn` now shows
`seg0  V M3` / `seg2  V M3`, and the design still audits clean.

### 5. Save, quit, and prove it persisted

```
custom_topo> done
custom_topo.buda: done -- 0 overlaps, 0 unplaced, 0 audit violations
```

A **new** session, resuming at the planner from the checkpoint:

```bash
btcl -r -s plan demo/custom_topo.buda
```

```
custom_topo.buda: -r resuming from …/demo/custom_topo.ckpt.bdb
```

```
custom_topo> topos a_0
── bundle 1  nets=8 (a_0…)  width=12.0  sel=9 PINNED  cands=9
   topo type                  wl        wl[lo..hi] segs pass  mslide  notes
      …
      4 Z_VHV@y300@x200      600       [600..1160]    3    0     160  dup
      9 USER                 600       [600..1160]    3    0     160  *SEL,dup
custom_topo> pins
dump_pins: 1 pinned bundle(s):
  bundle 1 (a_0) -> topo 9 (USER) layers[M3 M4 M3]
```

The USER candidate is back in the pool, still pinned, **with its forced
layers** (`layers[M3 M4 M3]`) — the choice outlived the process. (The `dup`
marks are honest: our USER candidate is geometrically candidate 4 with
different layers, and the dump says so.)

To hand control back at any point: `unpin a_0` (prompt) or
`unpin_topology a_0` (script) — which also drops the forced layers, so the
planner's next choice starts unencumbered.

### The same thing as a plain script

Every prompt step above is an ordinary command, so the whole session can be a
`.buda` tail (or `buda::…` lines in a Tcl flow) instead:

```
select_topology a_0 4
run_planner 5
edit_topology 1
edit_set_layer 0 3
edit_set_layer 2 3
edit_commit pin
run_planner 5
run_nuts
run_detailed_nuts
check_design
```

The interactive prompt and the script are the same code path — a pin means the
same thing through every door.

---

## Pitfalls checklist

1. **1-based candidates, 0-based segments.** `select_topology 8 3` and
   `edit_topology 8 3` count from 1; `edit_set_layer 2 6` counts from 0
   (as `edit_status` / `--conn` print).
2. **`edit_commit` ≠ `edit_commit pin`.** Only the `pin` form selects the
   candidate and forces the session's layer edits; the bare form leaves them
   as hints (and warns).
3. **Pin, then re-plan.** Nothing re-routes until `run_planner` / `replan`.
4. **`unpin_topology` also drops forced layers and group pins** — by design;
   see the recipe above.
5. **Pins index into the candidate pool**, so any opt-in knob that renumbers
   it — `set_prune_dominated`, `set_dedup_loci`, `set_drop_dangling`
   (drop modes), `set_trim_mst_legs`, `set_trim_trunk_stubs` — must be
   declared *before* generating, and your pins must come from a run with the
   same knobs. (Pinned candidates themselves are never dropped; selections
   are remapped by content uid.)
6. **Bundle IDs shift between netlist/bundling changes** — pin by net-name
   prefix; the audit's `Bundle N:` number is valid for *this* run.
7. **`edit_topology` wants the numeric bundle ID** — the one place a net-name
   hint doesn't work; get the ID from `dump_topologies <prefix>`.
8. **One edit session at a time** — `edit_commit` or `edit_abort` before
   opening the next.
9. **Hier: pin the template, not an instance.** A cell-local pin propagates to
   every occurrence; per-instance divergence is the healers' job
   (`check_template_tracks … independent`), not the pin's.

---

## See also

- [script_reference/planner.md](script_reference/planner.md) — `select_topology`
  / `select_topologies` / `unpin_topology` / `dump_pins` in full, including the
  group-pin semantics.
- [script_reference/topologies.md](script_reference/topologies.md) — the
  TopoEdit session reference, `generate_more_topologies` (accrete candidates
  instead of editing), and the pool-shaping knobs.
- [KEY_BINDINGS.md](KEY_BINDINGS.md) — the explorer's pin/edit keys.
- [BUILD_RESUME.md](BUILD_RESUME.md) — the `btcl -b` / `btcl -r` workflow guide.
- [TCL_FRONT_END.md](TCL_FRONT_END.md) — the bridge, the full `-i` machinery, and
  stage resumes.
- [BDB_REFERENCE.md](BDB_REFERENCE.md) — what persists, and how
  `load_pipeline` restores it.
