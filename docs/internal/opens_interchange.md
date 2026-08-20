# Open items — file I/O and interfaces

What the LEF / DEF / Verilog / GDS readers and writers, and the front ends
that drive them, deliberately do **not** do yet. The built interface is
described in [`lefdef_interface_plan.md`](lefdef_interface_plan.md) (phases
0–5, all landed), [`engine_units.md`](engine_units.md),
[`gds_oa_interchange.md`](gds_oa_interchange.md),
[`message_ids.md`](message_ids.md) and [`../TCL_FRONT_END.md`](../TCL_FRONT_END.md);
this page is the backlog behind them.

Snapshot index — last verified against `main`: **2026-08-14**.  Everything
here has landed except **item 8, the packaged wheel**, which is a CI and
packaging project rather than an interchange defect and is the only entry
still owed code.  Item 13 is the newest, and was found by chasing what
looked like a QoR problem in `flow/ariane133` and turned out to be an
importer defect — the third-vehicle argument again, and a reminder that a
diagnostic reporting ZERO of something is a different claim from a
diagnostic reporting too few.  Item 9 closed WITHOUT any: its last residual turned out to
be a provenance accident with no dependants, and saying so precisely is the
resolution.  Item 12 closed WITH code, but not the code this page first
proposed — see its "the proposed fix was the wrong one" section, which is
the third entry here to record that, and the first where the error was
caught by measuring the geometry before building anything.  Each item states
what was missing, why it was left rather than forgotten, and where to start.
Every claim below was reproduced on `main` before being written down; the
reproduction is given so a reader can re-derive it rather than trust it.

Resolved items are kept in place, struck through, rather than moved to the
resolved table at the bottom. Each entry records where this page's **own
first description was wrong** — item 1 about the merge case, and about the
fix it originally proposed, which would have been the wrong fix; item 2
about the severity, having called a silent SHORT a width collapse; item 3
reserved a size-rule choice that measurement showed to be no choice at all;
item 12 was wrong TWICE about its own fix — it proposed a rectangle union
that would have been work for almost nothing (the rects it meant to merge
have real gaps between them), and then shipped the replacement unconditional
on a false claim that the loci it drops are unreachable.
That is worth more than a tidy list, and the pattern is worth naming: the
first description of a fault is written from the symptom you noticed, and
the symptom is rarely the whole fault.  Item 12 adds two corollaries — the
first *fix* is written from the description, so it inherits the error, and
the cheapest place to catch that is a probe of the real data before any
code; and the argument that a change is FREE deserves more suspicion than
the change itself, because it is the part no gate checks.

There are three working vehicles. Two are deliberately at opposite ends of
the scale: **[`flow/def/`](../../flow/def/)** is the smallest design that
exercises the path — 36 nets, 4-bit buses, one bus per level; most of the
items below were found by building it. **[`flow/rv/`](../../flow/rv/)** is
a dual-core RV32-shaped SoC — 1230 nets, five levels, a 32-bit datapath,
part-selects that are not zero-based — and found items 10 and 11. That is
the argument for having both: a small vehicle finds the faults that are
about *structure*, and a large one finds the faults that only appear once a
quantity stops being one.

The third differs on an axis that is not size. **[`flow/ariane133/`](../../flow/ariane133/)**
is a real 45nm design in a real technology — 5576 nets, 133 SRAM macros, a
127-module netlist — and, unlike the other two, **it is not authored here**;
its inputs are fetched, digest-pinned, from the benchmark suite that also
produced `demo/ariane/ariane.def` (so it is item 9's other half: the same
DEF, finally given the LEF that describes it). It found item 12 immediately,
and that item was invisible to both other vehicles for a reason worth
stating — they author their own LEF, and a hand-written LEF has a handful of
`OBS` rects because a human typed them. Scale was never going to surface it;
only somebody else's file was. It then found item 13 the same way, and that
one had been sitting in the importer since Phase 3c: a hand-written DEF puts
a halo on a macro when the author remembers to, and neither of ours does.

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

