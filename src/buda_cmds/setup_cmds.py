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

"""Setup commands: technology, floorplan, netlist, routing policy.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import buda
import sys
import re

from buda_session.util import min_bit_pitch

from ._options import reject_unknown_options, require_int

_ADD_BLOCK_USAGE = (
    "add_block <name> <x1> <y1> <x2> <y2> [container] [corner_margin ...]  |  "
    "add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [teg_mode thru|over]")
_ADD_KEEPOUT_USAGE = "add_keepout <x1> <y1> <x2> <y2> <layer> [<layer> ...]"
# Blocks and keepouts share the floorplan's integer coordinate space, so they
# share the clause explaining what a fractional coordinate would have cost.
_BLOCK_COORD_WHY = ("A block's corners define Hanan grid lines, which every "
                    "later stage snaps to.")
_KEEPOUT_COORD_WHY = ("Truncating would SHRINK the zone, leaving routing that "
                      "looks legal over blocked ground.")


def cmd_add_block(session, cmd, args, cmd_line):
    # Single-rect: add_block <name> <x1> <y1> <x2> <y2> [corner_margin ...]
    # Multi-rect:  add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [corner_margin ...]
    name = args[0]
    # Block names must be unique.  Floorplan::add_block silently OVERWRITES an
    # existing block (last-wins), so a redefinition — or a typo'd name that
    # collides with a real block — silently moves/resizes it or drops one of two
    # intended blocks.  Reject it (like the duplicate add_net guard).
    if session.fp.has_block(name):
        print(f"Error: block '{name}' is already defined — "
              f"duplicate add_block (block names must be unique)")
        sys.exit(1)

    def _block_coord(what, tok):
        return require_int("add_block", what, tok, usage=_ADD_BLOCK_USAGE,
                           why=_BLOCK_COORD_WHY)

    if len(args) > 1 and args[1].lower() == "rect":
        rects = []
        i = 1
        while i < len(args) and args[i].lower() == "rect":
            n = len(rects) + 1
            rects.append(tuple(
                _block_coord(f"<{ax}> of rect {n}", args[i + k])
                for k, ax in enumerate(("x1", "y1", "x2", "y2"), start=1)))
            i += 5
        # Optional teg_mode keyword after rects
        teg_mode = buda.TegMode.THRU
        if i < len(args) and args[i].lower() == "teg_mode":
            i += 1
            if i < len(args):
                # A bad teg_mode value used to silently fall back to THRU.
                reject_unknown_options("add_block teg_mode",
                                       [args[i].lower()], ("over", "thru"))
                teg_mode = buda.TegMode.OVER if args[i].lower() == "over" else buda.TegMode.THRU
                i += 1
        session.fp.add_block_rects(name, rects, teg_mode)
        x1 = min(r[0] for r in rects); y1 = min(r[1] for r in rects)
        x2 = max(r[2] for r in rects); y2 = max(r[3] for r in rects)
        rest = list(args[i:])
    else:
        x1, y1, x2, y2 = (_block_coord(f"<{ax}>", args[k]) for k, ax
                          in enumerate(("x1", "y1", "x2", "y2"), start=1))
        session.fp.add_block(name, x1, y1, x2, y2)
        rest = list(args[5:])
    # Optional 'container' flag: marks a hierarchy envelope (transparent
    # to LOW layers) rather than a solid leaf cell.  See Gap 2.
    if any(t.lower() == "container" for t in rest):
        session.fp.set_container(name)
        rest = [t for t in rest if t.lower() != "container"]
    if rest and rest[0].lower() != "corner_margin":
        # Unknown trailing token(s) after the geometry — `container` was already
        # stripped above, so the only thing left may be `corner_margin`.  A
        # stray token (e.g. `add_block b 0 0 10 10 garbage`) used to be dropped.
        reject_unknown_options("add_block", [rest[0].lower()],
                               ("container", "corner_margin"))
    if rest and rest[0].lower() == "corner_margin":
        rest = rest[1:]
        kws = {}
        i = 0
        while i < len(rest):
            kw = rest[i].lower()
            if kw in ("dx", "dy", "pct_h", "pct_v"):
                # A recognized key MUST be followed by a value — erroring here
                # (rather than falling to the reject branch, which would return
                # without advancing i) avoids an infinite loop on a trailing key.
                if i + 1 >= len(rest):
                    print(f"Error: add_block corner_margin {kw} requires a value")
                    sys.exit(1)
                kws[kw] = float(rest[i + 1]); i += 2
            else:
                # A bad corner_margin sub-keyword used to be silently skipped.
                reject_unknown_options("add_block corner_margin", [kw],
                                       ("dx", "dy", "pct_h", "pct_v"))
        # Resolve to absolute dx, dy
        cm_dx = cm_dy = 0
        if "dx" in kws:    cm_dx = int(round(kws["dx"]))
        if "dy" in kws:    cm_dy = int(round(kws["dy"]))
        if "pct_h" in kws: cm_dx = int(round((x2 - x1) * kws["pct_h"] / 100.0))
        if "pct_v" in kws: cm_dy = int(round((y2 - y1) * kws["pct_v"] / 100.0))
        # If only one axis specified, mirror to the other
        if "dx" in kws and "dy" not in kws and "pct_v" not in kws: cm_dy = cm_dx
        if "dy" in kws and "dx" not in kws and "pct_h" not in kws: cm_dx = cm_dy
        if "pct_h" in kws and "pct_v" not in kws and "dy" not in kws: cm_dy = cm_dx
        if "pct_v" in kws and "pct_h" not in kws and "dx" not in kws: cm_dx = cm_dy
        if cm_dx > 0 or cm_dy > 0:
            session.fp.set_block_corner_margin(name, cm_dx, cm_dy)


def cmd_corner_margin(session, cmd, args, cmd_line):
    # Syntax: corner_margin [dx <dx>] [dy <dy>] [pct_h <pct>] [pct_v <pct>]
    #      or corner_margin <dx> [<dy>]
    kws = {}
    i = 0
    while i < len(args):
        kw = args[i].lower()
        if kw in ("dx", "dy"):
            # A recognized key MUST be followed by a value — error here rather
            # than fall to the reject branch (which returns without advancing i
            # and would loop forever on a trailing `dx`/`dy`).
            if i + 1 >= len(args):
                print(f"Error: corner_margin {kw} requires a value"); sys.exit(1)
            kws[kw] = float(args[i + 1]); i += 2
        elif kw[0].isdigit() or (kw[0] == '-' and len(kw) > 1 and kw[1].isdigit()): # Positional
            if "dx" not in kws: kws["dx"] = float(kw)
            elif "dy" not in kws: kws["dy"] = float(kw)
            i += 1
        elif kw in ("pct_h", "pct_v"):
            print(f"Error: corner_margin pct_h/pct_v not supported globally "
                  f"(no single block dimension to use). Use dx/dy instead.")
            i += 2
        else:
            # An unknown token used to be silently skipped.
            reject_unknown_options("corner_margin", [kw],
                                   ("dx", "dy", "pct_h", "pct_v"))
    cm_dx = int(round(kws.get("dx", 0)))
    cm_dy = int(round(kws.get("dy", 0)))
    if "dx" in kws and "dy" not in kws: cm_dy = cm_dx
    if "dy" in kws and "dx" not in kws: cm_dx = cm_dy
    session.fp.set_global_corner_margin(cm_dx, cm_dy)
    session._corner_margin = (cm_dx, cm_dy)


def cmd_set_min_stub_length(session, cmd, args, cmd_line):
    # The setting lives on the session Floorplan; the session mirror lets
    # DERIVED floorplans (hier cell-local / cross-level / depth projection)
    # re-apply it, so their generation and local solves see the same stub
    # semantics as the flat pipeline (_apply_fp_session_settings).
    if args:
        session.fp.set_min_stub_length(int(args[0]))
        session._min_stub["global"] = int(args[0])


def cmd_set_min_stub_length_dir(session, cmd, args, cmd_line):
    if len(args) >= 2:
        dstr = args[0].upper()
        val = int(args[1])
        if dstr in ("H", "HORIZONTAL"):
            session.fp.set_min_stub_length_dir(buda.LayerDir.HORIZONTAL, val)
            session._min_stub["dir"][buda.LayerDir.HORIZONTAL] = val
        elif dstr in ("V", "VERTICAL"):
            session.fp.set_min_stub_length_dir(buda.LayerDir.VERTICAL, val)
            session._min_stub["dir"][buda.LayerDir.VERTICAL] = val
        else:
            print(f"Error: unknown direction '{args[0]}' — use H or V")


def cmd_set_min_stub_length_layer(session, cmd, args, cmd_line):
    if len(args) >= 2:
        lname = args[0]
        val = int(args[1])
        lid = session._layer_name_map.get(lname)
        if lid is not None:
            session.fp.set_min_stub_length_layer(lid, val)
            session._min_stub["layer"][lid] = val
        else:
            print(f"Error: unknown layer '{lname}'")


def cmd_set_feedthru(session, cmd, args, cmd_line):
    # set_feedthru <blocks|*> <layers|*> [on|off]   (value defaults to on)
    #   blocks : comma-separated block names, or * / all
    #   layers : comma-separated layer names or ids, or * / all
    # Resolution is most-specific-first: (block,layer) > (block,*) > (*,layer) > global.
    if len(args) < 2:
        print("Error: usage: set_feedthru <blocks|*> <layers|*> [on|off]")
    else:
        blocks_tok, layers_tok = args[0], args[1]
        val, ok = True, True
        if len(args) >= 3:
            v = args[2].lower()
            if v in ("on", "true", "1", "yes"):
                val = True
            elif v in ("off", "false", "0", "no"):
                val = False
            else:
                print(f"Error: unknown on/off value '{args[2]}' — use on or off")
                ok = False
        if ok:
            blocks_wild = blocks_tok.lower() in ("*", "all")
            layers_wild = layers_tok.lower() in ("*", "all")
            block_names = []
            if not blocks_wild:
                known = {n for n, _ in session.fp.get_all_blocks()}
                for b in blocks_tok.split(","):
                    b = b.strip()
                    if not b:
                        continue
                    if b in known:
                        block_names.append(b)
                    else:
                        print(f"Warning: unknown block '{b}' in set_feedthru")
            layer_ids = []
            if not layers_wild:
                for t in layers_tok.split(","):
                    t = t.strip()
                    if not t:
                        continue
                    if t.isdigit():
                        layer_ids.append(int(t))
                    elif t in session._layer_name_map:
                        layer_ids.append(session._layer_name_map[t])
                    else:
                        print(f"Warning: unknown layer '{t}' in set_feedthru")
            if blocks_wild and layers_wild:
                session.fp.set_feedthru(val)
            elif blocks_wild:
                for lid in layer_ids:
                    session.fp.set_feedthru_layer(lid, val)
            elif layers_wild:
                for n in block_names:
                    session.fp.set_feedthru_block(n, val)
            else:
                for n in block_names:
                    for lid in layer_ids:
                        session.fp.set_feedthru_block_layer(n, lid, val)


def cmd_detour_channel(session, cmd, args, cmd_line):
    # Usage: detour_channel <dir> <size> [<dir> <size> ...]
    # dir : N/S/E/W (single), Y (N+S), X (E+W), A (all four).
    # size: outer-band width in layout units; negative resets to auto.
    # Multiple dir/size pairs may appear in one command, e.g.:
    #   detour_channel Y 50 X 30
    i = 0
    _VALID_DIR_CHARS = set("NSEWYXA")
    while i + 1 < len(args):
        dirs = args[i]
        # Validate every direction char — the C++ set_detour_channel silently
        # ignores an unrecognized char (its switch has a `default: break`), so a
        # typo like `Q` would be a no-op with no diagnostic.
        bad = [c for c in dirs.upper() if c not in _VALID_DIR_CHARS]
        if bad:
            print(f"Error: detour_channel: unknown direction char(s) "
                  f"{', '.join(repr(c) for c in bad)} in '{dirs}'. Valid: "
                  f"N S E W (single side), Y (N+S), X (E+W), A (all four).")
            sys.exit(1)
        try:
            size = int(args[i + 1])
        except ValueError:
            print(f"Error: detour_channel size must be an integer, got '{args[i+1]}'")
            sys.exit(1)
        session.fp.set_detour_channel(dirs, size)
        i += 2
    # An odd final token (e.g. `detour_channel N 50 Q`) is a direction with no
    # size — the pair loop skips it; reject it instead of silently ignoring.
    if i < len(args):
        print(f"Error: detour_channel: unpaired trailing token '{args[i]}' — "
              f"needs <dir> <size> pair(s)")
        sys.exit(1)


def cmd_add_keepout(session, cmd, args, cmd_line):
    # Usage: add_keepout <x1> <y1> <x2> <y2> <layer1> <layer2> ...
    if len(args) < 5:
        print("Error: add_keepout requires x1 y1 x2 y2 and at least one layer")
        return

    def _ko_coord(what, tok):
        return require_int("add_keepout", what, tok, usage=_ADD_KEEPOUT_USAGE,
                           why=_KEEPOUT_COORD_WHY)
    x1, y1 = _ko_coord("<x1>", args[0]), _ko_coord("<y1>", args[1])
    x2, y2 = _ko_coord("<x2>", args[2]), _ko_coord("<y2>", args[3])
    try:
        layer_ids = []
        for name in args[4:]:
            if name.isdigit():
                layer_ids.append(int(name))
            elif name in session._layer_name_map:
                layer_ids.append(session._layer_name_map[name])
            else:
                print(f"Warning: unknown layer '{name}' in add_keepout")

        if not layer_ids:
            print("Error: no valid layers specified for add_keepout")
            return

        # 1. Update Floorplan (for CongestionPlanner / Stage 7)
        session.fp.add_keepout_zone(x1, y1, x2, y2, layer_ids)

        # 2. Update RoutingGrid (for DetailedNUTS / Stage 9)
        if session.routing_grid:
            for lid in layer_ids:
                if session.routing_grid.has_layer(lid):
                    session.routing_grid.add_keepout(lid, x1, y1, x2, y2)

        print(f"[Floorplan] Added keepout zone at ({x1},{y1})-({x2},{y2}) "
              f"for layers {layer_ids}")
    except (ValueError, IndexError):
        print("Error: invalid arguments for add_keepout")


def cmd_add_net(session, cmd, args, cmd_line):
    # Syntax A (directed):       add_net <name> <drv_pin> <rcv_pins_csv>
    # Syntax B (undirected):     add_net <name> <pin1> <pin2_csv> unknown
    # Syntax C (bidirectional):  add_net <name> <pin1> <pin2_csv> inout
    # The optional 4th token is a direction keyword — reject anything else (a
    # typo like `unkown` used to be dropped, silently making a DIRECTED net).
    if len(args) >= 4:
        reject_unknown_options("add_net", [a.lower() for a in args[3:]],
                               ("unknown", "inout"))
    last_kw = args[3].lower() if len(args) >= 4 else ""
    unknown_dir = (last_kw == "unknown")
    inout_dir   = (last_kw == "inout")
    name, drv_pin, rcv_str = args[0], args[1], args[2]
    # A net name must be unique: Netlist::add_net only appends, so a redefinition
    # silently creates a SECOND net of the same name (double-counted bits, or —
    # with different endpoints — two same-named bundles plus a clobbered
    # _net_endpoints entry).  Reject it, like the unknown-command guard.
    if name in session._net_endpoints:
        print(f"Error: net '{name}' is already defined — "
              f"duplicate add_net (net names must be unique)")
        sys.exit(1)
    rcv_pins = rcv_str.split(',')
    drv_inst = session._pin_instance(drv_pin)
    rcv_insts = [session._pin_instance(r) for r in rcv_pins]
    if not (unknown_dir or inout_dir) and drv_inst in rcv_insts:
        print(f"Error: block '{drv_inst}' is used as both driver and receiver in net '{name}'")
        sys.exit(1)
    session.netlist.add_net(name, drv_pin, rcv_pins)
    session._net_endpoints[name] = (drv_inst, rcv_insts)
    if session.bdb is not None and session.bdb_net_mode:
        if unknown_dir:
            session.bdb.add_net_pins_undirected(name, [drv_pin] + rcv_pins)
        elif inout_dir:
            session.bdb.add_net_pins_inout(name, [drv_pin] + rcv_pins)
        else:
            session.bdb.add_net_pins(name, drv_pin, rcv_pins)


def cmd_add_bus(session, cmd, args, cmd_line):
    # Syntax A (directed):       add_bus <prefix>[N] <drv_pin> <rcv_pin>
    # Syntax B (undirected):     add_bus <prefix>[N] <pin1> <pin2> unknown
    # Syntax C (bidirectional):  add_bus <prefix>[N] <pin1> <pin2> inout
    import re
    # A directed bus has exactly 3 args; a 4th (trailing) token must be a
    # direction keyword — reject anything else (a typo used to be silently
    # dropped, keeping the bus directed).
    if len(args) >= 4:
        reject_unknown_options("add_bus", [a.lower() for a in args[3:]],
                               ("unknown", "inout"))
    last_kw = args[-1].lower() if args else ""
    unknown_dir = (last_kw == "unknown")
    inout_dir   = (last_kw == "inout")
    bus_args = args[:-1] if (unknown_dir or inout_dir) else args
    m = re.match(r'^(.+)\[(\d+)(?::(\d+))?\]$', bus_args[0])
    if not m:
        print(f"Error: bad add_bus syntax '{bus_args[0]}' — expected name[N] or name[lo:hi]")
        return
    prefix = m.group(1)
    lo = int(m.group(2))
    hi = int(m.group(3)) if m.group(3) is not None else lo - 1
    if m.group(3) is None:      # name[N]  → indices 0 … N-1
        lo, hi = 0, int(m.group(2)) - 1
    elif lo > hi:
        # Verilog-style descending range (bus[7:0] = bits 0..7): normalize —
        # range(lo, hi+1) on the raw order was EMPTY and silently created
        # zero nets (audit P5-02).
        lo, hi = hi, lo
    drv_pin  = bus_args[1]
    rcv_pins = bus_args[2].split(',')
    drv_inst = session._pin_instance(drv_pin)
    rcv_insts = [session._pin_instance(r) for r in rcv_pins]
    if not (unknown_dir or inout_dir) and drv_inst in rcv_insts:
        print(f"Error: block '{drv_inst}' is used as both driver and receiver in bus '{prefix}'")
        sys.exit(1)
    # Net names must be unique — reject a bus that redefines any already-defined
    # bit (else Netlist::add_net silently doubles it).  Check the whole range
    # BEFORE inserting any bit, so a collision leaves the netlist untouched.
    dup = [f"{prefix}_{i}" for i in range(lo, hi + 1)
           if f"{prefix}_{i}" in session._net_endpoints]
    if dup:
        shown = ", ".join(dup[:4]) + (" …" if len(dup) > 4 else "")
        print(f"Error: bus '{prefix}' redefines already-defined net(s): {shown} — "
              f"duplicate add_bus (net names must be unique)")
        sys.exit(1)
    for i in range(lo, hi + 1):
        net_name = f"{prefix}_{i}"
        session.netlist.add_net(net_name, drv_pin, rcv_pins)
        session._net_endpoints[net_name] = (drv_inst, rcv_insts)
        if session.bdb is not None and session.bdb_net_mode:
            if unknown_dir:
                session.bdb.add_net_pins_undirected(net_name, [drv_pin] + rcv_pins)
            elif inout_dir:
                session.bdb.add_net_pins_inout(net_name, [drv_pin] + rcv_pins)
            else:
                session.bdb.add_net_pins(net_name, drv_pin, rcv_pins)


def cmd_def_layer(session, cmd, args, cmd_line):
    # def_layer <id> <name> <H|V> [TOP|LOW] <overhead%>
    #           [span_min N] [span_max N] [kSpan K]
    # TOP/LOW is optional; omitting it means non-TOP. LOW is accepted for
    # backward compatibility and treated as non-TOP.
    lid, name, dirstr = args[0], args[1], args[2]
    # Layer id AND name must be unique.  LayerStack::add_layer silently keeps the
    # FIRST layer for a duplicate id (the redefinition is dropped), while a reused
    # NAME silently clobbers the name->id map (last-wins), so name-based lookups
    # (set_min_stub_length_layer, def_track_pattern dir, …) resolve to the wrong
    # layer.  Reject both (like the duplicate add_net guard).
    # Precedence (Phase 2b): an explicit `def_layer` ALWAYS outranks a layer
    # that came from `import_lef_tech`, in either declaration order.  Import
    # skips what the script already declared; here, the script REPLACES what
    # the import provided.  `add_layer` appends, so the old row has to go —
    # a duplicate id would leave both in the vector with lookups silently
    # taking the first, i.e. the imported one.
    _replacing_import = session._layer_source.get(int(lid)) == "lef"
    if _replacing_import:
        old_name = next((n for n, i in session._layer_name_map.items()
                         if i == int(lid)), None)
        session.layers.remove_layer(int(lid))
        if old_name is not None:
            session._layer_name_map.pop(old_name, None)
        print(f"[LEF] def_layer {name} overrides the imported layer {int(lid)}"
              + (f" ('{old_name}')" if old_name and old_name != name else ""))
    elif session.layers.has_layer(int(lid)):
        print(f"Error: layer id {int(lid)} is already defined — "
              f"duplicate def_layer (layer ids must be unique)")
        sys.exit(1)
    if name in session._layer_name_map:
        print(f"Error: layer name '{name}' is already used by layer "
              f"{session._layer_name_map[name]} — "
              f"duplicate def_layer (layer names must be unique)")
        sys.exit(1)
    rest = list(args[3:])
    if rest and rest[0].upper() in ("TOP", "LOW"):
        typestr = rest.pop(0).upper()
    else:
        typestr = "NONE"
    ovh = rest.pop(0)
    # Parse optional keyword args
    span_min = span_max = kspan_override = None
    i = 0
    while i < len(rest):
        kw = rest[i].lower()
        if kw == "span_min":    span_min = int(rest[i+1]);    i += 2
        elif kw == "span_max":  span_max = int(rest[i+1]);    i += 2
        elif kw == "kspan":     kspan_override = float(rest[i+1]); i += 2
        else:
            # An unknown trailing keyword used to be silently skipped.
            reject_unknown_options("def_layer", [kw],
                                   ("span_min", "span_max", "kspan"))
    # Fail fast on a bad direction token: anything that is not exactly H or V
    # (e.g. a typo, or arguments passed in the wrong order) used to fall
    # through SILENTLY to VERTICAL — a wrong-direction layer makes every
    # segment assigned to it an unbuildable wire (audit P5-01).
    if dirstr.upper() not in ("H", "V"):
        print(f"Error: def_layer direction must be H or V, got '{dirstr}'")
        return
    ldir  = buda.LayerDir.HORIZONTAL if dirstr.upper()=="H" else buda.LayerDir.VERTICAL
    ltype = buda.LayerType.TOP if typestr == "TOP" else buda.LayerType.LOW
    session.layers.add_layer(int(lid), name, ldir, ltype)
    if span_min is not None or span_max is not None:
        smin = span_min if span_min is not None else 0
        smax = span_max if span_max is not None else 1_000_000_000
        session.layers.set_layer_span(int(lid), smin, smax)
    if kspan_override is not None:
        session.layers.set_layer_kspan(int(lid), kspan_override)
    ovh_val = float(ovh)
    if ovh_val > 0.0:
        session.layers.set_layer_overhead(int(lid), ovh_val)
        session._layer_overheads[int(lid)] = ovh_val
    session._layer_name_map[name] = int(lid)
    session._layer_source[int(lid)] = "script"
    # An IMPORTED pattern outlives the layer row it was installed beside, and
    # the routing grid stores the layer's direction with it.  Overriding an
    # imported layer with a different H/V would otherwise leave that pattern
    # registered the old way — tracks running across the wires that use them.
    # Re-register it against the new direction; the script can still replace
    # the pattern itself with def_track_pattern.
    if _replacing_import and session._pattern_source.get(int(lid)) == "lef":
        grid = session.routing_grid.get_layer_grid(int(lid))
        session.routing_grid.define_layer(
            int(lid), grid.global_pattern(),
            ldir == buda.LayerDir.HORIZONTAL)


def cmd_set_track_pitch(session, cmd, args, cmd_line):
    # Usage: set_track_pitch <pitch>|auto
    # Declare the inter-bus pitch BEFORE run_planner so its band
    # reservations (Gap 1) match the run_nuts that packs the tracks.
    # run_nuts with no argument reuses this value.
    #
    # `auto` (Phase 1b, docs/internal/engine_units.md) DERIVES the gap from
    # the routing grid — one signal-track pitch on the densest pattern layer
    # — instead of the literal default 1.0.  The literal is the one physical
    # default the grid does not already supply (bus width is grid-derived via
    # LayerStack::eff_bus_width), so it is the one that silently means "one
    # micron" on a micron design and "one DBU" on a DBU one.  Opt-in, not a
    # new default: a derived gap is a different (larger) reservation on every
    # design that has patterns, which is a QoR change, not a unit fix — it has
    # to be measured before it can be defaulted, the way every other planner
    # knob in this repo was.
    if not args:
        print("Error: set_track_pitch requires a pitch value (or `auto`)")
        return
    if args[0].lower() == "auto":
        pitch = min_bit_pitch(session, no_pattern=0.0)
        if pitch <= 0.0:
            print("Error: set_track_pitch auto needs a track pattern — "
                  "declare def_track_pattern first, or give an explicit pitch")
            return
        session._nuts_pitch = pitch
        print(f"[Setup] set_track_pitch auto → {pitch:g} layout units "
              f"(one signal-track pitch on the densest pattern layer)")
        return
    session._nuts_pitch = float(args[0])


def _lef_layer_id(name, taken):
    """A layer id for a LEF layer NAME.

    LEF names layers; BUDA numbers them.  The trailing integer is used when
    there is one (`M3`->3, `metal5`->5, `Metal10`->10), because that is how
    every hand-written stack in this repo already numbers its layers — so an
    imported stack and a script that refers to `def_layer 4` mean the same
    thing.  A name with no number gets the next free id.

    Returns None on a COLLISION (two LEF names claiming one id).  Inventing a
    substitute would silently re-number a stack whose numbers are how the
    script refers to it."""
    m = re.search(r"(\d+)\s*$", name)
    if m:
        lid = int(m.group(1))
        return None if lid in taken else lid
    lid = 1
    while lid in taken:
        lid += 1
    return lid


def cmd_import_lef_tech(session, cmd, args, cmd_line):
    # Usage: import_lef_tech <file.lef> [top <N>]
    #
    # Phase 2b: turn a LEF technology stack into `def_layer` + track patterns,
    # replacing a hand-typed stack.  Only ROUTING layers with a DIRECTION are
    # usable — BUDA has no undirected layer, and a layer with no PITCH has no
    # track pattern to synthesize.
    #
    # PRECEDENCE: an explicit `def_layer` / `def_track_pattern` ALWAYS wins,
    # in either declaration order.  Declared first, it is skipped here;
    # declared later, it replaces what this installed (see cmd_def_layer).
    # That is what keeps every existing flow byte-identical while letting a
    # real design drop the hand-typed stack entirely.
    if not args:
        print("Error: import_lef_tech requires <file.lef>"); return
    reject_unknown_options("import_lef_tech",
                           [a for a in args[1:] if not a.replace(".", "").isdigit()],
                           ("top",))
    n_top = 2                      # the natural pair: one H + one V
    if "top" in args:
        i = args.index("top")
        if i + 1 >= len(args):
            print("Error: import_lef_tech top requires a count"); return
        try:
            n_top = int(args[i + 1])
        except ValueError:
            print(f"Error: import_lef_tech top must be an integer, "
                  f"got '{args[i + 1]}'"); return
        if n_top < 0:
            print("Error: import_lef_tech top must be >= 0"); return

    try:
        lib = buda.read_lef(args[0])
    except RuntimeError as e:
        print(f"Error: import_lef_tech: {e}"); sys.exit(1)

    routing = [l for l in lib.layers if l.type == "ROUTING"]
    if not routing:
        print(f"[LEF] {args[0]}: no ROUTING layers — nothing to import "
              f"({len(lib.layers)} layer(s) read)")
        return

    taken = set(session._layer_source) | {
        i for i in session._layer_name_map.values()}
    plan, skipped = [], []
    for l in routing:
        if not l.dir:
            skipped.append((l.name, "no DIRECTION (BUDA has no undirected layer)"))
            continue
        if l.name in session._layer_name_map:
            skipped.append((l.name, "already declared by the script"))
            continue
        lid = _lef_layer_id(l.name, taken)
        if lid is None:
            skipped.append((l.name, "layer id already in use — rename or "
                                    "declare it explicitly"))
            continue
        taken.add(lid)
        plan.append((lid, l))

    # TOP is a BUDA notion (trunk preference), not a LEF one: nothing in the
    # file says which layers the planner should prefer for spines.  The
    # highest-numbered layer in each direction is the defensible default, and
    # `top <N>` / an explicit `def_layer` override it.
    order = sorted(plan, key=lambda p: p[0], reverse=True)
    top_ids, seen_dirs = set(), set()
    for lid, l in order:
        if len(top_ids) >= n_top:
            break
        if l.dir in seen_dirs:
            continue
        seen_dirs.add(l.dir)
        top_ids.add(lid)

    for lid, l in sorted(plan):
        ldir = (buda.LayerDir.HORIZONTAL if l.dir == "HORIZONTAL"
                else buda.LayerDir.VERTICAL)
        ltype = buda.LayerType.TOP if lid in top_ids else buda.LayerType.LOW
        session.layers.add_layer(lid, l.name, ldir, ltype)
        session._layer_name_map[l.name] = lid
        session._layer_source[lid] = "lef"

        # A track pattern needs PITCH and WIDTH.  One SIGNAL slot per pitch is
        # the honest reading of LEF ALONE: the file says how far apart tracks
        # are and how wide a wire is, and says nothing about which of them a
        # power grid will take — that lives in the DEF's SPECIALNETS.  So the
        # synthesized pattern is all-signal, and a design with a real PDN
        # declares the rails itself.
        if not (l.has_pitch and l.has_width):
            continue
        if l.pitch <= l.width:
            print(f"[LEF] layer {l.name}: PITCH {l.pitch:g} <= WIDTH "
                  f"{l.width:g} — no room for spacing; pattern skipped")
            continue
        if l.has_spacing and l.width + l.spacing > l.pitch + 1e-12:
            print(f"[LEF] layer {l.name}: WIDTH {l.width:g} + SPACING "
                  f"{l.spacing:g} exceeds PITCH {l.pitch:g} — the file is "
                  f"inconsistent; using PITCH")
        slot = buda.TrackSlot(type="SIGNAL", label="",
                              width=l.width, space_after=l.pitch - l.width)
        pat = buda.TrackPattern(origin=l.offset if l.has_offset else 0.0,
                                slots=[slot])
        if session.routing_grid is None:
            session.routing_grid = buda.RoutingGridStack()
        session.routing_grid.define_layer(lid, pat,
                                          l.dir == "HORIZONTAL")
        session._pattern_source[lid] = "lef"
        session._lef_track_width[lid] = l.width
        session.layers.set_layer_dilution(lid, pat.dilution_factor())
        session.layers.set_bit_pitch(lid, pat.unit_pitch())

    rows = ", ".join(f"{l.name}={lid}{'(TOP)' if lid in top_ids else ''}"
                     for lid, l in sorted(plan))
    print(f"[LEF] imported {len(plan)} routing layer(s): {rows}")
    for name, why in skipped:
        print(f"[LEF] skipped layer {name}: {why}")


def cmd_set_unit_check(session, cmd, args, cmd_line):
    # Usage: set_unit_check [on|warn|off]
    # The unit-plausibility guard (Phase 1d): `on` (default) STOPS a run whose
    # block coordinates and track patterns are on different scales, `warn`
    # reports and continues, `off` disables the check.  See
    # docs/internal/engine_units.md.
    if not args:
        print(f"unit_check is {session._unit_check}")
        return
    val = args[0].lower()
    if val not in ("on", "warn", "off"):
        print(f"Error: set_unit_check expects on|warn|off, got {args[0]!r}")
        return
    session._unit_check = val


COMMANDS = {
    "set_unit_check": cmd_set_unit_check,
    "import_lef_tech": cmd_import_lef_tech,
    "add_block": cmd_add_block,
    "corner_margin": cmd_corner_margin,
    "set_min_stub_length": cmd_set_min_stub_length,
    "set_min_stub_length_dir": cmd_set_min_stub_length_dir,
    "set_min_stub_length_layer": cmd_set_min_stub_length_layer,
    "set_feedthru": cmd_set_feedthru,
    "detour_channel": cmd_detour_channel,
    "add_keepout": cmd_add_keepout,
    "add_net": cmd_add_net,
    "add_bus": cmd_add_bus,
    "def_layer": cmd_def_layer,
    "set_track_pitch": cmd_set_track_pitch,
}
