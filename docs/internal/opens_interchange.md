# Open items — file I/O and interfaces

What the LEF / DEF / Verilog / GDS readers and writers, and the front ends
that drive them, deliberately do **not** do yet. The built interface is
described in [`lefdef_interface_plan.md`](lefdef_interface_plan.md) (phases
0–5, all landed), [`engine_units.md`](engine_units.md),
[`gds_oa_interchange.md`](gds_oa_interchange.md),
[`message_ids.md`](message_ids.md) and [`../TCL_FRONT_END.md`](../TCL_FRONT_END.md);
this page is the backlog behind them.

Snapshot index — last verified against `main`: **2026-08-09**, at the commit
that merged `flow/def` (PR #650). Each item states what is missing, why it
was left rather than forgotten, and where to start. Every claim below was
reproduced on `main` before being written down; the reproduction is given so
a reader can re-derive it rather than trust it.

The working vehicle for all of this is **[`flow/def/`](../../flow/def/)** —
a LEF + DEF + Verilog design read off disk and routed end to end. Most of
these items were found by building it, which is the argument for having it.

---

## 1. The netlist reader drops blackbox instances with ordinary names

**The one to fix first.** An instance of a module the netlist does not define
is skipped unless its instance name is **backslash-escaped**:

```verilog
module top ();
  fakeram45_256x16 u_mem  (.A(x), .Z(y));    // dropped
  fakeram45_256x16 \u_mem (.A(x), .Z(y));    // kept
endmodule
```

Reproduced on `main`: the first form yields `components: []`.

The intent is sound and worth keeping — it is what stops a million standard
cells becoming a million component rows — and there is a second filter
behind it that admits a *macro* (a cell name containing a lowercase letter)
while rejecting a std cell (`DFFR_X1`). But that macro exception sits inside
the `esc_inst` branch, so it is **unreachable for a netlist that does not
escape its instance names**, and most do not. A hard macro therefore
vanishes from the hierarchy with no diagnostic — which for a routing tool is
the whole design.

*Where to start:* `finish_inst` in `bdb.cpp`. The escape is being used as a
proxy for "this is worth keeping"; the cell-name test already there is the
real signal. Reversing the nesting (apply the macro test regardless of
escaping) is a small change, but it needs a census-style diagnostic — "N
instances skipped as standard cells, M kept as macros" — because silently
changing which instances exist is exactly the failure this item is about.

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