## 12. ~~Macro `OBS` import scales with LEF detail, not with the design~~ — RESOLVED 2026-08-14

Phase 3c reads macro `OBS` into keepouts, which is correct — that metal is
occupied and routing over it is a real violation. What was never bounded is
what happens when a technology draws its obstruction finely. **One
`fakeram45_256x16` carries 99 `OBS RECT`s**, so a 133-macro design imports
**13,034 keepouts** to describe 133 rectangles' worth of physics. Every
keepout edge is a Hanan line, and the planner's `GlobalCut` structure is
built per Hanan cut.

The measurement, on `demo/ariane/ariane.def` + the NanGate45
`fakeram45_256x16.lef` (see [`flow/ariane133/`](../../flow/ariane133/)):

| | Hanan x | Hanan y | cells | `run_planner hier 2` |
|---|---|---|---|---|
| as imported | 1996 | 1257 | **2,508,972** | **killed at 50 min** |
| `no_blockages` | 67 | 37 | 2,479 | **0.79 s** |

A **1012× grid** and, on the flow that has to walk it, the difference
between a seven-second run and not finishing. Note the grid grows only ~30× per
axis rather than 99×: the macros are identical and row-aligned, so their
edges coincide and the axis-projected set dedups itself. That is luck of
this floorplan, not a bound — a design mixing macro types gets the full
product.

**Why this was left rather than forgotten.** It is not a reader defect —
the reader imports what the LEF says, and `no_blockages` already declines
the whole category. It is that BUDA has no notion between *"model every
rectangle"* and *"model none of them"*, and the useful answer is in between.
Nor is it ariane-specific: it is a property of any LEF whose macros carry
detailed obstruction, which is most real ones. The existing vehicles missed
it because `flow/def/` and `flow/rv/` both author their own LEF, and a
hand-written LEF has a handful of `OBS` rects because a human typed them.

### The proposed fix was the wrong one, and measuring first is what said so

This page's own first answer was to **merge each macro's `OBS` into covering
rectangles at import** — a per-layer, per-cell rectangle union. It sounded
obviously right: 99 rects to describe "all of metal1–metal4 over the whole
footprint" is clearly redundant.

It is not redundant. Looking at the geometry before writing any union code:

| layer | rects | what they are |
|---|---|---|
| metal1, metal2 | 1 each | the full macro |
| metal3 | 61 | one near-full rect **plus 60 slivers with real gaps** — pin access down the left edge |
| metal4 | 35 | two full-width bands plus vertical strips |
| OVERLAP | 1 | not a routing layer; already skipped |

The metal3 gaps are genuine openings, so a union cannot collapse them: it
would have been real work for almost nothing, and it would have left the
grid exactly as large.

The measurement that redirected it took one probe. On `demo/ariane`:

| | Hanan x | Hanan y | cells |
|---|---|---|---|
| blocks alone | 67 | 37 | **2,479** — *exactly the `no_blockages` grid* |
| keepouts add | +1,929 | +1,220 | 2,508,972 |

The entire blowup is **loci**, not rects. The rects were never the problem.

### What landed — `set_keepout_loci all|outside`, default `all`

Under `outside`, a keepout lying wholly inside a block contributes **no
Hanan loci**. It still blocks, identically; it just stops adding
coordinates.

Three deliberate choices:

* **Geometric, not by provenance.** The rule is "inside a block", not "came
  from `OBS`". `KeepoutZone::inside_block` defaults false, so every
  hand-declared `add_keepout` is untouched.
* **The importer measures containment**, against the instance's placed
  extent, rather than assuming an `OBS` rect is inside its macro. LEF does
  not require one to sit within `SIZE`, and a rect that pokes out is exactly
  the one whose edge *is* a useful locus. It decides this where both
  rectangles are already in hand and it costs nothing; a later consumer
  would have to search every block to recover the same fact.
