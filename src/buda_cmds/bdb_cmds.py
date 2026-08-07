# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""BDB — physical-design-database commands (ingest, hierarchy, mutate).

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import buda
import os
import sys

from ._options import reject_unknown_options


def cmd_bdb_net_mode(session, cmd, args, cmd_line):
    # bdb_net_mode on|off
    if len(args) < 1 or args[0].lower() not in ('on', 'off'):
        print("Error: bdb_net_mode requires on|off"); return
    session.bdb_net_mode = (args[0].lower() == 'on')
    print(f"[BDB] net mode {'enabled' if session.bdb_net_mode else 'disabled'}")


def cmd_add_cell_pin(session, cmd, args, cmd_line):
    # add_cell_pin <cell> <pin_name> [INPUT|OUTPUT|INOUT] [<px> <py>]
    if session.bdb is None:
        print("Error: add_cell_pin requires an open BDB (use open_bdb first)"); return
    if len(args) < 2:
        print("Error: add_cell_pin requires <cell> <pin_name> [dir] [px py]"); return
    cell_name = args[0]; pin_name = args[1]
    direction = args[2].upper() if len(args) > 2 else "INOUT"
    px = float(args[3]) if len(args) > 3 else -1.0
    py = float(args[4]) if len(args) > 4 else -1.0
    session.bdb.add_cell_pin(cell_name, pin_name, direction, px, py)


def cmd_def_gds_layer(session, cmd, args, cmd_line):
    # def_gds_layer <buda_layer_id> <gds_layer> [<gds_datatype>]
    # def_gds_layer file <path>      (lines: <id> <gds_layer> [<dt>], # comments)
    # def_gds_layer labels <csv>     (default TEXT label layers for import_gds)
    # GDSII layer mapping (Phase G3): binds a def_layer metal layer to a
    # GDS (layer, datatype) pair. import_gds excludes shapes on mapped
    # pairs from cell footprints (they are wires, not macro outlines);
    # export (Phase G4) writes each metal layer to its mapped pair.
    if len(args) < 2:
        print("Error: def_gds_layer requires <buda_layer_id> <gds_layer> "
              "[<gds_datatype>], 'file <path>', or 'labels <csv>'"); return
    if args[0] == "labels":
        session._gds_label_layers = [int(x) for x in args[1].split(",") if x]
        return
    entries = []
    if args[0] == "file":
        try:
            with open(args[1]) as f:
                for ln, line in enumerate(f, 1):
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) not in (2, 3):
                        print(f"Error: {args[1]}:{ln}: expected "
                              f"<buda_layer_id> <gds_layer> [<gds_datatype>]")
                        return
                    entries.append(parts)
        except OSError as e:
            print(f"Error: def_gds_layer file: {e}"); return
    else:
        entries.append(args[:3])
    # Validate the whole map before installing any entry: sourced
    # scripts continue past a returned error, and a partially
    # installed map would let a following import_gds exclude only
    # SOME of the mapped wires from footprints.
    parsed = []
    for parts in entries:
        lid, gl = int(parts[0]), int(parts[1])
        dt = int(parts[2]) if len(parts) > 2 else 0
        if not session.layers.has_layer(lid):
            print(f"Error: unknown layer id {lid} — define it with "
                  f"def_layer first (no mappings installed)"); return
        parsed.append((lid, gl, dt))
    for lid, gl, dt in parsed:
        other = session.layers.layer_for_gds(gl, dt)
        if other >= 0 and other != lid:
            print(f"Warning: GDS ({gl},{dt}) already mapped to layer "
                  f"{other}; layer {lid} also maps it")
        session.layers.set_gds_mapping(lid, gl, dt)


def cmd_open_bdb(session, cmd, args, cmd_line):
    # open_bdb <path> [writeback]
    if not args:
        print("Error: open_bdb requires a file path"); return
    # `writeback` is the only option after the path — a typo must not silently
    # leave write-back disarmed (and drop changes at exit).
    reject_unknown_options("open_bdb", args[1:], ("writeback",))
    # Persist any fixture armed by a previous open_bdb before switching.
    session._flush_bdb_writeback()
    # `writeback` must be the explicit optional argument immediately after
    # the path. A membership test (`in args[1:]`) would also match the word
    # inside a trailing comment — do_command only strips full-line comments,
    # so `open_bdb foo.sql # no writeback` keeps 'writeback' as a token —
    # silently arming write-back on a fixture from a read-looking line.
    writeback = len(args) >= 2 and args[1] == "writeback"
    bdb_path = args[0]
    if (session._script_stack
            and not os.path.isabs(bdb_path)
            and bdb_path != ':memory:'):
        parent_dir = os.path.dirname(session._script_stack[-1])
        bdb_path = os.path.normpath(os.path.join(parent_dir, bdb_path))
    # A serialized text BDB (e.g. mix.bdb.sql) is materialized into a
    # throwaway temp binary so the pipeline never dirties the checked-in
    # text fixture. With `writeback`, changes are dumped back to the .sql on
    # save_bdb/exit. See docs/internal/bdb_test_data.md.
    if bdb_path != ':memory:' and bdb_path.endswith('.sql'):
        bdb_path = session._materialize_bdb_sql(bdb_path, writeback=writeback)
    elif writeback:
        print("open_bdb: 'writeback' applies only to a serialized *.sql "
              "fixture; a binary BDB is opened read-write and persists "
              "directly — ignoring.")
    # The file the live connection holds open (temp binary for a .sql
    # fixture) — save_bdb's save-as guard refuses to back up onto it.
    session._bdb_open_path = bdb_path
    session.bdb = buda.BDB(bdb_path)
    # A different design's rotation-class clone registry is meaningless
    # here (and stale id-keyed provenance would corrupt the next persist,
    # Codex #253); load_pipeline rebuilds both from this BDB's rows.
    session._bu_clone_cells = {}
    session._bu_clone_from = {}
    # The selective-persist fingerprint memo describes rows in the PREVIOUS
    # BDB — against a fresh file it would skip writes for rows that do not
    # exist there.  A full persist rebuilds it.
    session._persisted_plan_fp = None
    # Per-cell layer policies persisted in this BDB (v20) come back so the
    # resumed flow plans under the same bands (session-typed entries win).
    session._restore_layer_policies()


