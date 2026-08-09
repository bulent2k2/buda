# Open items — file I/O and interfaces

What the LEF / DEF / Verilog / GDS readers and writers, and the front ends
that drive them, deliberately do **not** do yet. The built interface is
described in [`lefdef_interface_plan.md`](lefdef_interface_plan.md) (phases
0–5, all landed), [`engine_units.md`](engine_units.md),
[`gds_oa_interchange.md`](gds_oa_interchange.md),
[`message_ids.md`](message_ids.md) and [`../TCL_FRONT_END.md`](../TCL_FRONT_END.md);
this page is the backlog behind them.

Snapshot index — last verified against `main`: **2026-08-09**, after item 1
landed. Each item states what is missing, why it was left rather than
forgotten, and where to start. Every claim below was reproduced on `main`
before being written down; the reproduction is given so a reader can
re-derive it rather than trust it.

Item 1 is kept in place, struck through, rather than moved to the resolved
table at the bottom — its entry now records that this page's **first
description of it was wrong about the merge case**, and that the fix it
originally proposed would have been the wrong fix. Both are worth more than
a tidy list.

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

**The fix asks the placement instead of guessing.** The DEF is the authority
on what exists physically, so an instance whose name the placement already
contains is kept, however it is spelled — no cell-class heuristic involved.
The legacy escaped-name rule stands unchanged behind it for Verilog-only
imports, where there is no placement to ask. Basenames are matched, which is
a deliberate superset: the netlist knows an instance by its local name and
only elaboration knows the path, and the cost of a false keep is one real
netlist instance kept while the cost of a false drop is a hole.

Deliberately **not** done: lifting the lowercase-letter test out of the
escape branch, which is what this page originally suggested. It would have
been wrong — std cells are all-lowercase in some libraries (`sky130_fd_sc_hd__inv_1`),
so that rule keeps every gate in exactly the netlists the filter exists to
protect against.

**And the count is now always stated** (`BUDA-1608`), with the cell *kinds*,
because the filter remains a heuristic on the Verilog-only path and an
instance that silently never existed is indistinguishable from a design that
never had one. `import_verilog` returns a `VerilogImportStats`.

Pinned by `test_bdb_import_edges.py`: the macro joins the hierarchy, the
container it would have orphaned is sized, Verilog-only filtering is
unchanged, and the census names distinct kinds.

## 2. A vector port map collapses to one net

```verilog
wire [1:0] w;
sub u0 (.a0(w[0]), .a1(w[1]));
```

Reproduced on `main`: `nets: ['w']`. `parse_portmap` resolves a bit-select to
its **base name**, so a 4-bit bus arrives as a single net and the bundler
sees a 1-bit bus — a silent width collapse, not an error.

`flow/def/chip.v` works around it with four scalar wires, and says so in a
comment. That is fine for a fixture and wrong for a real netlist, where
vectors are how buses are written.

*Where to start:* keep the base name as the **bus** name (`net_props.bus_name`
already exists for this) and make the net per-bit. The DEF side already
names bits individually, so the merge would then line up instead of one side
having four nets and the other one.

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

## 4. Relative paths resolve against two different roots

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

*Where to start:* not a one-line change — picking either root breaks
existing scripts in the other direction, and `source ../tracks/tracks.buda`
(script-relative) is used throughout `flow/hbundles/`. The likely shape is
script-relative everywhere with a deprecation pass, or an explicit
`set_path_base cwd|script`. Worth a decision before a patch.

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