* **A halo always contributes**, in either mode. It extends beyond the
  footprint by construction — and on ariane those 133 zones are why the grid
  lands at 6,327 cells rather than 2,479.

**Blocking behaviour is unchanged** in both modes. This decides which
coordinates enter the grid and nothing else.

### The second wrong turn: it is not free, and shipped once as though it were

This landed **unconditional** first, on the argument that a position inside
a bracketed footprint is unreachable anyway, so the loci were pure waste.

**That argument is false.** A trunk may cross a block *over-the-cell* —
BUDA does it routinely and this repo's own docs call it a pass-through — so
an interior locus is a perfectly reachable candidate position. Dropping it
removes candidate positions, which is a trade.

`flow/rv/soc_conv_div` demonstrated it within one suite run. Its macros
carry ten `OBS` rects, and bundle 42's selected trunk sat exactly on one of
their edges:

| | `all` (main) | `outside`, unconditional |
|---|---|---|
| bundle 42 | `TRUNK_V@x108000`, **2 segments** | `TRUNK_V@x125000`, **4 segments** |
| dangling metal | 0 | **3,956,000** |

Two things are worth keeping from how that was caught:

* **`test_tapered_bit_spans` caught it; the QoR corpus did not.** The corpus
  reported *0 better, 0 worse, 48 unchanged* straight through this, because
  its metric is overlaps/unplaced/viol_bundles and dangling metal is none of
  those. It is a QoR gate, not a correctness one, and it is not a substitute
  for the suite.
* **The knob is where this should have started.** Every other lever here
  that moves candidate positions — `set_prune_dominated`, `set_dedup_loci`,
  `set_trim_mst_legs`, `set_drop_dangling` — is opt-in for exactly this
  reason, and each says so in its own docs.

Default `all` is byte-identical to before: corpus 48/48 unchanged with
abstract and detailed WL **+0**.

Measured on `flow/ariane133/`, which declares `set_keepout_loci outside`:
13,034 of 13,167 keepouts interior, grid **2,508,972 → 6,327 cells (397×)**,
and the flow now runs **with** its obstruction model in ~19 s where it
previously did not finish — `no_blockages` is gone from it, which was this
item's closing condition.

One consequence to expect rather than be surprised by: the vehicle is now
genuinely congested. `OBS` covers metal1–metal4 across all 133 macros, so
that interconnect has to live on metal5 and above — which is the truth
about the floorplan, and what `no_blockages` was hiding. It ends dirty;
healers take 125 overlaps to 61 and are still improving at the move budget.
Convergence there is that vehicle's QoR question, not an interchange one.

Pinned by `test_keepout_hanan_loci.py`.

### Residual, found 2026-08-17: `outside` governs one of the two grids

`outside` filters `Floorplan::get_hanan_grid` — the design-wide grid, which
is where the quadratic blowup lives and where the 397× above was measured.
The **n-pin trunk generator** builds a second, per-bundle Hanan grid
(`TopologyGenerator::generate_npin`) and that one does **not** consult
`inside_block`, so an interior keepout still contributes loci there. (It
does honour the later `stripes` modifier, which is how the asymmetry came to
light — a reviewer noticed the composition claim in the script reference was
true of only one grid.)

Live rather than theoretical: on `flow/ariane133`, **12,635 of 13,034**
keepouts are both interior and strap-shaped, so 97% of them add per-bundle
loci the design grid drops.

**Not repaired, because which behaviour is right is genuinely open.** The
per-bundle grid is bounded by the bundle's extent, so `outside`'s quadratic
justification does not transfer to it; and this item's own lesson is that an
interior locus *is* reachable — `flow/rv`'s bundle 42 proved it — so keeping
those positions exactly where the trunk is chosen may be part of why
`outside` is affordable at all. Filtering here would move trunk candidates
on every flow declaring `outside`, which wants a corpus measurement.

To settle it: apply the filter behind a temporary env knob, sweep
`tools/qor_corpus.py --vs main`, and read `flow/rv/soc_conv_div` and
`flow/ariane133` specifically — the first is where dropping interior loci
already cost a trunk, the second is where 97% of keepouts are affected.

