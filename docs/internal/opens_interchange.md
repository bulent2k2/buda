# Open items — file I/O and interfaces

What the LEF / DEF / Verilog / GDS readers and writers, and the front ends
that drive them, deliberately do **not** do yet. The built interface is
described in [`lefdef_interface_plan.md`](lefdef_interface_plan.md) (phases
0–5, all landed), [`engine_units.md`](engine_units.md),
[`gds_oa_interchange.md`](gds_oa_interchange.md),
[`message_ids.md`](message_ids.md) and [`../TCL_FRONT_END.md`](../TCL_FRONT_END.md);
this page is the backlog behind them.

Snapshot index — last verified against `main`: **2026-08-12**.  Everything
here has landed except **item 8, the packaged wheel** — which is a CI and
packaging project rather than an interchange defect, and is the only entry
still owed code.  Item 9 closed WITHOUT any: its last residual turned out to
be a provenance accident with no dependants, and saying so precisely is the
resolution.  Each item states what was missing, why it was left rather than
forgotten, and where to start.  Every claim below was reproduced on `main`
before being written down; the reproduction is given so a reader can
re-derive it rather than trust it.

Resolved items are kept in place, struck through, rather than moved to the
resolved table at the bottom. Each entry records where this page's **own
first description was wrong** — item 1 about the merge case, and about the
fix it originally proposed, which would have been the wrong fix; item 2
about the severity, having called a silent SHORT a width collapse; item 3
reserved a size-rule choice that measurement showed to be no choice at all.
That is worth more than a tidy list, and the pattern is worth naming: the
first description of a fault is written from the symptom you noticed, and
the symptom is rarely the whole fault.

There are two working vehicles, and they are deliberately at opposite ends
of the scale. **[`flow/def/`](../../flow/def/)** is the smallest design that
exercises the path — 36 nets, 4-bit buses, one bus per level; most of the
items below were found by building it. **[`flow/rv/`](../../flow/rv/)** is
a dual-core RV32-shaped SoC — 1230 nets, five levels, a 32-bit datapath,
part-selects that are not zero-based — and found items 10 and 11. That is
the argument for having both: a small vehicle finds the faults that are
about *structure*, and a large one finds the faults that only appear once a
quantity stops being one.

---

## 1. ~~The netlist reader drops blackbox instances with ordinary names~~ — RESOLVED 2026-08-09

An instance of a module the netlist does not define was kept only if its
instance name was **backslash-escaped**, so a hard macro instantiated the
normal way — `fakeram45_256x16 u_mem (...)` — read as a standard cell and was
dropped. The macro exception behind that test (a cell name containing a
lowercase letter) sat *inside* the escaped-name branch and was therefore
unreachable for the netlists that need it.

**Correcting what this page first said about the merge case.** "A hard macro
vanishes" is right for a Verilog-only import (`components: []`), and *wrong*
for a DEF+Verilog merge, where the DEF's row survives — it is simply never
given a parent. That is not milder, it is worse and quieter: the macro sits
orphaned at depth 0, so its **container has no children at all**, cannot be
sized by `derive_container_bboxes`, gets no busterm, and the routing
interface loses a whole level. One dropped instance, a missing level, no
diagnostic.

