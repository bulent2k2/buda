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

from ._options import reject_unknown_options


def cmd_add_block(session, cmd, args, cmd_line):
    # Single-rect: add_block <name> <x1> <y1> <x2> <y2> [corner_margin ...]
    # Multi-rect:  add_block <name> rect <x1> <y1> <x2> <y2> [rect ...] [corner_margin ...]
    name = args[0]
    if len(args) > 1 and args[1].lower() == "rect":
        rects = []
        i = 1
        while i < len(args) and args[i].lower() == "rect":
            x1r, y1r, x2r, y2r = int(args[i+1]), int(args[i+2]), int(args[i+3]), int(args[i+4])
            rects.append((x1r, y1r, x2r, y2r))
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
        x1, y1, x2, y2 = int(args[1]), int(args[2]), int(args[3]), int(args[4])
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
    try:
        x1, y1 = int(float(args[0])), int(float(args[1]))
        x2, y2 = int(float(args[2])), int(float(args[3]))
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


def cmd_set_track_pitch(session, cmd, args, cmd_line):
    # Usage: set_track_pitch <pitch>
    # Declare the inter-bus pitch BEFORE run_planner so its band
    # reservations (Gap 1) match the run_nuts that packs the tracks.
    # run_nuts with no argument reuses this value.
    if not args:
        print("Error: set_track_pitch requires a pitch value"); return
    session._nuts_pitch = float(args[0])


COMMANDS = {
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