---

## 13. ~~A component `HALO` was imported as a routing keepout~~ — RESOLVED 2026-08-14

Found by going after `flow/ariane133`'s congestion, and it turned out not to
be congestion at all.

`ariane.def` puts `+ HALO 10000` — 5 µm a side at 2000 DBU/µm — on every one
of its 133 macros. Each became a keepout with **no layer**, and the session
maps a layerless keepout onto *every* routing layer. So 133 zones forbade
routing across an area the DEF left completely routable.

DEF has two halos, and they are different constraints:

| | means | layers |
|---|---|---|
| `+ HALO [SOFT] l b r t` | keep other **cells** away — placement | none |
| `+ ROUTEHALO dist minLayer maxLayer` | keep **routing** away | named |

We honoured the placement construct as routing and **ignore** the routing
construct. Backwards — and it is the same mistake recorded a few lines below
it in the same function for `PLACEMENT` blockages, which are dropped because
*"importing them forbade routing the DEF left routable"*. That reasoning
was written, applied to the neighbouring construct, and not applied here.

Measured on `flow/ariane133`, with nothing else changed:

| | before | after |
|---|---|---|
| track overlaps | 195 | **0** |
| `check_design` | 178 violations / 47 bundles | **77 / 25** |
| supply-doomed seats | 83 (435 stranded bits) | 18 (104) |
| runtime | 18.9 s | **13.5 s** |

The `OBS` obstruction model is untouched and still fully enforced — 13,034
keepouts, the thing that actually describes where macro metal is. What went
away is 133 all-layer zones describing where *cells* may not go. A halo is
real placement information BUDA has no stage to apply, so it is now
**censused** (`COMPONENTS.HALO`) beside `BLOCKAGES.PLACEMENT`, not silently
dropped.

**What this cost before it was found.** The congestion was first attacked as
QoR: promoting metal5–metal10 to `TOP` and running `negotiate_congestion` +
`ripup_reroute` took 195 → 23 overlaps in 84 s, and looked like progress.
The root-cause fix reaches **0 in 13.5 s** with the original layer policy and
no healers, so all of that tuning was discarded. The tell was in the
diagnostics the whole time: 83 supply-doomed seats reporting **zero** signal
tracks in their placed windows, on layers carrying 4,848 tracks. Zero is not
a congestion number — it is a "something is blocking everything" number, and
it should have been read as one before any knob was turned.

**Still open, deliberately:** `ROUTEHALO` remains parsed-and-ignored. That is
not a regression — it was ignored before too, and the halo blocking was an
accident rather than an implementation of it — but it *is* the construct
that should produce a routing keepout, on its own declared layer range. No
vehicle here declares one, so it is left rather than built blind.

Pinned by `test_def_import.py`, which **reverses** an assertion this repo
used to make. Its old comment was explicit about the belief — *"the HALO
ring around i1, which the placer honoured and the router must too"* — and it
is quoted in the new test so the reversal reads as a decision rather than a
silent edit.
## 14. ~~An imported grid has track positions but no wire WIDTH~~ — RESOLVED 2026-08-14

A DEF `TRACKS` statement says where tracks are and nothing about how wide a
wire on one is. `import_def_lef` therefore modelled each layer as **one
full-pitch SIGNAL slot** — a wire occupying its entire track, with no space
beside it — unless a LEF had supplied the width. It had a hook for exactly
that (`_lef_track_width`), and on `flow/ariane133` the hook never fired.

**Why it never fired: layer precedence was per LAYER, not per FACT.**
`import_lef_tech` skipped any layer the script had already named
(`"already declared by the script"`), which discarded that layer's PITCH and
WIDTH along with it — the only facts in the file the script had no syntax to
state, since `def_layer` declares id, name, direction, TOP/LOW and overhead
and nothing about geometry. ariane133 declares its ten layers by hand, so it
got no widths at all.