**The fix asks the technology, which states the answer outright.** A LEF
`MACRO … CLASS` other than `CORE` *is* the hard-macro declaration (an absent
CLASS reads as CORE, LEF's own default), so the class is persisted on the
`cell` row at import (`cell.cls`, schema v24) and consulted at elaboration.
No cell-class heuristic, and no dependence on how the instance is spelled.
The legacy escaped-name rule stands unchanged behind it for an import with no
LEF to ask.

Deliberately **not** done, two attempts recorded because each looked right:

- Lifting the lowercase-letter test out of the escape branch, which is what
  this page originally suggested. Std cells are all-lowercase in some
  libraries (`sky130_fd_sc_hd__inv_1`), so that rule keeps every gate in
  exactly the netlists the filter exists to protect against.
- **Asking the placement** — "keep an instance whose name the DEF already
  contains" — which this page recommended next and which shipped briefly.
  It is right on a macro-only DEF and wrong on every real one: a DEF for a
  gate-level design lists *every standard cell* in `COMPONENTS`, so the test
  is true of every buffer and flop and the filter admits the whole netlist.
  Measured on a 5-component merge: 4 of 4 std cells elaborated into
  component, pin and net rows (Codex P1 on #654). The lesson is the one this
  page keeps re-learning — a signal that correlates with the right answer on
  the design in front of you is not the right answer.

**And the count is now always stated** (`BUDA-1608`), with the cell *kinds*,
because the filter remains a heuristic on the no-LEF path and an instance
that silently never existed is indistinguishable from a design that never had
one. `import_verilog` returns a `VerilogImportStats`; the named-kinds list
caps at eight and `skipped_kinds` carries the true total, so a truncated list
says so rather than presenting eight kinds as the whole story.

Pinned by `test_bdb_import_edges.py`: the macro joins the hierarchy, the
container it would have orphaned is sized, a gate-level merge keeps only the
macro, no-LEF filtering is unchanged, and the census names distinct kinds and
admits when it is truncated.

## 2. ~~A vector port map collapses to one net~~ — RESOLVED 2026-08-10 (both halves)

```verilog
wire [1:0] w;
sub u0 (.a0(w[0]), .a1(w[1]));
```

Reproduced on `main`: `nets: ['w']`. `parse_portmap` resolved a bit-select to
its **base name**, so a 4-bit bus arrived as a single net.

**Correcting what this page first called it.** "A silent width collapse" is
the smaller half. The bits of a bus are *different nets*, and they all landed
on one — so the reader also **shorted the bus**, reporting pins joined that
the netlist keeps apart:

```
  u0 .a0 -> w        u0 .a1 -> w        (two bits, one net)
```

A width error understates it: a short is a correctness fault, and nothing
downstream could see either one. Measured on `flow/def/chip.v` once its buses
were written as vectors, the old reader routes **18 bit-wires where the
design has 60** — 15 nets against the DEF's declared 36 — and `check_design`
reports "Success: no violations found" at all three stages. Nothing can tell
a bus that was never read from a bus that never existed.

**The fix keeps the selector and resolves base-then-select.** `w[0]` is net
`w[0]`, which is also what the DEF side has always called it, so the merge
lines up (36 declared, 36 imported, 36 after the merge). The identifier is
resolved through the hierarchy context and the selector re-applied to the
*result*, so a parent's `.p(w)` plus a child's `p[0]` reach `w[0]` — the same
net the parent's own `.a0(w[0])` names. `net_props.bus_name` / `bit_index`
are filled in from the stored name by one shared helper, so a DEF net and the
Verilog net it merges with cannot be classified differently.

Three sub-cases, modelled to three depths and each **counted** so a caller
can tell which it got:

| Shape | Handling |
|---|---|
| `.a(w[0])` bit-select | exact — one net per bit |
| `.a(w[3:0])` part-select | a pin per bit **the formal port can take**. Not the base name, which would strand this pin off the bit-selects; not a net called `w[3:0]`, which the netlist never declared |
| `.a(w)` whole vector | a pin per bit of the port, on the matching bit of the actual (see the second half below) |
| `{a,b}`, `w[i]` | still unresolved, now **warned** (`BUDA-1610`) — each is an open, and guessing which net a concatenation means would place a wire the netlist never asked for |

**How much of a part-select lands is the FORMAL's width, not the select's.**
Verilog width-adapts a port connection, so `.s(w[3:0])` on a scalar `input s`
connects bit 0 and nothing else. Expanding to the select's width regardless
put four nets on that one pin and reported three connections the netlist does
not have — the same invention as the short this item is about, in the other
direction (Codex P1 on #661). Port ranges are now kept (`input [3:0] a` is
width 4) and bits are taken LSB-first, the end Verilog aligns.

An **unknown** formal width — an undefined module declares no ports here —
is not guessed either way: bit 0 is connected, because it is connected for
every width ≥ 1, and the rest is reported (`BUDA-1612`).

Two smaller faults from the same review, both reproduced first:

* `w[ 0 ]` and `w[3 : 0]` are legal and their indices *are* literal, but the
  digit test ran untrimmed, so they were classified as unresolvable and their
  pins opened — a reader limitation reported as a design property.
* `unresolved_conns` was counted while each module DEFINITION was parsed, not
  as instances elaborate. A module instantiated three times reported one open,
  and a module never instantiated reported one it does not have. Measured
  together: a design losing **3** connections reported **2**. The counter now
  runs at elaboration, like `bit_selects` and `part_selects`.

The trap, which cost a test: `[` is not always a select. A Verilog **escaped**
identifier runs from `\` to whitespace and takes its brackets with it, so
`\w[0]` is an identifier *named* `w[0]`. For that spelling both readings
converge — and converging is what makes the merge work, since a DEF writes a
bus bit as `\w\[0\]`. They part on a name the select parser cannot read, like
the 2-D element `\w[1][0]`, whose "index" is `1][0`: read as a select it is
unresolvable and the pin silently loses its net.

### The second half: a vector PORT is N pins

A port is as many **pins** as it is bits wide. Modelled as one pin named `a`,
a whole-vector connection `.a(w)` put that single pin on a single net `w`, so
an 8-net design arrived as 2 nets and 2 pins — the same collapse, entered
from the port side.

**And the first half caused a worse shape before this landed.** Reproduced:

```verilog
wire [3:0] w;
drv u_d (.z(w));                                       // whole vector
rcv u_r (.a0(w[0]), .a1(w[1]), .a2(w[2]), .a3(w[3]));  // bit-selects
```

Before either half both spellings collapsed to a net `w` — wrongly joined,
but joined. Once bit-selects resolved per bit and ports did not, they *split*:
five nets where the design has four, the driver alone on a net no receiver
touches, every bit of the bus undriven. One kind of wrong became another. Only
wiring both sides per bit makes the two spellings name the same nets, which is
the argument for finishing the item rather than leaving half of it.

Three things this needed:

* **`cell_pin` rows per bit** — `a[0]`..`a[3]` — named as the instance pins
  are, so direction inference still matches them one to one.
* **Wire widths.** A whole-signal reference carries no width: `.a(w)` says
  nothing about how wide `w` is. Declarations (`wire [3:0] w;`) are now read,
  and a connection is `min(formal, actual)` bits, LSB-aligned. An undeclared
  signal is an implicit wire, which Verilog defines as 1 bit, so the default
  needs no guess — and a scalar on a 4-bit port connects bit 0 rather than
  inventing `s[1]`.
* **Declared indices.** A port's pins carry the indices it declares, so
  `input [7:4] a` is `a[4]`..`a[7]`; numbering from 0 put them where no
  LEF/DEF pin of that macro is, which is the very matching this item exists
  to fix. The same for a local `wire [7:4] w`.
* **Unconnected upper bits stay unconnected.** Width adaptation leaves a wider
  formal's high bits with nothing to connect to, and deriving them from the
  actual's base invented `s[3]` for a SCALAR `s` — a bit of a signal that has
  no bits. "Not mapped" and "mapped to nothing" are recorded as the different
  facts they are.
* **A per-bit context.** Port bits are mapped exactly, offset slices included,
  which base-plus-selector composition cannot express. Without it a module
  handed `w[7:4]` and passing the whole port down reconnected its child to
  `w[3:0]` — not an open, a **wrong net**, the one failure a reader must never
  produce quietly. This is what **retired BUDA-1611**: with an exact mapping
  there is no offset left to warn about, and a warning that fires on correct
  behaviour is worse than none.

*What remains:* nothing in this item. `flow/def/` is now vector end to end —
LEF pins `A[0]`..`A[3]` (the LEF already declared `BUSBITCHARS "[]"`), DEF
nets and die `PINS` named `din[0]`, and a netlist written the way netlists are
written. It routes **identically** to both earlier spellings — 15 bundles,
273,800 abstract and 870,800 detailed WL, 60 bit-wires, `check_design` clean —
which is what "the reader reads what the netlist says" should look like: the
design never changed, only how it was written.

Pinned by `test_bdb_import_edges.py` (bit-select, hierarchy resolution,
`net_props` classification, escaped identifier, part-select expansion) and by
`test_def_hier_flow.py`, which asserts each internal bus arrives 4 bits wide
and the design routes 36 nets.

## 3. ~~The GDS round trip loses what the merge invented~~ — RESOLVED 2026-08-10

**The honest question, answered by measurement.** This item asked what size a
derived container cell gets — max over instances, or per-instance geometry in
the export — and reserved the choice.  Measured on the vehicle before
choosing: every derived cell's instances are **size-uniform** (`__PORT__` ×8
at 500×500, `blk` ×4 at 93000×27000, `quad` ×2 at 96000×75000 — containers
come from congruent templates, ports from one PIN template), so the common
instance size IS the cell size and the two options coincide.  The rule
implemented is *the common size, written only where none exists*: a cell
whose instances disagree, or has an unplaced instance, stays 0×0 and the
export's dim-mismatch warning keeps that gap visible — inventing a max would
claim a footprint no instance has.

Three fixes, each where the size is derived:

* **containers** — `derive_container_bboxes` writes the derived size back to
  `cell.width/height` (guarded `WHERE width=0 AND height=0`, so a LEF SIZE or
  hand resize is never overwritten; 90°-oriented instances compared in the
  cell frame);
* **`__PORT__`** — `import_def_lef` Phase 3d creates the cell row, sized to
  the union of the port shapes;
* **the top module** — `import_verilog` records `meta.verilog_top`, and
  `export_gds` adopts that (uninstantiated, sizeless) cell as the top
  structure's NAME instead of emitting an empty orphan beside a synthetic
  `top` — direct evidence where the existing die-size adoption is
  circumstantial.

On the vehicle the re-import went from 8× `reference to undefined structure
'__PORT__'`, `structure 'chip' has no geometry anywhere`, two tops, 8 labels
skipped and **0 nets recovered** — to zero warnings, one top named `chip`,
all 8 ports back and **all 8 nets recovered**.  The export-side "4
placements have a bbox that differs" warning is gone with it.  Pinned by
`test_merge_scaffolding_survives_the_round_trip` (a miniature merge, all
three losses asserted individually) and
`test_disagreeing_instance_sizes_leave_the_cell_unsized` (the rule's
refusal half).

<details><summary>The original item, for the record</summary>

`export_gds` writes cell outlines from `cell.width/height`, and two kinds of
cell created during a DEF+Verilog merge have neither:

* the synthetic `__PORT__` cell behind Phase 3d's boundary components — no
  `cell` row at all, so the SREFs reference an undefined structure; and
* the container cells `import_verilog` creates (`INSERT OR IGNORE INTO
  cell(name,width,height) VALUES(?,0,0)`).

On re-import that reads as `reference to undefined structure '__PORT__'`
(×8), `structure 'chip' has no geometry anywhere`, and all 8 net labels
skipped as "outside every component". The routing geometry round-trips; the
scaffolding does not.

*Where to start:* the honest question first — a `cell` is one size and its
instances may differ, so what size does a derived container cell get? Two
defensible answers (max over instances; or per-instance geometry in the
export rather than per-cell) and the choice should be made deliberately
rather than by whoever writes the patch. `derive_container_bboxes` is the
natural place to fill in a size once the rule is chosen.

Related and cosmetic: `export_gds` warns that 4 placements have a bbox
differing from the oriented cell footprint — the same 0×0 container cells.

</details>

## 4. ~~Relative paths resolve against two different roots~~ — RESOLVED 2026-08-10

**One root now: the script's directory.** Every path-taking command —
`open_bdb`, `save_bdb`, `source`, the `import_*` and `export_*` commands,
`emit_guides`, `def_gds_layer file` — resolves a relative path through one
shared rule (`resolve_script_path`, `buda_session/util.py`): against the
directory of the enclosing script (the innermost `source`d file), falling
back to the CWD only when no script is running (interactive, the Tcl front
end, the Python API — there is no script to be relative to). A `.buda`
script is a location-independent artifact; `flow/def/chip.buda` now runs
from any directory, and both its self-consistent configurations below are
the SAME configuration.

**What decided it.** The original *where to start* said "picking either root
breaks existing scripts in the other direction" — measured, the two
directions were wildly asymmetric: exactly **one** script in the repo used
the CWD-rooted commands relatively (`flow/def/chip.buda`, five lines,
converted in the same commit), against **100+** script-relative `source`
lines in `flow/`. The fork looked balanced on the page and was 1-vs-100 in
reality. The `set_path_base cwd|script` alternative was rejected for making
the page-invisibility *worse*: behavior would depend on a declaration
possibly several `source` levels away.

**Migration aid for out-of-repo scripts** written against the old CWD rule:
a READ whose script-relative candidate does not exist, where the CWD-relative
one does, gets `BUDA-1609` naming both roots and the fix — then fails on the
resolved path anyway. The rule stays deterministic; only the diagnosis is
added. Writes cannot be disambiguated by existence and simply land next to
the script, which is where `save_bdb` always put them.

Pinned by `test_path_roots.py`, whose **previous revision pinned the split
itself** — the flip is a visible contract change in that file's history. The
sharp edge got its own test: `open_bdb` and `import_verilog` on adjacent
lines now satisfied by one file next to the script, and all output families
(`save_bdb` + exports) landing in the same `out/` from any CWD.

<details><summary>The original item, for the record</summary>

In one script, with no visual difference between them:

| Command | Relative path is resolved against |
|---|---|
| `open_bdb`, `save_bdb`, `source` | the **script's directory** |
| `import_def_lef`, `import_verilog`, `import_gds` | the **CWD** |
| `emit_guides`, `export_def_blockages`, `export_gds` | the **CWD** |

Verified in `bdb_cmds.py` (`_script_stack` is consulted at the `open_bdb`
and `save_bdb` sites and nowhere else) and `control_cmds.py`.

Note the first two rows: **`open_bdb` and `import_def_lef` are usually
adjacent lines in the same script** and resolve differently. That is the
sharp edge — `flow/def/chip.buda` documents it in a comment because it had
to. A file that *is* written, somewhere other than where the script says, is
worse than one that is not written at all.

**What this means for running the vehicle.** Because the CWD-rooted commands
in `chip.buda` are written `flow/def/…` and the script-rooted `save_bdb
out/…` lands in `flow/def/out/`, the two families agree on `flow/def/out/`
**only from the repo root** — so `chip.buda` runs from there and nowhere
else (`bin/buda flow/def/chip`, the ReadMe form). `cd flow/def && buda
chip.buda` breaks the imports: `flow/def/chip.def` does not exist relative to
that CWD. The *other* self-consistent config is to make **every CWD-rooted
path** `flow/def/`-relative and run from inside `flow/def/` — not the imports
alone: bare imports (`chip.def`) with the exports left at `flow/def/out/…`
would resolve those to `flow/def/flow/def/out/…` and still fail. So it is
`import_def_lef chip.def …` *and* `emit_guides out/… ` / `export_* out/…`
together, at which point the script-rooted `save_bdb out/…` lines up on the
same `flow/def/out/`. The fork is real, and which config a copy is in is
invisible on the page.

On macOS the windowed `bin/buda flow/def/chip` used to fail here for a second
reason: the launcher relaunches through a per-cell `.app`, and `open` starts
it from `/`, so the CWD-rooted imports resolved against `/` even when the
shell was at the repo root (the `--no-viz` path skipped the relaunch, so only
the windowed form broke). Fixed — `bin/buda` now bakes the launch CWD into
the generated launcher (`cd "$PWD"`), so the `.app` run matches a direct run.
Any future flow with CWD-relative paths inherits that; do not drop the `cd`.

**Companion (FIXED) — the output directory was not created.** `emit_guides` /
`export_def_blockages` / `export_gds` used to open their destination without a
`mkdir -p`, and `flow/def/out/` is git-ignored (so absent on a fresh
checkout). The ReadMe command died on the first export with
`FileNotFoundError: 'flow/def/out/chip_guides.json'` — *after* the pipeline
had routed cleanly, so it read as a routing success that then failed to write.
Same family as the root split (a path that resolves somewhere the run cannot
write), so it lived here rather than in §9. **Now fixed**: the three writers
call `ensure_parent_dir` (`buda_session/util.py`) to `mkdir -p` their parent
before writing, so the ReadMe command works on a fresh checkout with no manual
`mkdir` — all six artifacts land (guides json/csv/tcl, advisory DEF, GDS, and
the `save_bdb` BDB). This was the writer half only; the root split above is
the part still open. `save_bdb <path>` (save-as) creates its snapshot's parent
too, via the same `ensure_parent_dir`, so it no longer depends on an earlier
export having made the directory.

*Where to start:* not a one-line change — picking either root breaks
existing scripts in the other direction, and `source ../tracks/tracks.buda`
(script-relative) is used throughout `flow/hbundles/`. The likely shape is
script-relative everywhere with a deprecation pass, or an explicit
`set_path_base cwd|script`. Worth a decision before a patch.

</details>

## 5. ~~The DEF reader buffers the whole design~~ — RESOLVED 2026-08-10 (streaming import, both blockers cleared)

`read_def` held the file **once** (three times before PR #650), but the
parsed model dominated: measured 2026-08-09 — a 19.7 MB / 1.02 M-line DEF
with 340 k components cost **96 MB peak, 4.9× the file size**, most of it
components at ~220 B each. Budget **~5×**.

True streaming meant inserting DB rows as entries parse, which
`import_def_lef` could not do for two concrete reasons, cleared in order:

1. ~~it walks `def.components` a **second** time to place macro `OBS`
   keepouts~~ **CLEARED, 2026-08-10** (#668): the OBS collection
   (orientation transform included) runs inside the one COMPONENTS visit,
   right after the HALO — a reader that hands each component to a sink
   cannot be walked twice. Identical results (same keepouts, different
   order in `stats.keepouts`; the census counts by kind), −7% peak RSS on
   its own.
2. ~~net resolution needs the name index built from the full component
   list~~ **CLEARED, 2026-08-10**: the index it actually needs is one the
   database maintains anyway — `component.name` is UNIQUE, and one probe of
   that index returns the id, cell and layout-unit placement, which is
   everything the in-memory `comp_by_name` map supplied. The map was a
   *duplicate* of the table it had just written.

**The built shape.** `read_def` takes an optional `DefStreamSink`:
COMPONENTS and NETS entries — the two vectors that dominated the model —
are handed over as they parse and never retained; everything else (PINS,
TRACKS, BLOCKAGES, NDRs, the census) is small and stays buffered, so the
sink-less call (and the Python `read_def` binding) is unchanged.
`import_def_lef` inserts each component (row + HALO + OBS keepouts) at
delivery and resolves each net's connections against the DB; a connection
that cannot resolve mid-file is **deferred, not dropped** — die-port conns
always are (their boundary components are synthesized from PINS after the
parse), and so is every conn of a file whose NETS precede its COMPONENTS,
so correctness does not hang on the spec's section order. The design wipe
moved inside the import's transaction, so a malformed DEF **rolls back**
— the old "parse error leaves the previous design intact" contract holds
by a different mechanism. One new hard edge, stated rather than implied: a
`UNITS` statement after a streamed entry is a parse error (delivered
coordinates were converted under the divisor in force; two scales in one
import cannot be repaired retroactively), where the buffered reader
tolerated any order. DEF 5.8 puts UNITS in the header, so no real file
changes behavior.

**Measured** on a 25.2 MB / 1.0 M-line synthetic, 200 k OBS-bearing
components + 200 k nets (the deliberately OBS-heavy vehicle from blocker
1's A/B): peak import RSS **221 → 86 MB, 8.8× → 3.4× the file** — under
the budget — with import runtime a wash (4.4–4.8 s both sides: the
b-tree probes cost what the map lookups did). The remaining ~86 MB is the
one text buffer (25 MB), the keepout list the session applies, and
SQLite's transaction pages — data that is genuinely retained, which is
where this item's own note said the memory should be going.

Pinned by three streaming-contract tests in `test_def_import.py`:
section order does not change what resolves, a malformed DEF rolls back
to the previous design, late UNITS is a hard error.

## 6. ~~Script-declared distances are not scaled by the import scale~~ — RESOLVED 2026-08-10 (the ergonomic half; the engine behavior stays, deliberately)

The engine half is UNCHANGED and stays deliberate: `set_import_scale`
rescales **imported** geometry only, which is what keeps the ~59 downstream
`int(round())` sites correct by construction ([`engine_units.md`](engine_units.md)),
and `set_unit_check` remains the guard that turns a mixed-scale design into
a stop.  What this item actually asked for was its own *where to start*: an
ergonomic spelling that writes the intent down.

That spelling is the **`um` suffix**, accepted wherever a script states a
distance — `corner_margin dx 2um`, `set_min_stub_length 0.5um`,
`def_track_pattern 3 0 SIGNAL 0.07um 0.12um` (repetition groups compose),
`add_block`/`add_keepout`/`add_grid_override` coordinates, `detour_channel`,
`set_track_pitch` — converted through the declared `lu_per_um` at parse
time (`require_distance`, `buda_cmds/_options.py`).  A bare number stays a
layout-unit distance, byte-identical to before.  The contract is loud at
its edges: an integer-grid coordinate whose conversion misses the grid is
refused naming the scale (0.5um at scale 1 is 0.5 lu — an error, not a
truncation), `xum` is an error, and `set_import_scale` declared AFTER a
suffixed distance warns that the earlier values resolved under the previous
scale.  `dbu(...)` was considered and dropped: under `set_import_scale dbu`
a layout unit *is* a DBU, so a bare number already says it, and under any
other scale the DEF's DBU count is not knowable at parse time.

Pinned by `test_um_distances.py` (scale conversion, group composition,
integer-grid refusal, the late-scale warning, and bare numbers untouched).

## 7. ~~DEF `NONDEFAULTRULES` are read but not attached~~ — RESOLVED 2026-08-10

The translation job the item predicted, done as one: the reader now parses
each rule's **content** (`+ LAYER <l> WIDTH <w> [SPACING <s>]`, per-clause,
DBU — `DefNdr` in `def_io.h`, carried out on `DefImportStats` with
`net_ndrs` and `def_units`), and `translate_def_ndrs`
(`buda_cmds/ndr_cmds.py`) converts to BUDA's multiplier model against the
LEF defaults: `multiplier = (dbu / units) / lef_default`.  A rule whose
layers AGREE on the multiplier (1%) becomes `def_ndr <name> width x<m>
[spacing x<m>] layers <csv>`; each net's `+ NONDEFAULTRULE` becomes a
`set_ndr <net> <rule>` scope.

**What refuses, loudly (BUDA-1613)** — the translation is faithful or
absent, never approximate: per-layer multipliers that disagree (one `x<N>`
cannot state them; a max would claim a rule the DEF never wrote), a layer
with no LEF default WIDTH (or SPACING when the rule states one), an
undeclared layer, a rule with no LAYER clause.  A clause with no BUDA model
(`VIA`, `MINCUTS`, …) is noted and the rule translated without it; a net
asking for an untranslated rule is told by name.  And because `set_ndr`
scopes are PREFIXES, a net name that prefixes another net's name reports
the over-match instead of silently governing it.

Pinned by `test_def_ndr_import.py` (the x2 translation, per-net scopes,
each refusal, the prefix shadow, and a rule-free DEF declaring nothing).

## 8. Packaged wheel

Phase 5 shipped the Tcl front end and the logging conventions; the wheel was
not built. It needs a build of the C++ extension per platform and per Python
(`cibuildwheel`, plus `auditwheel`/`delocate`), which is its own CI problem
with its own gates — and the repo's `-march=native` default has to be pinned
per wheel or the artifact crashes with an illegal instruction on an older
CPU.

**The PREREQUISITE landed 2026-08-12** (still no wheels, deliberately): a
checkout is installable with the standard tooling — `pip install .` and
`pip install -e .`, three console scripts, `import buda` with nothing on
`PYTHONPATH` — via a `pyproject.toml` on `scikit-build-core`. It exists
because every distribution decision above has to be built on it, and because
doing it now is what flushes out the real work while it is cheap: BUDA's
import contract is a set of DIRECTORIES (`build`, `src`, `tools`, the repo
root) rather than a package, and none of those three names may be claimed in
`site-packages` — so a wheel has to fold the layer into one directory and put
it back on `sys.path` at import time, which `buda_runtime/__init__.py` is.
`-march` is already pinned there (`BUDA_ARCH=none` for the pip path, the same
reason CI pins it), so the illegal-instruction hazard above is answered for
the build path that exists; what is left is the matrix. One measured
correction on the way: an editable install initially COPIED the Python layer
into site-packages and reported success while serving a stale snapshot —
fixed by declaring `buda_runtime` as the wheel's package so it is redirected.
Design, the residuals, and the by-hand verification table:
[packaging.md](packaging.md).

The other half of that plan bullet — `BUDA_NO_APP=1` as the batch default on
macOS — is **already satisfied**: `bin/buda`'s Darwin relaunch requires all
three of stdin/stdout/stderr to be TTYs and skips `--no-viz`, so a redirected
or batch run already falls through to the direct launch.

## 9. ~~Smaller residuals~~ — RESOLVED 2026-08-12

* ~~**`demo/ariane` is a mismatched pair.**~~ **RESOLVED 2026-08-12 — no
  code is owed.** This page framed it as a question the owner had to
  settle: its DEF instantiates 133 × `fakeram45_256x16` while its LEF
  defines only `sram_asap7_16x256_1rw`, so *which file is authoritative?*

  That was the wrong question, and the repo already answers it. The DEF is
  the TILOS MacroPlacement **NanGate45** ariane133 benchmark
  (`demo/ariane/ariane.buda` cites it); the LEF is an **ASAP7** SRAM, and
  `demo/ariane/ReadMe.md` records how it arrived — *"ariane.lef (got it
  later)"*. They are two different technologies. Neither file is wrong;
  the LEF was simply never the LEF for this DEF, so there is nothing to
  choose between.

  And nothing depends on them agreeing, which is what makes this closable
  rather than deferred:

  | | |
  |---|---|
  | flows | **none import the pair** — `ariane.buda` / `ariane_core.buda` are hand-written floorplans derived from the benchmark, with no `import_def_lef` anywhere in `demo/` |
  | tests | one reads the DEF (`test_def_reader.py::test_the_checked_in_ariane_def_gives_up_nothing`, 133 components / 495 pins / 20 TRACKS / 6 GCELLGRIDs) through the raw `read_def` and **never opens the LEF**, so the mismatch cannot reach it |
  | corpus / CI | neither |

  (`demo/tracks_ariane136.buda` and `demo/gen_ariane136.py`, which two other
  tests do use, are generated 136-macro flows — a different artifact that
  shares only the name.)

  The reader's behaviour is already correct: it refuses the pair unless
  told `allow_missing_footprints`, which is what should happen when a
  design is handed a technology that does not describe it. Under the old
  importer every macro on that 2.7 mm die became a 0.5 µm speck in silence
  — that was the fault, and it is fixed.

  The LEF **stays**: it is a real 45-pin macro sample the LEF reader was
  developed against (`lefdef_interface_plan.md` §2a), and the ReadMe says
  it was kept deliberately. If the pair is ever wanted as a working import
  vehicle it needs the matching NanGate45 `fakeram45_256x16` LEF — but
  `flow/def/` and `flow/rv/` were built to cover that path, so it would be
  redundant coverage rather than a gap.
* ~~**The Tcl front end is synchronous.**~~ **RESOLVED 2026-08-10** — the
  GUI face landed as three composable pieces: opt-in **streaming**
  (`__stream on` → `OUT` progress frames, with the final frame carrying
  only the unstreamed tail so `buda::output` is identical in every mode; a
  client that never opts in never sees an `OUT` frame, so the one-frame
  protocol remains the whole contract for existing flows), **async
  driving** (`buda::async -done …` on a `fileevent` reader, so a Tk event
  loop stays live; plus `buda::wait` / `buda::running` / `buda::onprogress`),
  and **cancellation** — deliberately NOT a protocol feature, because POSIX
  already has one: `buda::cancel` sends SIGINT, Python raises
  `KeyboardInterrupt` at the next bytecode boundary, and the command fails
  as an ordinary `ERR` with the session alive holding what it had
  committed.  The boundary is stated rather than implied: Python-level
  loops (`source`, healer iterations) cancel promptly; one long C++ call
  returns before the interrupt lands.  SIGINT is deferred while a frame is
  on the wire so a cancel cannot tear a frame in half.  Pinned by
  `test_tcl_async.py`, incl. a real 150k-line `source` cancelled mid-run
  with partial state surviving.
* ~~**The Tcl protocol assumes nothing writes to fd 1 directly.**~~
  **RESOLVED 2026-08-10** — the assumption is gone rather than defended: at
  startup the server duplicates fd 1 to a private descriptor the protocol
  alone writes and repoints fd 1 at **stderr**, so a library writing the
  raw descriptor lands beside the diagnostics — visible, in order, outside
  every frame — instead of inside the conversation. "Nothing in BUDA does
  this today" was true, but it was a promise about other people's code;
  making the write harmless BY CONSTRUCTION replaces the promise. Pinned by
  a test whose rogue command `os.write(1, ...)`s mid-command: the frame
  stays well-formed, the junk arrives on stderr.
* ~~**`import_gds` label recovery needs components to land on.**~~
  **RESOLVED 2026-08-11** — the skip stays, because it is correct: a label
  on nothing has nothing to pin to, and inventing a pin would place a
  connection the layout never stated.  What was missing was the
  diagnostic's quality on the repo's own terms: the warning was
  unidentified prose (nothing to gate on, though each skip is a net
  silently missing from the recovered design), and it named the label but
  not WHERE it was or HOW it missed — yet the fixable cause is a NEAR
  miss, a label a fraction of a µm off a component edge from a scale or
  rounding slip, indistinguishable in the old text from a genuinely stray
  label.  Now: **BUDA-1614** (catalogued, counted), and the message names
  the label's position and the nearest component with its distance —
  `label 'x' at (500, 500) is outside every component — skipped (nearest:
  'leaf', 495 um away)` — so near-miss and stray read differently and the
  first is actionable.  (The case that originally made this bite — port
  labels landing on nothing because `__PORT__` never round-tripped — was
  item 3, resolved separately.)

## 10. ~~A die-port endpoint's depth was counted off its name~~ — RESOLVED 2026-08-10

`import_def_lef` names the boundary component it synthesizes for a DEF
`PIN` **`PIN/<port>`**, at **depth 0**. That is a name containing a slash
which is not a hierarchy separator, and two places in the hier pipeline —
the cross-block generation case in `_hier_topo_task`, and the frame
resolver the verifier reads — recovered an endpoint's depth by counting
slashes in it.

So a bundle *driven by* a die port had its routing frame built at **depth
1**, a level holding neither endpoint. It came out of
`generate_hier_topologies` with **zero candidates**, and at verify time
*both* its busterms read as "outside block face" — which is what a missing
block looks like, since a block that is not in the frame has no bounds to
be inside of. Neither message names the cause.

Reproduced on `flow/rv/soc.buda` (restore `return src.count('/')` to see
it): the **32 `boot` bundles**, one per input pad, generate an empty pool.
32 nets get no wire, and the end-of-flow `check_design` still reports
**"Success: no violations found"** — a bundle with no candidates has no
segments, so there is nothing left to find a violation in. The only place
it shows in an artifact is the corridor manifest, which simply does not
mention those nets.

`flow/def/` never showed it because its die ports connect within one level,
where the wrong depth and the right one coincide.

*Fixed* by asking the component instead of parsing its name —
`BudaSession._endpoint_depth` in `src/buda_session/hier.py`, used at both
sites, falling back to the slash count only for a name with no component
(a synthetic endpoint). The lesson is the one this page keeps recording:
a naming convention invented by one layer becomes a parsing rule in
another, and the two are only equal until someone adds a name that breaks
the coincidence.

## 11. ~~A die-port bus routes as one bundle per bit~~ — RESOLVED 2026-08-10

`soc.v` drives a 32-bit `dbg` bus from one mux to 32 die pads, and takes a
32-bit `boot` bus from 32 pads into one memory. The DEF has one `PINS`
entry per bit — correct, that is what a DEF has — so `import_def_lef`
synthesizes 32 boundary components per bus and the bundler sees 32 nets
with 32 *different* endpoint blocks. Under STRICT: 64 of `flow/rv`'s 70
depth-0 bundles are a single port bit.

**Correcting what this page first said**, which was wrong in the way that
matters: it claimed the endpoint sets differ so CONVERGENT and
BIDIRECTIONAL leave them at 32 too. Measured, CONVERGENT **already merges
`boot`** — 32 pads into one memory is a fan-in, exactly its case — taking
depth-0 from 70 bundles to 38. Only `dbg` stayed split. The gap was one
DIRECTION, not the whole case, and the page had described the symptom
(a port bus does not bundle) as though it were the fault.

**The fault is that the lattice is asymmetric.** N nets arriving at one
sink from N places bundle; the same N nets leaving one driver for N places
do not, under any strategy. That is the same physical object drawn
backwards.

**The fix is the missing mirror: `DIVERGENT`.** Group by the shared
DRIVER, receivers ignored — CONVERGENT reflected. It is realized as a
per-bit tapered tree rooted at the driver (reason `FANOUT:root|TO:leaves`,
the `FANIN:root|FROM:leaves` twin), and it needed no new taper machinery:
`derive_fanin_seg_bits` already BFSes driver→receiver, which is the
direction a fan-out runs in, so the same function tapers both with its
arguments unchanged.

Measured on `flow/rv` (`run_hier_bundler depth 4 DIVERGENT`):

| | STRICT | DIVERGENT |
|---|---|---|
| bundles | 127 | **78** |
| `dbg` | 32 × 1 net | **1 × 32 nets** |
| abstract WL | 31,234,654 | **19,104,008 (−38.8%)** |
| detailed WL | 409,819,470 | 430,511,600 (+5.0%) |
| endpoint | clean | clean, 0 unplaced |

It bundles far more than the port bus — depth-2 goes 30 → 16 — because
fan-outs are everywhere in a datapath. The abstract win is large because a
fan-out tree shares a trunk where N bundles each reserved their own; the
detailed cost is what the tree pays to reach scattered leaves. Both are
real, which is why this is **opt-in and not a new default**.

Two deliberate restraints:

* **`COMBINED` does not include it.** COMBINED is the join of the
  relations safe to apply unasked, and shared-driver is a far weaker
  signal than shared-receiver — a clock buffer's 200 sinks are not a bus.
  Folding it in would silently re-bundle every existing COMBINED flow.
  Ask for `DIVERGENT` by name; hold a prefix out with `set_bundling
  <prefix> no_divergent`.
* **The port-bus-grouping alternative was not taken.** Grouping the 32
  pads at import (their `net_props.bus_name` already says they are one
  bus) is narrower and fixes only die ports, and it would have to invent a
  geometry for the merged endpoint — a DEF may place `dbg[0]` and
  `dbg[31]` on opposite edges, and one bbox around them is a block that is
  not there.

One implementation note worth keeping, because it cost the vehicle its
route once already: the grouping key `DRV:<driver>,` must never be emitted
as a bundle REASON. Generation recovers endpoints by parsing the reason,
and a bare driver name carries no receivers — every net with a unique
driver forms its own one-net group under DIVERGENT, so `flow/rv`'s 32
`boot` bundles came out of generation with zero candidates. That is the
exact mirror of the guard the driverless `REC:` key already had, and it is
now written as one.

Pinned by `test_bundler_divergent.py`.

---

## Resolved (by 2026-08-09)

Recorded because each was found by building `flow/def/` and each had passed
the whole suite beforehand — the seam between the importers and the
hierarchy pipeline had no vehicle until then.

| | Was |
|---|---|
| Busterm insertion order | `derive_busterms` **crashed** on a DEF+Verilog merge — `FOREIGN KEY constraint failed`, naming nothing. Components are walked by id and the merge numbers leaves first, so children were inserted before parents. Now depth-ordered. |
| Die ports lost connectivity | `import_verilog` clears the pin table, and a top-level port is not an instance, so Phase 3d's boundary components came out with no pins — 8 nets silently unbundled. Their rows are now saved before the wipe and restored verbatim. |
| Port direction | A boundary component stands in for the world *outside* the die, so an `INPUT` port drives inward. Unflipped, input-port nets had two receivers and no driver. |
| Placed components demoted | An empty `module LEAF (...); endmodule` is how a netlist gives a hard macro its port directions; the merge read it as "container" and stopped the footprint blocking low layers. Leaf-ness now follows what the module *contains*, with the landed Verilog-only rule preserved. |
| Containers had no extent | Neither file has a row for a hierarchical instance, so the routing interface came out with a hole in the middle. New: `derive_container_bboxes`. |
| ANSI port headers | `module top (input a, output b, inout c);` recorded **all three** as `INPUT`, reaching `cell_pin` directions for any ANSI netlist. |
| `PLACEMENT` blockages | Imported as routing keepouts on every layer, forbidding routing the DEF left routable — acute on the round trip, since 4b emits `PLACEMENT + PARTIAL` over its own corridors. |
| `UNPLACED` components | Placed at the die origin in a pile and routed as a floorplan. |
| O(N²) net resolution | 40 k components: **7.97 s → 0.58 s**. |
