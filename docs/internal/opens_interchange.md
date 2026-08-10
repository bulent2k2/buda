# Open items — file I/O and interfaces

What the LEF / DEF / Verilog / GDS readers and writers, and the front ends
that drive them, deliberately do **not** do yet. The built interface is
described in [`lefdef_interface_plan.md`](lefdef_interface_plan.md) (phases
0–5, all landed), [`engine_units.md`](engine_units.md),
[`gds_oa_interchange.md`](gds_oa_interchange.md),
[`message_ids.md`](message_ids.md) and [`../TCL_FRONT_END.md`](../TCL_FRONT_END.md);
this page is the backlog behind them.

Snapshot index — last verified against `main`: **2026-08-10**, after items 1,
2 and 4 landed. Each item states what is missing, why it was left rather than
forgotten, and where to start. Every claim below was reproduced on `main`
before being written down; the reproduction is given so a reader can
re-derive it rather than trust it.

Items 1, 2 and 4 are kept in place, struck through, rather than moved to the
resolved table at the bottom. Each entry records where this page's **own
first description was wrong** — item 1 about the merge case, and about the
fix it originally proposed, which would have been the wrong fix; item 2
about the severity, having called a silent SHORT a width collapse. That is
worth more than a tidy list, and the pattern is worth naming: the first
description of a fault is written from the symptom you noticed, and the
symptom is rarely the whole fault.

The working vehicle for all of this is **[`flow/def/`](../../flow/def/)** —
a LEF + DEF + Verilog design read off disk and routed end to end. Most of
these items were found by building it, which is the argument for having it.

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

## 3. The GDS round trip loses what the merge invented

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

## 5. The DEF reader buffers the whole design

`read_def` now holds the file **once** (it held it three times before PR
#650), but the reader is still not streaming, and the text is not the
dominant term. Measured on `main`, 2026-08-09 — a 19.7 MB / 1.02 M-line DEF
with 340 k components costs **96 MB peak, 4.9× the file size**, most of it
the parsed model at ~220 B per component. Budget **~5×**.

True streaming means inserting DB rows as entries parse, which
`import_def_lef` cannot do today for two concrete reasons, both fixable:

1. it walks `def.components` a **second** time to place macro `OBS` keepouts;
2. net resolution needs the name index built from the full component list.

*Where to start:* item 1 is the easier half — collect OBS keepouts during the
first pass. Note the reader this replaced streamed cheaply only because it
*kept almost nothing* (it discarded TRACKS, PINS, BLOCKAGES and GCELLGRID,
and could not read a wrapped entry at all), so the memory is being spent on
data that is now actually retained.

## 6. Script-declared distances are not scaled by the import scale

`set_import_scale dbu` changes what a layout unit means for **imported**
geometry only. Everything a script states itself — `corner_margin`,
`set_min_stub_length*`, `def_track_pattern` widths, `add_keepout` rects — is
already in layout units and is left alone.

This is deliberate (applying the factor at import is what keeps the ~59
downstream `int(round())` sites correct by construction — see
[`engine_units.md`](engine_units.md)) and it is a real trap: change the
import scale and every hand-typed distance in the script silently means
something else. `set_unit_check` exists to turn that into a stop rather than
an optimistic plan, and fires on 0 of 41 corpus flows.

*Where to start:* probably nothing in the engine. The candidate is
ergonomic — a `um(...)`/`dbu(...)` spelling for script distances, so the
intent is written down rather than implied by the current scale.

## 7. DEF `NONDEFAULTRULES` are read but not attached

Phase 3e reads them and records them in the unmodelled census; nothing wires
them to the landed `def_ndr` / `set_ndr` feature. Left because the mapping
is per-rule **content** (widths, spacings, layer sets) rather than a name, so
it is a translation job and not a lookup.

*Where to start:* [`opens_ndr.md`](opens_ndr.md) owns the rule model; this
item is the import side of it.

## 8. Packaged wheel

Phase 5 shipped the Tcl front end and the logging conventions; the wheel was
not built. It needs a build of the C++ extension per platform and per Python
(`cibuildwheel`, plus `auditwheel`/`delocate`), which is its own CI problem
with its own gates — and the repo's `-march=native` default has to be pinned
per wheel or the artifact crashes with an illegal instruction on an older
CPU.

The other half of that plan bullet — `BUDA_NO_APP=1` as the batch default on
macOS — is **already satisfied**: `bin/buda`'s Darwin relaunch requires all
three of stdin/stdout/stderr to be TTYs and skips `--no-viz`, so a redirected
or batch run already falls through to the direct launch.

## 9. Smaller residuals

* **`demo/ariane` is a mismatched pair** (data, not code): its DEF
  instantiates 133 × `fakeram45_256x16` while its LEF defines only
  `sram_asap7_16x256_1rw`. Under the old importer every macro on that 2.7 mm
  die was a 0.5 µm speck and nothing said so; the reader now refuses it
  unless told `allow_missing_footprints`. Which of the two files is
  authoritative is the owner's call.
* **The Tcl front end is synchronous.** One request, one reply, no
  cancellation and no progress events — so a long `ripup_reroute` is a
  blocking call. Fine for a flow script; a GUI driving it would want more.
  Protocol in `tools/buda_server.py`.
* **The Tcl protocol assumes nothing writes to fd 1 directly.** Python's
  `sys.stdout` and C++'s `std::cout` are both captured; a library writing
  straight to the descriptor would land inside a frame and desynchronise the
  channel. Nothing in BUDA does this today.
* **`import_gds` label recovery needs components to land on.** Labels
  outside every component are skipped with a warning — correct, and a
  consequence of item 3 rather than a separate defect.

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