Measured before: every one of the ten routing layers had `n_signal = 1`,
`min_wire == unit_pitch`, **rail fraction 0.000**, and a metal1 "minimum
wire" of 280 DBU where NanGate45's is 140.

**What landed.** The script keeps a declared layer's IDENTITY; its track
GEOMETRY still comes from the file, and yields only to a geometry
declaration (`def_track_pattern`), checked where the pattern is installed.
The two paths share one installer so they cannot learn different geometry
from the same file, and a direction disagreement between script and file is
reported rather than absorbed. `flow/ariane133` fetches the NanGate45 tech
LEF (proprietary header — fetched, never vendored) and composes it with the
DEF: **TRACKS supplies the positions, LEF supplies the width.**

*The units differ, and composing them raw is the trap.* `_lef_track_width`
is MICRONS (a LEF is written in them) and the DEF's step is in the design's
layout units — 0.07 against 280 here. The conversion belongs at the DEF
TRACKS path and not at the LEF read, because `set_import_scale dbu` resolves
from the DEF's own `UNITS` statement and a tech LEF read first cannot know
it. Uncomposed, a 0.07-wide wire on a 280 pitch is a signal density of
0.025% that would strand practically every bit while looking like a grid.

Measured after: metal1 280/140 (50% space), metal2 380/140, metal9
3200/1600 — each matching the LEF exactly, with metal10 taking the DEF's own
1.68 µm step and the LEF's 0.8 µm width. **The route is unchanged** (one
signal track per pitch either way, so capacity is identical: measured on current main: 121 segments, 0 overlaps, 77 violations in 25 bundles, identical either way), which is the point — this is
a correctness fix to the modelled wire, not a QoR change.

Blast radius: of the five flows using `import_lef_tech`, only ariane133 also
declares layers by hand, so it is the only one whose behaviour moves.