def cmd_import_def_lef(session, cmd, args, cmd_line):
    # import_def_lef <def_path> <lef_path>
    if len(args) < 2:
        print("Error: import_def_lef requires <def_path> <lef_path>"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    session.bdb.import_def_lef(args[0], args[1])


def cmd_import_verilog(session, cmd, args, cmd_line):
    # import_verilog <v_path>
    if not args:
        print("Error: import_verilog requires a file path"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    session.bdb.import_verilog(args[0])


def cmd_import_gds(session, cmd, args, cmd_line):
    # Usage: import_gds <file.gds> [labels <layer_csv>]
    # GDSII stream -> BDB placement/hierarchy + TEXT-label net recovery
    # (fresh load; see docs/internal/gds_oa_interchange.md, Phases G1-G3).
    # `labels 63,64` restricts which GDS layers carry net labels;
    # default = layers from `def_gds_layer labels`, else every TEXT
    # record is a label. Shapes on def_gds_layer-mapped routing pairs
    # are excluded from cell footprints (wires, not macro outlines).
    if not args:
        print("Error: import_gds requires a file path"); return
    # `labels` is the only keyword after the file — a typo must not silently
    # fall back to the default label layers.  (The layer CSV after it is a
    # free-form value, not validated here.)
    if len(args) >= 2:
        reject_unknown_options("import_gds", [args[1]], ("labels",))
        if len(args) < 3:
            print("Error: import_gds labels requires a layer CSV"); return
        if len(args) > 3:
            # The layer list is ONE comma-separated token — `labels 63 64`
            # (space-separated) used to import only layer 63, dropping 64.
            print(f"Error: import_gds: unexpected trailing argument(s) "
                  f"{', '.join(repr(a) for a in args[3:])} — the layer list is "
                  f"one comma-separated token (e.g. `labels 63,64`)")
            sys.exit(1)
    if session.bdb is None:
        print("Error: open_bdb first"); return
    label_layers = list(session._gds_label_layers)
    if len(args) >= 3 and args[1] == "labels":
        label_layers = [int(x) for x in args[2].split(",") if x]
    st = session.bdb.import_gds(args[0], label_layers,
                             session.layers.gds_mapped_pairs())
    for wmsg in st.warnings:
        print(f"[import_gds] Warning: {wmsg}")
    tops = ", ".join(st.tops) if st.tops else "(none)"
    lbl = (f"{st.n_nets} net(s) / {st.n_pins} pin(s) recovered from "
           f"{st.n_texts} TEXT label(s)"
           + (f" ({st.n_labels_skipped} skipped)"
              if st.n_labels_skipped else "")
           if st.n_texts else "no TEXT labels (pair with "
           "import_verilog for connectivity)")
    rt = (f" {st.n_routing_shapes} routing shape(s) excluded from "
          f"footprints." if st.n_routing_shapes else "")
    print(f"[import_gds] {st.n_structures} structure(s) -> "
          f"{st.n_cells} cell(s), {st.n_components} component(s); "
          f"top(s): {tops}; {lbl}.{rt}")


def cmd_export_gds(session, cmd, args, cmd_line):
    # export_gds <file.gds> [outline <gds_layer>] [labels <gds_layer>|off]
    #            [via_size <um>]
    # GDSII export (Phase G4): stream the BDB — cells as structures
    # with outline rects, components as SREFs, persisted net_segment
    # wires (bus_segment fallback) on their def_gds_layer-mapped pairs,
    # vias as squares, one TEXT label per pin. Re-importable with the
    # same map (see docs/internal/gds_oa_interchange.md).
    if not args:
        print("Error: export_gds requires a file path"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    outline = 10
    label_layer = (session._gds_label_layers[0]
                   if session._gds_label_layers else 63)
    write_labels = True
    via_size = 1.0
    i = 1
    while i < len(args):
        kw = args[i]
        if kw == "outline" and i + 1 < len(args):
            outline = int(args[i+1]); i += 2
        elif kw == "labels" and i + 1 < len(args):
            if args[i+1] == "off":
                write_labels = False
            else:
                label_layer = int(args[i+1])
            i += 2
        elif kw == "via_size" and i + 1 < len(args):
            via_size = float(args[i+1]); i += 2
        else:
            print(f"Error: export_gds: unknown option {kw!r}"); return
    layer_map = [(lid, session.layers.get_gds_layer(lid),
                  session.layers.get_gds_datatype(lid))
                 for lid in sorted(set(session._layer_name_map.values()))
                 if session.layers.get_gds_layer(lid) >= 0]
    st = session.bdb.export_gds(args[0], layer_map, outline, label_layer,
                             write_labels, via_size)
    for wmsg in st.warnings:
        print(f"[export_gds] Warning: {wmsg}")
    print(f"[export_gds] wrote {args[0]}: {st.n_structures} "
          f"structure(s), {st.n_placements} placement(s), "
          f"{st.n_wire_shapes} wire shape(s) "
          f"({st.stage or 'no routing'}), {st.n_via_shapes} via(s), "
          f"{st.n_labels} label(s).")


def cmd_add_blocks_from_bdb(session, cmd, args, cmd_line):
    # add_blocks_from_bdb <depth> [deepest|skip|error]
    if not args:
        print("Error: add_blocks_from_bdb requires <depth>"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    depth    = int(args[0])
    mode     = args[1].lower() if len(args) > 1 else "deepest"
    if mode not in ("deepest", "skip", "error"):
        print(f"Error: unknown mode {mode!r}; use deepest|skip|error"); return
    session._add_blocks_from_bdb(depth, mode)


def cmd_set_die(session, cmd, args, cmd_line):
    # set_die <w> <h>
    if len(args) < 2:
        print("Error: set_die requires <w> <h>"); return
    w, h = float(args[0]), float(args[1])
    if session.bdb is not None:
        session.bdb.set_die(w, h)
    else:
        session._die_w, session._die_h = w, h


def cmd_move_comp(session, cmd, args, cmd_line):
    # move_comp <name> <x> <y>
    if len(args) < 3:
        print("Error: move_comp requires <name> <x> <y>"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    session.bdb.move_comp(args[0], float(args[1]), float(args[2]))


def cmd_resize_cell(session, cmd, args, cmd_line):
    # resize_cell <cell> <w> <h>
    if len(args) < 3:
        print("Error: resize_cell requires <cell> <w> <h>"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    session.bdb.resize_cell(args[0], float(args[1]), float(args[2]))


def cmd_add_cell(session, cmd, args, cmd_line):
    # add_cell <name> <width> <height>
    if len(args) < 3:
        print("Error: add_cell requires <name> <width> <height>"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    session.bdb.add_cell(args[0], float(args[1]), float(args[2]))


def cmd_add_inst(session, cmd, args, cmd_line):
    # add_inst <inst_name> <cell_name> <parent|-> <x> <y>
    # x,y are relative to parent's origin; absolute when parent is "-"
    if len(args) < 5:
        print("Error: add_inst requires <inst_name> <cell_name> "
              "<parent|-> <x> <y>"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    parent = "" if args[2] == "-" else args[2]
    session.bdb.add_inst(args[0], args[1], parent,
                      float(args[3]), float(args[4]))


def cmd_add_inst_to_cell(session, cmd, args, cmd_line):
    # add_inst_to_cell <parent_cell> <inst_name> <child_cell> <x> <y>
    # Defines the structural contents of parent_cell; no component rows
    # are created until add_inst places an occurrence of parent_cell.
    if len(args) < 5:
        print("Error: add_inst_to_cell requires <parent_cell> <inst_name> "
              "<child_cell> <x> <y>"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    session.bdb.add_inst_to_cell(args[0], args[1], args[2],
                              float(args[3]), float(args[4]))


def cmd_flip_comp(session, cmd, args, cmd_line):
    # flip_comp <name> x|y
    if len(args) < 2:
        print("Error: flip_comp requires <name> x|y"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    axis = args[1].lower()
    if axis not in ('x', 'y'):
        print(f"Error: flip_comp axis must be 'x' or 'y', got {args[1]!r}"); return
    session.bdb.flip_comp(args[0], axis == 'x')


def cmd_rotate_comp(session, cmd, args, cmd_line):
    # rotate_comp <name> 90|180|270
    if len(args) < 2:
        print("Error: rotate_comp requires <name> 90|180|270"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    try:
        degrees = int(args[1])
    except ValueError:
        print("Error: rotate_comp degrees must be 90, 180, or 270"); return
    session.bdb.rotate_comp(args[0], degrees)


def cmd_add_comp(session, cmd, args, cmd_line):
    # add_comp <name> <cell> <parent|-> <x1> <y1> <x2> <y2> [leaf]
    # Use "-" for parent to create a root instance.
    if len(args) < 7:
        print("Error: add_comp requires <name> <cell> <parent|-> "
              "<x1> <y1> <x2> <y2> [leaf]"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    parent = "" if args[2] == "-" else args[2]
    is_leaf = True
    if len(args) >= 8:
        is_leaf = args[7].lower() not in ("0", "false", "no", "nonleaf")
    session.bdb.add_comp(args[0], args[1], parent,
                      float(args[3]), float(args[4]),
                      float(args[5]), float(args[6]), is_leaf)


def cmd_derive_busterms(session, cmd, args, cmd_line):
    # derive_busterms [max_depth]
    # Populate BDB busterm table from the component hierarchy.
    if session.bdb is None:
        print("Error: open_bdb first"); return
    max_depth = int(args[0]) if args else 1
    session._busterm_gen = buda.BustermGen(session.bdb)
    session._busterm_gen.derive(max_depth)
    bts = session.bdb.all_busterms()
    print(f"derive_busterms: {len(bts)} busterms written (depth 0..{max_depth}).")


def cmd_refine_busterms(session, cmd, args, cmd_line):
    # refine_busterms — re-derive busterms using the same max_depth as
    # the last derive_busterms call (clears and rewrites the busterm table).
    if session._busterm_gen is None:
        print("Error: run derive_busterms first"); return
    session._busterm_gen.refine()
    bts = session.bdb.all_busterms()
    print(f"refine_busterms: {len(bts)} busterms written.")


def cmd_load_pipeline(session, cmd, args, cmd_line):
    # Usage: load_pipeline [expanded]
    # Rehydrate bundles + candidate topologies (+ plan + NUTS result, as
    # deep as was persisted) from the open BDB, so the pipeline resumes
    # where a previous session stopped. Requires the Floorplan/LayerStack
    # setup to be re-declared first (see docs/BDB_REFERENCE.md).
    # `expanded` is the only option — a typo must not silently load the
    # non-expanded view.
    reject_unknown_options("load_pipeline", args, ("expanded",))
    # The rehydrated wrappers replace self.bundles — the selective-persist
    # fingerprint memo describes the PREVIOUS wrappers' persisted rows, so
    # the next persist must rebuild it from a full pass.
    session._persisted_plan_fp = None
    session._load_pipeline_from_bdb(
        expanded=bool(args) and args[0] == "expanded")


def _resolve_layer_id(session, tok):
    # Accept a numeric layer id or a declared layer name (the session's
    # def_layer name map, same source _make_layer_names reads).
    if tok.isdigit():
        lid = int(tok)
        return lid if session.layers.has_layer(lid) else None
    lid = session._layer_name_map.get(tok)
    return lid if (lid is not None and session.layers.has_layer(lid)) \
        else None


def _band_missing_dir(session, floor, cap):
    """"H"/"V" if the band [floor..cap] grants no routing layer of that
    direction (an unroutable policy — a corner no ladder can escape), else
    None.  floor < 0 = no lower bound."""
    lo = floor if floor >= 0 else -(10 ** 9)
    have = {buda.LayerDir.HORIZONTAL: False, buda.LayerDir.VERTICAL: False}
    for d in have:
        for lid in session.layers.get_layer_ids_by_dir(d):
            if lo <= lid <= cap:
                have[d] = True
    if not have[buda.LayerDir.HORIZONTAL]:
        return "H"
    if not have[buda.LayerDir.VERTICAL]:
        return "V"
    return None


def cmd_set_cell_layer_cap(session, cmd, args, cmd_line):
    # set_cell_layer_cap <cell>|* <cap_layer> [-min <floor_layer>]  |  * off
    # Per-cell layer policy, binary band form (docs/internal/hier_layer_caps.md,
    # Q1 resolved): the cell's OWN interconnect defaults to FULL use of layers
    # in [floor..cap] and NO use outside it.  '*' sets the default policy for
    # cells without an explicit one; '* off' clears everything — bands AND
    # fractional shares (byte-identical to never declaring a policy).
    # Validation is LOUD at declaration time: unknown layer, floor above cap,
    # or a band granting no H or no V routing layer are all hard errors here,
    # never BEST_EFFORT surprises later.
    if not args:
        print("Error: set_cell_layer_cap requires <cell>|* <cap_layer> "
              "[-min <floor_layer>] (or '* off')"); return
    cell = args[0]
    if cell == "*" and len(args) > 1 and args[1].lower() == "off":
        n = len(getattr(session, "_cell_layer_policy", {}) or {})
        ns = len(getattr(session, "_cell_layer_shares", {}) or {})
        session._cell_layer_policy = {}
        session._cell_layer_policy_restored = set()
        session._cell_layer_policy_by_depth = set()
        session._cell_layer_shares = {}
        session._cell_layer_shares_restored = set()
        if session.bdb is not None:
            # Write-through (v20): clear every persisted band + share + the
            # '*' default so a later open/resume does not resurrect them.
            for c in session.bdb.layer_capped_cells():
                session.bdb.set_cell_layer_band(c, -1, -1)
            for c in session.bdb.layer_share_cells():
                for lid, _s in session.bdb.cell_layer_shares(c):
                    session.bdb.set_cell_layer_share(c, lid, 0.0)
            session.bdb.meta_set("layer_cap_default", "")
            session.bdb.meta_set("layer_caps_by_depth", "")
        print(f"[LayerCap] cleared {n} polic{'y' if n == 1 else 'ies'}"
              + (f" + {ns} share(s)" if ns else "")
              + " (byte-identical to no caps; re-run the planner to lift "
                "masks already applied)")
        return
    if len(args) < 2:
        print("Error: set_cell_layer_cap requires a cap layer"); return

    def _lid(tok):
        return _resolve_layer_id(session, tok)

    cap = _lid(args[1])
    if cap is None:
        print(f"Error: set_cell_layer_cap: unknown layer '{args[1]}' "
              f"(declare it with def_layer first)"); return
    floor = -1
    rest = list(args[2:])
    if rest:
        if rest[0] != "-min" or len(rest) < 2:
            print(f"Error: set_cell_layer_cap: unexpected '{' '.join(rest)}' "
                  f"(only '-min <floor_layer>' is accepted)"); return
        floor = _lid(rest[1])
        if floor is None:
            print(f"Error: set_cell_layer_cap: unknown floor layer "
                  f"'{rest[1]}'"); return
        if floor > cap:
            print(f"Error: set_cell_layer_cap: floor {rest[1]} is above the "
                  f"cap {args[1]} — the band [floor..cap] is empty"); return
    # The band must grant at least one H and one V routing layer, or nothing
    # in it is routable (a corner no ladder can escape).
    missing = _band_missing_dir(session, floor, cap)
    if missing:
        print(f"Error: set_cell_layer_cap: band grants no {missing} routing "
              f"layer — an unroutable policy"); return
    # Cell must exist when a BDB is open (mirrors set_bottom_up); '*' always ok.
    if cell != "*" and session.bdb is not None:
        if not any(c.cell == cell for c in session.bdb.all_components()):
            print(f"Error: set_cell_layer_cap: unknown cell '{cell}'"); return
    if not hasattr(session, "_cell_layer_policy") or \
            session._cell_layer_policy is None:
        session._cell_layer_policy = {}
    session._cell_layer_policy[cell] = (floor, cap)
    # An explicit declaration is session-typed from here on — it survives a
    # BDB switch, unlike an entry a restore added (see
    # _restore_layer_policies).
    getattr(session, "_cell_layer_policy_restored", set()).discard(cell)
    # ... and it outranks a by-depth default, now and on a later re-run of
    # set_layer_caps_by_depth (Phase 5 Q1: explicit always wins) — including
    # after a reload, hence the persisted provenance memo.
    getattr(session, "_cell_layer_policy_by_depth", set()).discard(cell)
    if session.bdb is not None:
        _persist_by_depth(session)
        # Write-through (v20): the policy is a design attribute — persist it
        # so a resumed session (open_bdb / load_pipeline) plans under the
        # same bands.  '*' lives in meta; a cell not yet in the cell table
        # (busterm-only flows) keeps the session-only entry with a note.
        if cell == "*":
            session.bdb.meta_set("layer_cap_default", f"{floor}:{cap}")
        else:
            try:
                session.bdb.set_cell_layer_band(cell, floor, cap)
            except RuntimeError:
                print(f"[LayerCap] note: cell '{cell}' has no cell-table row "
                      f"— policy kept for this session only")
    band = (f"[{rest[1] if floor >= 0 else 'lowest'}..{args[1]}]")
    print(f"[LayerCap] {cell}: band {band} "
          f"(ids {'%d' % floor if floor >= 0 else 'min'}..{cap})")


def _persist_by_depth(session):
    """Write the by-depth PROVENANCE memo into the open BDB's meta.

    A persisted `cell.layer_cap` alone cannot say whether it came from an
    explicit `set_cell_layer_cap` or from the bulk `set_layer_caps_by_depth`
    — and after a reload that difference decides both precedence (explicit
    always outranks) and what `set_layer_caps_by_depth off` may clear.  So
    the set of by-depth cells rides in meta beside `layer_cap_default`
    (Codex #551); `_restore_layer_policies` reads it back.  Cells with no
    band are dropped, so the memo can never resurrect a cleared entry."""
    if session.bdb is None:
        return
    pol = getattr(session, "_cell_layer_policy", None) or {}
    cells = sorted(c for c in getattr(session, "_cell_layer_policy_by_depth",
                                      set()) if c in pol)
    session.bdb.meta_set("layer_caps_by_depth", ",".join(cells))


def cmd_set_layer_caps_by_depth(session, cmd, args, cmd_line):
    # set_layer_caps_by_depth <cap1> [<cap2> ...] [-min <floor>]  |  off
    # Bulk per-cell bands from the hierarchy, counting DEEPEST-FIRST over
    # INTRINSIC cell levels (docs/internal/hier_layer_caps.md §13 Phase 5 Q1;
    # levels computed by BudaSession._cell_levels): level i gets the band
    # [floor..cap_i], so <cap1> caps the deepest leaves, <cap2> the level
    # above them, and so on.  Bands are CUMULATIVE — each level keeps the
    # cheap LOW layers and ADDS the metal its argument grants (disjoint bands
    # stay expressible per-cell via set_cell_layer_cap -min).  Levels past the
    # argument list are UNRESTRICTED, so a short list fails in the right
    # direction (the top keeps everything).  An explicit set_cell_layer_cap
    # ALWAYS outranks this default; 'off' clears only the by-depth entries.
    if session.bdb is None:
        print("Error: set_layer_caps_by_depth requires an open BDB "
              "(open_bdb first)"); return
    if not args:
        print("Error: set_layer_caps_by_depth requires <cap1> [<cap2> ...] "
              "[-min <floor_layer>] (or 'off')"); return
    pol = getattr(session, "_cell_layer_policy", None)
    if pol is None:
        pol = session._cell_layer_policy = {}
    by_depth = getattr(session, "_cell_layer_policy_by_depth", None)
    if by_depth is None:
        by_depth = session._cell_layer_policy_by_depth = set()

    if args[0].lower() == "off":
        cleared = sorted(c for c in by_depth if c in pol)
        for c in cleared:
            del pol[c]
            try:
                session.bdb.set_cell_layer_band(c, -1, -1)
            except RuntimeError:
                pass
        by_depth.clear()
        _persist_by_depth(session)
        print(f"[LayerCaps] cleared {len(cleared)} by-depth polic"
              f"{'y' if len(cleared) == 1 else 'ies'}"
              + (f": {', '.join(cleared)}" if cleared else "")
              + " (explicit set_cell_layer_cap entries untouched; re-run the "
                "planner to lift masks already applied)")
        return

    toks = list(args)
    floor, floor_tok = -1, None
    if "-min" in toks:
        i = toks.index("-min")
        if i + 1 >= len(toks):
            print("Error: set_layer_caps_by_depth: -min needs a floor layer")
            return
        floor_tok = toks[i + 1]
        floor = _resolve_layer_id(session, floor_tok)
        if floor is None:
            print(f"Error: set_layer_caps_by_depth: unknown floor layer "
                  f"'{floor_tok}'"); return
        del toks[i:i + 2]
    if not toks:
        print("Error: set_layer_caps_by_depth requires at least one cap layer")
        return
    caps = []
    for lvl, tok in enumerate(toks, 1):
        cap = _resolve_layer_id(session, tok)
        if cap is None:
            print(f"Error: set_layer_caps_by_depth: unknown layer '{tok}' "
                  f"(declare it with def_layer first)"); return
        if floor >= 0 and floor > cap:
            print(f"Error: set_layer_caps_by_depth: level {lvl}'s cap {tok} "
                  f"is below the floor {floor_tok} — the band is empty")
            return
        missing = _band_missing_dir(session, floor, cap)
        if missing:
            print(f"Error: set_layer_caps_by_depth: level {lvl}'s band "
                  f"[{floor_tok or 'lowest'}..{tok}] grants no {missing} "
                  f"routing layer — an unroutable policy"); return
        caps.append((tok, cap))
    ids = [c for _t, c in caps]
    if any(ids[i] > ids[i + 1] for i in range(len(ids) - 1)):
        print("[LayerCaps] note: caps are not non-decreasing — a level is "
              "capped BELOW the level under it (deepest-first order; "
              "intended?)")

    levels, _containers = session._cell_levels()
    if not levels:
        print("[LayerCaps] no cells in the open BDB — nothing to cap"); return
    per_level, kept_explicit, unrestricted, no_row = {}, [], [], []
    freed = []
    for cell in sorted(levels):
        lvl = levels[cell]
        # An entry this command owns is by-depth; ANY other entry (typed this
        # session OR restored from the BDB, where a persisted band can only
        # have come from an explicit set_cell_layer_cap unless this command
        # recorded it — see the by-depth meta memo) is explicit and wins.
        is_explicit = cell in pol and cell not in by_depth
        if lvl > len(caps):
            unrestricted.append(cell)
            # A SHORTENED cap list must actually free the levels it no longer
            # names: without this the stale band survives in the session, in
            # by_depth and in the BDB while the report claims unrestricted.
            if cell in by_depth:
                pol.pop(cell, None)
                by_depth.discard(cell)
                try:
                    session.bdb.set_cell_layer_band(cell, -1, -1)
                except RuntimeError:
                    pass
                freed.append(cell)
            continue
        if is_explicit:
            kept_explicit.append(cell)          # explicit declaration wins
            continue
        cap = caps[lvl - 1][1]
        pol[cell] = (floor, cap)
        by_depth.add(cell)
        getattr(session, "_cell_layer_policy_restored", set()).discard(cell)
        try:
            session.bdb.set_cell_layer_band(cell, floor, cap)
        except RuntimeError:
            no_row.append(cell)                 # session-only (no cell row)
        per_level.setdefault(lvl, []).append(cell)
    _persist_by_depth(session)

    lo_s = floor_tok or "lowest"
    for lvl in sorted(per_level):
        cells = per_level[lvl]
        shown = ", ".join(cells[:6]) + ("" if len(cells) <= 6
                                        else f" (+{len(cells) - 6} more)")
        print(f"[LayerCaps] level {lvl} -> band [{lo_s}..{caps[lvl - 1][0]}]: "
              f"{len(cells)} cell(s): {shown}")
    if unrestricted:
        shown = ", ".join(unrestricted[:6]) + (
            "" if len(unrestricted) <= 6 else f" (+{len(unrestricted) - 6} more)")
        print(f"[LayerCaps] level {len(caps) + 1}+ unrestricted: "
              f"{len(unrestricted)} cell(s): {shown}"
              + (f" ({len(freed)} freed by the shorter list: "
                 f"{', '.join(freed)})" if freed else ""))
    if kept_explicit:
        print(f"[LayerCaps] kept {len(kept_explicit)} explicit polic"
              f"{'y' if len(kept_explicit) == 1 else 'ies'} "
              f"(set_cell_layer_cap wins): {', '.join(kept_explicit)}")
    if no_row:
        print(f"[LayerCaps] note: {len(no_row)} cell(s) have no cell-table row "
              f"— policy kept for this session only: {', '.join(no_row)}")


def _all_layer_ids(session):
    """Every declared layer id, ascending (H and V unioned)."""
    ids = set()
    for d in (buda.LayerDir.HORIZONTAL, buda.LayerDir.VERTICAL):
        ids.update(session.layers.get_layer_ids_by_dir(d))
    return sorted(ids)


def cmd_reserve_top_layers(session, cmd, args, cmd_line):
    # reserve_top_layers <N> [-min <floor>]  |  off
    # The STACK-RELATIVE spelling of the per-cell band (Phase 5 follow-up,
    # docs/internal/hier_layer_caps.md §12): reserve the top N layers of the
    # declared stack for the TOP LEVEL, and cap every other cell just below
    # them.  `set_layer_caps_by_depth M3 M5` names absolute layers, so it
    # silently goes stale when the stack grows — on the chip vehicle the same
    # text meant "reserve the top 2" at six layers and "reserve the top 6" at
    # ten, starving the cell templates (3192 unplaced vs 1708 correctly
    # scaled).  This command states the INTENT instead, so it re-derives
    # correctly on any stack.  Bulk-declared like set_layer_caps_by_depth:
    # it shares that command's ownership set and provenance memo, so an
    # explicit set_cell_layer_cap still outranks it and `off` clears it.
    if session.bdb is None:
        print("Error: reserve_top_layers requires an open BDB "
              "(open_bdb first)"); return
    if not args:
        print("Error: reserve_top_layers requires <N> [-min <floor_layer>] "
              "(or 'off')"); return
    if args[0].lower() == "off":
        cmd_set_layer_caps_by_depth(session, cmd, ["off"], cmd_line)
        return

    toks = list(args)
    floor, floor_tok = -1, None
    if "-min" in toks:
        i = toks.index("-min")
        if i + 1 >= len(toks):
            print("Error: reserve_top_layers: -min needs a floor layer"); return
        floor_tok = toks[i + 1]
        floor = _resolve_layer_id(session, floor_tok)
        if floor is None:
            print(f"Error: reserve_top_layers: unknown floor layer "
                  f"'{floor_tok}'"); return
        del toks[i:i + 2]
    if len(toks) != 1:
        print(f"Error: reserve_top_layers takes ONE count "
              f"(got '{' '.join(toks)}') — use set_layer_caps_by_depth for "
              f"a per-level ladder"); return
    try:
        n = int(toks[0])
    except ValueError:
        print(f"Error: reserve_top_layers: <N> must be an integer, got "
              f"'{toks[0]}'"); return
    if n < 1:
        print(f"Error: reserve_top_layers: N must be at least 1 (got {n}) — "
              f"reserving nothing is the same as declaring no policy"); return

    ids = _all_layer_ids(session)
    if n >= len(ids):
        print(f"Error: reserve_top_layers: N={n} leaves no layer for the "
              f"cells below the top ({len(ids)} layer(s) declared)"); return
    cap = ids[-(n + 1)]                       # highest layer NOT reserved
    reserved = ids[-n:]
    if floor >= 0 and floor > cap:
        print(f"Error: reserve_top_layers: floor {floor_tok} is above the "
              f"derived cap (layer {cap}) — the band is empty"); return
    missing = _band_missing_dir(session, floor, cap)
    if missing:
        print(f"Error: reserve_top_layers: reserving the top {n} leaves the "
              f"cells no {missing} routing layer — an unroutable policy")
        return

    pol = getattr(session, "_cell_layer_policy", None)
    if pol is None:
        pol = session._cell_layer_policy = {}
    by_depth = getattr(session, "_cell_layer_policy_by_depth", None)
    if by_depth is None:
        by_depth = session._cell_layer_policy_by_depth = set()

    def _free_bulk(cells):
        """Drop the bands this command family owns for `cells` — session
        entry, ownership memo and persisted band.  A bulk declaration
        REPLACES whatever the other one left, so every exit path that stops
        governing a cell has to free it; otherwise the command reports one
        thing while the planner still sees another (Codex #558)."""
        out = []
        for c in sorted(cells):
            if c not in by_depth:
                continue
            pol.pop(c, None)
            by_depth.discard(c)
            try:
                session.bdb.set_cell_layer_band(c, -1, -1)
            except RuntimeError:
                pass
            out.append(c)
        return out

    # Every cell BELOW the top level is capped; the top level keeps the
    # reserved layers (and everything else).  The level ladder is the
    # by-depth command's: a cell's level is intrinsic to its own subtree.
    levels, _containers = session._cell_levels()
    if not levels or max(levels.values()) < 2:
        # Nothing to reserve FOR — but a previous bulk declaration may still
        # be in force, and "nothing capped" must not mean "still capped".
        released = _free_bulk(set(by_depth))
        if released:
            _persist_by_depth(session)
        why = ("no cells in the open BDB" if not levels
               else "every cell is at level 1 — no level above to reserve for")
        print(f"[LayerCaps] {why}; nothing capped"
              + (f" ({len(released)} prior bulk polic"
                 f"{'y' if len(released) == 1 else 'ies'} cleared: "
                 f"{', '.join(released)})" if released else ""))
        return
    top_level = max(levels.values())

    capped, kept_explicit, freed, no_row = [], [], [], []
    for cell in sorted(levels):
        below_top = levels[cell] < top_level
        if cell in pol and cell not in by_depth:
            kept_explicit.append(cell)        # explicit declaration wins
            continue
        if not below_top:                     # top level: unrestricted
            freed += _free_bulk({cell})       # drop a band this family set
            continue
        pol[cell] = (floor, cap)
        by_depth.add(cell)
        getattr(session, "_cell_layer_policy_restored", set()).discard(cell)
        try:
            session.bdb.set_cell_layer_band(cell, floor, cap)
        except RuntimeError:
            no_row.append(cell)
        capped.append(cell)
    _persist_by_depth(session)

    names = session._make_layer_names()
    def lname(i):
        return names.get(i, f"L{i}")
    res_s = ", ".join(lname(i) for i in reserved)
    lo_s = floor_tok or "lowest"
    shown = ", ".join(capped[:6]) + ("" if len(capped) <= 6
                                     else f" (+{len(capped) - 6} more)")
    print(f"[LayerCaps] reserving the top {n} layer(s) ({res_s}) for level "
          f"{top_level}: {len(capped)} cell(s) below it capped to "
          f"[{lo_s}..{lname(cap)}]: {shown}")
    if freed:
        print(f"[LayerCaps] freed {len(freed)} top-level cell(s): "
              f"{', '.join(freed)}")
    if kept_explicit:
        print(f"[LayerCaps] kept {len(kept_explicit)} explicit polic"
              f"{'y' if len(kept_explicit) == 1 else 'ies'} "
              f"(set_cell_layer_cap wins): {', '.join(kept_explicit)}")
    if no_row:
        print(f"[LayerCaps] note: {len(no_row)} cell(s) have no cell-table row "
              f"— policy kept for this session only: {', '.join(no_row)}")


def cmd_set_cell_layer_share(session, cmd, args, cmd_line):
    # set_cell_layer_share <cell> <layer> <pct>
    # Fractional layer share (docs/internal/hier_layer_caps.md Phase 3): the
    # cell's OWN interconnect may use at most pct% of <layer>'s signal
    # tracks — realized as a derived THINNED pattern for cell-local solves
    # (keep the first floor(s x n_signal) SIGNAL slots per period; Q1
    # resolved: contiguous-first) and as the s x capacity scale + scalar
    # collective budget for globally-planned bundles (Q3 resolved).  The
    # share overrides the band in either direction: thin a layer inside it,
    # or grant a bounded slice above the cap (the escape valve).  pct 100
    # removes the share (explicit full use).  Validation is LOUD at
    # declaration: unknown layer/cell, a share whose floor keeps ZERO
    # tracks per period (§9.6), or a layer with no def_track_pattern (the
    # thinning needs the period) are hard errors.
    if len(args) < 3:
        print("Error: set_cell_layer_share requires <cell> <layer> <pct>")
        return
    cell = args[0]
    lid = _resolve_layer_id(session, args[1])
    if lid is None:
        print(f"Error: set_cell_layer_share: unknown layer '{args[1]}' "
              f"(declare it with def_layer first)"); return
    try:
        pct = float(args[2])
    except ValueError:
        print(f"Error: set_cell_layer_share: pct must be a number, "
              f"got '{args[2]}'"); return
    if not (0.0 < pct <= 100.0):
        print(f"Error: set_cell_layer_share: pct must be in (0, 100], "
              f"got {pct}"); return
    if (session.routing_grid is None
            or not session.routing_grid.has_layer(lid)
            or not session.routing_grid.get_layer_grid(lid)
                       .global_pattern().slots):
        print(f"Error: set_cell_layer_share: layer {args[1]} has no track "
              f"pattern — declare def_track_pattern first (the share thins "
              f"the pattern's signal slots)"); return
    pat = session.routing_grid.get_layer_grid(lid).global_pattern()
    n_sig = sum(1 for s in pat.slots if s.type == "SIGNAL")
    if n_sig == 0:
        print(f"Error: set_cell_layer_share: layer {args[1]}'s track "
              f"pattern has no SIGNAL slots — nothing to share"); return
    share = pct / 100.0
    kept = int(share * n_sig + 1e-9)
    if share < 1.0 and kept == 0:
        import math
        min_pct = math.ceil(100.0 / n_sig)
        print(f"Error: set_cell_layer_share: {pct}% of layer {args[1]}'s "
              f"{n_sig} signal slot(s)/period keeps floor({share:.3f} x "
              f"{n_sig}) = 0 tracks — the budget rounds to nothing; the "
              f"minimum meaningful share is {min_pct}%"); return
    if cell != "*" and session.bdb is not None:
        if not any(c.cell == cell for c in session.bdb.all_components()):
            print(f"Error: set_cell_layer_share: unknown cell '{cell}'")
            return
    if not hasattr(session, "_cell_layer_shares") or \
            session._cell_layer_shares is None:
        session._cell_layer_shares = {}
    if share >= 1.0:
        session._cell_layer_shares.pop((cell, lid), None)
        if session.bdb is not None:
            session.bdb.set_cell_layer_share(cell, lid, 0.0)
        print(f"[LayerShare] {cell}: layer {args[1]} share 100% — explicit "
              f"full use (share removed)")
        return
    session._cell_layer_shares[(cell, lid)] = share
    getattr(session, "_cell_layer_shares_restored", set()).discard((cell, lid))
    if session.bdb is not None:
        try:
            session.bdb.set_cell_layer_share(cell, lid, share)
        except RuntimeError:
            print(f"[LayerShare] note: cell '{cell}' has no cell-table row "
                  f"— share kept for this session only")
    granted = 100.0 * kept / n_sig
    print(f"[LayerShare] {cell}: layer {args[1]} share {pct:g}% -> "
          f"{kept}/{n_sig} slot(s)/period kept (granted {granted:.1f}% "
          f"<= declared)")


def cmd_set_bottom_up(session, cmd, args, cmd_line):
    # set_bottom_up <cell>|* [on|off]
    # Mark a cell template for bottom-up planning: its cell-local interconnect
    # is planned/NUTSed once and copied to every instance (see
    # docs/internal/hier_bottom_up_planning.md).  Persisted in the BDB.
    # '*' is the keepout-scope generalization: mark EVERY eligible cell at
    # once (congruent placed instances — >= 2 = solve-once-COPY, a single
    # instance = solve-once + freeze as a keepout, nothing to copy;
    # '* off' clears all marks).
    if not args:
        print("Error: set_bottom_up requires <cell>|* [on|off]"); return
    if session.bdb is None:
        print("Error: open_bdb first"); return
    cell = args[0]
    mode = args[1].lower() if len(args) > 1 else "on"
    if mode not in ("on", "off"):
        print(f"Error: set_bottom_up mode must be on|off, got '{args[1]}'")
        return
    on = mode == "on"
    if cell == "*":
        _set_bottom_up_all(session, on)
        return
    comps = session.bdb.all_components()
    insts = [c for c in comps if c.cell == cell]
    if on:
        # Bottom-up copies are translation-only, so every instance must be a
        # pure translated copy: identity orientation, equal outline, identical
        # full subtree (rotate/flip of a hierarchical block rewrites the
        # children while keeping orient='N', so geometry must be compared).
        issues = session._bottom_up_congruence_issues(cell, comps)
        if issues:
            shown = "; ".join(issues[:4])
            more = "" if len(issues) <= 4 else f" (+{len(issues) - 4} more)"
            print(f"Error: set_bottom_up {cell}: instances are not congruent "
                  f"(translated-only copies impossible): {shown}{more}")
            return
    try:
        session.bdb.set_cell_bottom_up(cell, on)
    except RuntimeError as e:
        print(f"Error: {e}"); return
    print(f"[BDB] cell '{cell}' bottom_up = {mode} ({len(insts)} instance(s))")


def _set_bottom_up_all(session, on):
    # set_bottom_up * [on|off] — the keepout-scope generalization.
    # ON marks every ELIGIBLE cell (congruent placed instances: >= 2 =
    # solve-once-copy, a single instance = solve-once + freeze as a keepout);
    # cells whose >= 2 instances are non-congruent (cannot be frozen) are
    # reported and left on the top-down path (fail LOUD, never silent).
    # OFF clears the mark on every currently-marked cell.
    if not on:
        cleared = list(session.bdb.bottom_up_cells())
        for c in cleared:
            session.bdb.set_cell_bottom_up(c, False)
        print(f"[BDB] set_bottom_up * off: cleared {len(cleared)} cell(s)")
        return
    eligible, skipped = session._eligible_bottom_up_cells()
    elig_set = set(eligible)
    for c in eligible:
        session.bdb.set_cell_bottom_up(c, True)
    # Enforce the invariant "after '* on', the marked set == the eligible
    # set": clear any cell that is currently marked but no longer eligible
    # (Codex #265).  A stale mark on a now-non-congruent cell would
    # otherwise survive and make run_planner hier hit the unsupported
    # template path instead of leaving the cell top-down as reported.
    stale = sorted(c for c in session.bdb.bottom_up_cells()
                   if c not in elig_set)
    for c in stale:
        session.bdb.set_cell_bottom_up(c, False)
    print(f"[BDB] set_bottom_up * on: marked {len(eligible)} eligible cell(s)"
          + (f": {', '.join(eligible)}" if eligible else ""))
    if stale:
        print(f"[BDB] set_bottom_up *: cleared {len(stale)} stale mark(s) now "
              f"ineligible: {', '.join(stale)}")
    if skipped:
        print(f"[BDB] set_bottom_up *: {len(skipped)} cell(s) left top-down "
              "(not congruent — cannot freeze-and-copy):")
        for c, reason in skipped[:8]:
            print(f"    {c}: {reason}")
        if len(skipped) > 8:
            print(f"    (+{len(skipped) - 8} more)")


def cmd_align_bottom_up(session, cmd, args, cmd_line):
    # align_bottom_up [max_shift <um>] [force]
    # Nudge every set_bottom_up cell's instances onto a common track phase
    # with minimal total movement (subtree-aware translate_comp), so the
    # bottom-up copies land on real signal tracks in every occurrence.
    # Requires an open BDB and a routing grid (def_track_pattern); run
    # BEFORE derive_busterms / add_blocks_from_bdb.  Optional max_shift
    # skips any nudge larger than the given distance (um).  By default a
    # move that introduces a NEW overlap / outside-die issue is auto-
    # REVERTED (exact-geometry slack cap); 'force' keeps such moves and
    # only warns.
    if session.bdb is None:
        print("Error: open_bdb first"); return
    max_shift = None
    force = False
    i = 0
    while i < len(args):
        tok = args[i].lower()
        if tok == "max_shift" and i + 1 < len(args):
            try:
                max_shift = float(args[i + 1])
            except ValueError:
                print(f"Error: max_shift needs a number, got '{args[i+1]}'")
                return
            i += 2
        elif tok == "force":
            force = True
            i += 1
        else:
            print("Error: usage: align_bottom_up [max_shift <um>] [force]")
            return
    session._align_bottom_up(max_shift=max_shift, force=force)


def cmd_save_bdb(session, cmd, args, cmd_line):
    # save_bdb [<path.bdb | path.bdb.sql>]
    # No argument: serialize the working BDB back to its writeback source
    # .sql now (only meaningful after `open_bdb <file>.sql writeback`).
    # With a path: SAVE-AS — snapshot the CURRENT state to a new file,
    # independent of any writeback source (e.g. a placement checkpoint right
    # after align_bottom_up). A .sql destination gets the diffable text form
    # (tools/bdb_serialize), anything else a binary SQLite snapshot; an
    # existing destination is overwritten. Unlike the no-arg form, a save-as
    # is a one-shot copy: it is NOT re-flushed at exit.
    if args:
        if session.bdb is None:
            print("Error: open_bdb first"); return
        dest = args[0]
        if (session._script_stack and not os.path.isabs(dest)):
            parent_dir = os.path.dirname(session._script_stack[-1])
            dest = os.path.normpath(os.path.join(parent_dir, dest))
        live = getattr(session, "_bdb_open_path", None)
        if live and live != ':memory:':
            # Symlink-proof: realpath resolves links in the destination AND
            # its parents; samefile additionally catches hard links when
            # both ends exist. A backup onto the file the live connection
            # holds open would corrupt it, not snapshot it.
            same = os.path.realpath(dest) == os.path.realpath(live)
            if not same and os.path.exists(dest) and os.path.exists(live):
                try:
                    same = os.path.samefile(dest, live)
                except OSError:
                    pass
            if same:
                print("Error: save_bdb: destination is the live BDB file "
                      "itself — a binary BDB already persists in place; "
                      "pick a different path for the snapshot")
                return
        try:
            if dest.endswith(".sql"):
                import shutil
                import tempfile
                import bdb_serialize
                tmp_dir = tempfile.mkdtemp(prefix="buda_saveas_")
                try:
                    tmp = os.path.join(tmp_dir,
                                       os.path.basename(dest)[:-len(".sql")])
                    session.bdb.save_copy(tmp)
                    bdb_serialize.dump(tmp, dest)
                finally:
                    # rmtree, not remove+rmdir: BDB runs journal_mode=WAL,
                    # the backup copy inherits it, and the dump's connection
                    # can leave -wal/-shm sidecars beside the temp binary.
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            else:
                session.bdb.save_copy(dest)
        except RuntimeError as e:
            print(f"Error: {e}"); return
        print(f"save_bdb: wrote snapshot {dest}")
        return
    if session._write_bdb_sql():
        print(f"save_bdb: wrote {session._bdb_writeback_src}")
    else:
        print("save_bdb: nothing to write — open the BDB with "
              "`open_bdb <file>.sql writeback` to enable write-back, or "
              "give a path to save a snapshot copy: save_bdb <file.bdb[.sql]>")


COMMANDS = {
    "bdb_net_mode": cmd_bdb_net_mode,
    "add_cell_pin": cmd_add_cell_pin,
    "def_gds_layer": cmd_def_gds_layer,
    "open_bdb": cmd_open_bdb,
    "import_def_lef": cmd_import_def_lef,
    "import_verilog": cmd_import_verilog,
    "import_gds": cmd_import_gds,
    "export_gds": cmd_export_gds,
    "add_blocks_from_bdb": cmd_add_blocks_from_bdb,
    "set_die": cmd_set_die,
    "move_comp": cmd_move_comp,
    "resize_cell": cmd_resize_cell,
    "add_cell": cmd_add_cell,
    "add_inst": cmd_add_inst,
    "add_inst_to_cell": cmd_add_inst_to_cell,
    "flip_comp": cmd_flip_comp,
    "rotate_comp": cmd_rotate_comp,
    "add_comp": cmd_add_comp,
    "derive_busterms": cmd_derive_busterms,
    "refine_busterms": cmd_refine_busterms,
    "load_pipeline": cmd_load_pipeline,
    "save_bdb": cmd_save_bdb,
    "set_bottom_up": cmd_set_bottom_up,
    "set_cell_layer_cap": cmd_set_cell_layer_cap,
    "set_cell_layer_share": cmd_set_cell_layer_share,
    "set_layer_caps_by_depth": cmd_set_layer_caps_by_depth,
    "reserve_top_layers": cmd_reserve_top_layers,
    "align_bottom_up": cmd_align_bottom_up,
}