**It also exposed a defect in NDR's run model.** Rails are what bound a
contiguous signal run, so modelling one period plus a single splice across
the tiled boundary is exact while rails exist — and a LEF says nothing about
which tracks the power grid takes (that lives in the DEF's `SPECIALNETS`), so
every layer imported from one is **all-signal**, where the run does not end
at all. `ndr_geom` capped those at two periods' worth: a one-slot period
reported a longest run of 2, so a metal-shaped rule needing 3 slots was
refused as unrealizable on a layer that can host any width. `NdrLayerGeom`
now carries `unbounded` and consumers extend the repeating unit on demand.
Verified on the design: `width 0.35um metal` resolves to **3 slots/bit** on
metal1/metal2, which was an R3 hard error before.

Pinned by `test_lef_tech_import.py` (precedence per fact) and
`test_ndr_metal_quantization.py` (unbounded runs, with the railed and
single-splice cases as the twins that keep the rule honest).

---

## 15. The special-wire reader read one form of SPECIALNETS wire

**Reader RESOLVED 2026-08-16 for every form a generator emits; the keepout
IMPACT is still unmeasured (§"Still open" below).**

`read_specialnet` collected points only while the next token was `(`, so it
handled a contiguous width-plus-polyline and **nothing else**. Measured by
parsing one-net DEFs through `parse_def`:

| special-wire form | wires kept, before | now |
|---|---:|---:|
| `+ ROUTED M6 2000 ( x y ) ( * y )` | 1 | 1 |
| `+ ROUTED M6 2000 + SHAPE STRIPE ( x y ) ( * y )` | **0** | 1 |
| `NEW M6 0 + SHAPE STRIPE ( x y ) via6_7_…` | **0**, as unread wire | via placement |
| `… ( x y ) ( * y ) M6_M7 ( x y ) ( x2 * )` (via mid-path) | 1, **truncated** | 1, truncated |
| `+ ROUTED M6 2000 RECT ( … ) ( … )` | **0** | **0** |
| `+ ROUTED M6 2000 POLYGON ( … ) …` | **0** | **0** |

The clauses it recognises at all are `+ ROUTED`, `+ FIXED`, `+ COVER` and
their `NEW` continuations — matched by POSITION since the lexer strips
quotes, so a `+ PROPERTY mode "ROUTED"` value is not mistaken for wiring.
Any other geometry-bearing clause is outside what the reader looks for.

`+ SHAPE` is not an exotic form — DEF's grammar is
`ROUTED layer width [+ SHAPE type] [+ STYLE n] points`, so the `+` sits
between the width and the first `(` and the point walk never starts. That is
what a PDN generator emits for **every stripe it draws**, so a real power grid
could be imported almost entirely as nothing. A via truncates a path to its
first leg; `RECT` and `POLYGON` special wires are not represented at all.

**The census half was resolved 2026-08-15; the READER on 2026-08-16, when a
vehicle turned up that had been fetchable all along.** Both halves are below,
in the order they landed, because the second one is a lesson about the first.

### The census (2026-08-15)

A net whose paths were present but unread used to be recorded as
`SPECIALNETS.no_geometry` — not silence but a positive claim that the DEF drew
no metal there — and a truncated path was recorded as nothing at all, since a
kept wire looks like a complete read. Now each path is censused by what
defeated the reader (`SPECIALNETS.unread_wire`, `SPECIALNETS.partial_wire`),
and `no_geometry` is emitted only when the net has no `+ ROUTED` at all, which
is what `demo/ariane/ariane.def` genuinely carries.

That made the gap **loud instead of invisible** — and it is what made the next
step measurable, since the count below is the census reporting itself.

### The reader (2026-08-16)

This page said the fix "wants a placed and power-routed DEF" and that until
one existed it "cannot be measured on anything but a synthetic case". **That
was wrong, and wrong in the direction this document keeps warning about.**
OpenROAD's pdn regression goldens (`src/pdn/test/*.defok`) are what pdngen
writes and diffs against — real generator output, ~1 MB, fetchable through
the same `raw.githubusercontent.com` channel `flow/ariane133/fetch.py`
already uses. Measured across four of them:

| | paths | before | after |
|---|---:|---:|---:|
| metal polylines | 685 | **0 read** | 685 read |
| via placements | 6781 | counted as unread WIRE | censused `via_placement` |
| via mid-path / `RECT` / `POLYGON` | 0 | — | — |

Every one of the 685 was defeated by the same three tokens. What landed:

1. **`+ SHAPE` / `+ STYLE` are skipped** between the width and the first
   point, and the shape TYPE is kept (`DefSpecialWire::shape`) — a rail
   inside a standard-cell row and a stripe crossing the die are different
   obstacles, and this clause is the only thing that says which. It also
   distinguishes a reader that RECOGNISES the clause from one that merely
   tolerates it, which is what the first cut did.
2. **A one-point path ending in a via name is a via PLACEMENT, not a wire**
   — `NEW metal6 0 ( x y ) via6_7_…`, 6781 of the goldens' 7466 paths. It
   draws no run, so there is no polyline to lose; what goes unmodelled is the
   via's ENCLOSURE metal, whose extent lives in the DEF's `VIAS` section and
   not here. Censusing these as `unread_wire` would bury the wires that
   really were lost under a 10:1 majority of things that are not wires.

**Not represented, and NOT for that reason: a via placement's ENCLOSURE
metal.**  A via draws metal on both layers it joins, and its extent lives in
the DEF's `VIAS` section, which this reader does not resolve — so the 6781
via placements in the goldens, and the **113,969** in `flow/ariane133`'s PDN,
block nothing at all.  Filed apart from the three below because the reason is
different in the way that decides whether to build it: those have no
generator output to build against, while this one has 113,969 instances of
its input sitting in the tree.  It is unbuilt, not evidence-starved.

*What it costs today.* Every measurement of what a PDN blocks is a **lower
bound on the obstruction** — stated as such in
[specialnets_scope.md](specialnets_scope.md), where the caveat also records
that this does NOT make the five-unit WL delta a bound in either direction
(more obstruction can move the planner onto a different topology).  The grid
conclusion is unaffected: more keepouts can only add loci.

*Where to start:* parse `VIAS` into per-via-name rect sets in `def_io`, then
give `read_specialnet`'s via-placement branch (`DefSpecialWire` with one
point) those rects at its coordinate, translated per the via's own frame.
The census key `via_placement` already counts exactly the population that
would gain geometry, so the before/after is measurable on day one.

**Still not represented, deliberately: a via MID-path, `RECT` and `POLYGON`
special wires.** All three are legal DEF; none appears in any generator
output available here, so an implementation would be built against no
evidence at all — which is the mistake this item exists to record, not one to
make in a fresh direction. They stay censused, and
`test_the_goldens_carry_no_form_the_reader_still_cannot_read` fails the day a
golden carries one, so the boundary gets revisited with a vehicle in hand.

**What the keepouts DO to a route — MEASURED 2026-08-16, and it needed a fix.**
The goldens are 100–200 µm parser vehicles, so this needed a real PDN on
`flow/ariane133`: the pdngen run in
[openroad_pdn_recipe.md](openroad_pdn_recipe.md) produced one, spliced into
the DEF as `SPECIALNET:6673` keepouts (controlled — same netlist as baseline).
The finding was not a QoR delta but a **grid explosion**: `set_keepout_loci
outside` (item 12, a block-*interior* rule) suppressed the loci of none of the
6673, and their thin-axis edges took the Hanan grid ~6,327 → ~3.2M cells and
stalled the planner (killed at 16 min) — item 12 re-opened in a new direction.
**Fixed by `set_keepout_loci stripes`** (§8.1): a thin+long strap (aspect ≥ 8)
keeps only its long-axis loci.

*Why `outside` reached none of them was two reasons, not one* — corrected
2026-08-19/20, after this paragraph first said simply "they are die-crossing
and lie outside every block".  Only ONE of `pdn.tcl`'s three grids is
die-spanning (`grid`: M1 followpins + M4/M7); `macro_r0` (M5+M6) and
`macro_r90` (M6) are `-macro` grids drawn OVER the 133 SRAMs, which ARE
blocks.  `outside` missed those for a different reason than geometry — the
importer stamped every strap keepout `inside_block=false` unconditionally,
where a macro `OBS` had its containment measured — and that half is now
**fixed**: straps and `LAYER` blockages measure containment like `OBS`
(#800).  `stripes` remains the remedy for the core grid, which no containment
test can help with because those straps genuinely are outside everything. With `outside stripes` the design routes in ~30 s at 0
overlaps and abstract WL within 5 units of the no-PDN baseline — so the
enforced PDN keepouts, once they stop exploding the grid, barely perturb this
design's route (its signal metal lives on M8–M10). Pinned by
`test_stripe_keepout_loci.py`; the reader fix stays byte-identical on every
tree flow (none carries `+ SHAPE`), which is why it landed alone.

### Why it survived this long

Item 12's lesson, twice over. First: the only two DEFs here with any PDN are
**ours** (`flow/def/chip.def`, `flow/rv/soc.def`), both written in the one
form the reader handled, so "the reader is complete" had been validated
against the assumption it was built on. Second, and newer: once that was
understood, the fix was parked behind "we need a power-routed DEF" for a day
— when the thing actually needed was not a *design* but a *sample of the
output grammar*, and upstream publishes 1 MB of it as test data. The
prerequisite was real for the QoR question and imaginary for the parsing one,
and nobody had separated the two.

Pinned by `test_def_reader.py` (the forms, one clause at a time) and
`test_def_specialnets.py` (the same reader against upstream's bytes: a
checked-in four-clause excerpt that always runs, plus the fetched goldens for
the counts), with the twins that keep the census honest — a net that really
has no wires must still say `no_geometry`, a wire read in full must census
nothing, and a stripe must reach the keepout consumer, which nothing pinned
before.

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
