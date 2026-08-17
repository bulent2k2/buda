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

"""Stage 8 — routing-grid track patterns.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import math
import sys

import buda

from slot_groups import MAX_SLOT_REPEAT as _MAX_SLOT_REPEAT  # noqa: F401
from slot_groups import expand_slot_groups

from ._options import (require_distance, require_int,
                       require_layer_id, require_number)

_DEF_TRACK_PATTERN_USAGE = (
    "def_track_pattern <layer_id> <origin> [<type> <width> <space_after>] ...")
_ADD_GRID_OVERRIDE_USAGE = (
    "add_grid_override <layer_id> <x1> <y1> <x2> <y2> <origin> "
    "[<type> <width> <space_after>] ...")


# Canonical track-slot types (routing_grid.h TrackSlot).  Only "SIGNAL" carries
# FUNCTIONAL meaning — bits land only on SIGNAL slots, a case-sensitive string
# compare (`type == "SIGNAL"`) throughout routing_grid.cpp / detailed_nuts.cpp;
# the other types just categorize pre-route rails for viz (the [Preroutes]
# filter cycles POWER/GROUND/CLOCK/SHIELD).  An unrecognized type used to be
# accepted verbatim and SILENTLY became a non-signal rail — so a mistyped or
# lower-cased SIGNAL lost all its tracks (guaranteed DetailedNUTS opens) with no
# warning.  These two tables turn that into a hard error.
_CANONICAL_SLOT_TYPES = ("POWER", "GROUND", "CLOCK", "SHIELD", "SIGNAL", "CUSTOM")
# Established EDA shorthand accepted and normalized to the canonical type (so the
# viz preroute filter, which matches the canonical names, groups them correctly).
# `_` is a terse shorthand for SIGNAL, by far the most common (and only routable)
# slot type, so a dense pattern reads `_ 1 1 _ 1 1` instead of `SIGNAL 1 1 …`.
_SLOT_TYPE_ALIASES = {
    "_": "SIGNAL",
    "GND": "GROUND", "CLK": "CLOCK", "VDD": "POWER", "VSS": "GROUND",
}


def _canonical_slot_type(cmd_name, raw):
    """Resolve a `def_track_pattern` / `add_grid_override` slot-type token to its
    canonical TrackSlot type (case-insensitive; the aliases above are accepted).
    An unknown token is a flow-stopping error — silently accepting it would make
    a mistyped SIGNAL a non-signal rail (lost tracks, no warning)."""
    up = raw.upper()
    if up in _CANONICAL_SLOT_TYPES:
        return up
    if up in _SLOT_TYPE_ALIASES:
        return _SLOT_TYPE_ALIASES[up]
    valid = ", ".join(_CANONICAL_SLOT_TYPES)
    aliases = ", ".join(f"{a}->{c}" for a, c in _SLOT_TYPE_ALIASES.items())
    print(f"Error: {cmd_name} unknown slot type '{raw}' — "
          f"valid types: {valid} (aliases: {aliases})")
    sys.exit(1)


def _require_slot_geometry(cmd_name, slot_type, width, space_after, usage):
    """Reject a slot whose geometry cannot describe a track.

    Track widths and spacings are genuinely FRACTIONAL — `TrackSlot::width` and
    `space_after` are `double`, and sub-unit patterns (`SIGNAL 0.4 0.4`) are a
    supported, shipped case — so this validates the SIGN, not the fraction.

    Nothing checked either before, and the value was taken at face value: a
    `SIGNAL 0 0` slot yielded `unit_pitch=0.000` and a layer with no tracks at
    all, which does not fail at the declaration.  It surfaces six stages later
    as `[DetailedNUTS] Warning: Layer 4 has insufficient signal tracks (0)`
    with every bit on the layer stranded — and the run still exits 0.  The
    declaration is where a non-positive width is diagnosable: no downstream
    circumstance makes such a layer routable.
    """
    def die(what, val, why):
        print(f"Error: {cmd_name}: {what} of slot '{slot_type}' must be "
              f"{why}, got {val:g}." + (f"\n  Usage: {usage}" if usage else ""))
        sys.exit(1)

    if not math.isfinite(width) or width <= 0:
        die("<width>", width, "a positive, finite number — a zero-, negative- "
                              "or infinite-width slot is not a track, and a "
                              "pattern of them gives the layer no tracks at all")
    if not math.isfinite(space_after) or space_after < 0:
        die("<space_after>", space_after,
            "zero or a positive, finite number — negative space would overlap "
            "the next slot (0 is fine: abutting slots)")


def _parse_slots(session, cmd_name, toks, usage):
    """Parse a `<type> <width> <space_after>` slot list (repetition groups
    expanded first) into TrackSlots.  Shared by def_track_pattern and
    add_grid_override — the two had byte-identical copies of this loop.
    Widths and spacings are DISTANCES, so the `um` suffix is accepted
    (`SIGNAL 0.07um 0.12um` declares real metal at any import scale)."""
    toks = expand_slot_groups(cmd_name, toks, usage)
    slots = []
    i = 0
    while i + 2 < len(toks):
        slot_type   = toks[i]
        width       = require_distance(cmd_name,
                                       f"<width> of slot '{slot_type}'",
                                       toks[i + 1], session, usage=usage)
        space_after = require_distance(cmd_name,
                                       f"<space_after> of slot '{slot_type}'",
                                       toks[i + 2], session, usage=usage)
        _require_slot_geometry(cmd_name, slot_type, width, space_after, usage)
        slots.append(buda.TrackSlot(
            type=_canonical_slot_type(cmd_name, slot_type),
            label=slot_type.lower(),
            width=width, space_after=space_after))
        i += 3
    return slots


def cmd_def_track_pattern(session, cmd, args, cmd_line):
    # Usage: def_track_pattern <layer_id> <origin> [<type> <width> <space_after>] ...
    # Example: def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0
    if len(args) < 2:
        print("Error: def_track_pattern requires layer_id and origin\n"
              f"  Usage: {_DEF_TRACK_PATTERN_USAGE}")
        return
    layer_id = require_layer_id("def_track_pattern", args[0], session,
                                usage=_DEF_TRACK_PATTERN_USAGE)
    origin   = require_distance("def_track_pattern", "<origin>", args[1],
                                session, usage=_DEF_TRACK_PATTERN_USAGE)
    slots = _parse_slots(session, "def_track_pattern", args[2:],
                         _DEF_TRACK_PATTERN_USAGE)
    if not slots:
        print("Error: def_track_pattern requires at least one slot triple")
        return
    pat = buda.TrackPattern(origin=origin, slots=slots)
    if session.routing_grid is None:
        session.routing_grid = buda.RoutingGridStack()
    # A layer's global track pattern must be defined once.  define_layer silently
    # OVERWRITES an existing pattern (last-wins), so a duplicate — e.g. two
    # def_track_pattern lines for the same layer — silently drops the earlier one.
    # Reject it (region-scoped variation is add_grid_override, not a re-def).
    # Guard on the GLOBAL pattern actually being defined (non-empty slots), NOT
    # on has_layer(): add_grid_override creates the layer map entry too, so an
    # override BEFORE the global def would otherwise false-trip this — a valid
    # ordering (init sets the global, keeping the earlier overrides).
    elif (session.routing_grid.has_layer(layer_id) and
          len(session.routing_grid.get_layer_grid(layer_id).global_pattern().slots) > 0):
        # Precedence (Phase 2b): an explicit pattern ALWAYS outranks one
        # synthesized by `import_lef_tech`, in either declaration order —
        # so an imported pattern is REPLACED here rather than rejected as a
        # duplicate.  define_layer already overwrites last-wins, which is
        # exactly what the override needs.
        if session._pattern_source.get(layer_id) == "lef":
            print(f"[LEF] def_track_pattern overrides the imported pattern "
                  f"on layer {layer_id}")
        elif layer_id in session._pattern_restored:
            # Restored from the open BDB (v29), not declared in THIS session.
            # The duplicate error is about a flow contradicting itself; a flow
            # re-declaring what its own checkpoint handed back is the ordinary
            # case — `open_bdb` then the same setup lines — and erroring on it
            # would make every flow that opens a checkpoint unrunnable.
            session._pattern_restored.discard(layer_id)
        else:
            print(f"Error: layer {layer_id} already has a track pattern — "
                  f"duplicate def_track_pattern (use add_grid_override for a "
                  f"region-scoped pattern)")
            sys.exit(1)

    # Resolve layer direction.
    is_h = True
    if session.layers.has_layer(layer_id):
        is_h = (session.layers.get_layer_dir(layer_id) == buda.LayerDir.HORIZONTAL)

    session.routing_grid.define_layer(layer_id, pat, is_h)
    session._persist_track_pattern(layer_id, pat, is_h, 'script')
    session._pattern_restored.discard(layer_id)
    session._pattern_source[layer_id] = "script"
    session.layers.set_layer_dilution(layer_id, pat.dilution_factor())
    # Measured per-bit channel cost: one signal track every
    # unit_pitch/n_signals units.  Supersedes the density model
    # (base width x dilution) in planner/NUTS effective widths.
    n_sig = sum(1 for s in slots if s.type == "SIGNAL")
    if n_sig > 0:
        session.layers.set_bit_pitch(layer_id, pat.unit_pitch() / n_sig)
        # R1 metal-shaped NDR needs the slot GEOMETRY, not just the pitch:
        # pushed beside it so the two can never describe different patterns.
        session.layers.set_ndr_geom(layer_id, pat.ndr_geom())

    # Re-apply any existing keepouts to this new layer grid.  Explicit layer
    # sets only: a zone with EMPTY layer_ids (= blocks all layers, Python-API
    # only) is synced by _install_leaf_keepouts before every solve — handling
    # it here too would double-install it on layers patterned after the zone.
    for koz in session.fp.get_keepout_zones():
        if layer_id in koz.layer_ids:
            session.routing_grid.add_keepout(layer_id, 
                                         koz.bbox.x1, koz.bbox.y1, 
                                         koz.bbox.x2, koz.bbox.y2)

    print(f"[RoutingGrid] Layer {layer_id}: {len(slots)} slots, "
          f"unit_pitch={pat.unit_pitch():.3f}, "
          f"signal_density={pat.signal_density():.3f}, dilution={pat.dilution_factor():.3f}")


def cmd_report_overhead(session, cmd, args, cmd_line):
    # Usage: report_overhead
    # For each layer, compare the overhead% set in def_layer against the
    # overhead implied by the track pattern.  Prints a suggested corrected
    # def_layer command for any layer where the two values diverge.
    layer_names = session._make_layer_names()

    # Collect all layer IDs that have either a def_layer overhead or a track pattern.
    all_lids: set[int] = set(session._layer_overheads.keys())
    if session.routing_grid is not None:
        for lid in (session.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL) +
                    session.layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL)):
            if session.routing_grid.has_layer(lid):
                all_lids.add(lid)

    if not all_lids:
        print("[report_overhead] No layers defined.")
        return

    print("[report_overhead] Layer overhead analysis:")
    print(f"  {'Layer':<10} {'def_layer%':>11} {'actual%':>9} {'dilution':>9}  status")
    print(f"  {'-'*10} {'-'*11} {'-'*9} {'-'*9}  ------")
    for lid in sorted(all_lids):
        lname    = layer_names.get(lid, f"L{lid}")
        def_ovh  = session._layer_overheads.get(lid)
        def_str  = f"{def_ovh:.2f}%" if def_ovh is not None else "(not set)"

        if session.routing_grid is not None and session.routing_grid.has_layer(lid):
            pat          = session.routing_grid.get_layer_grid(lid).effective_pattern_at(0.0, 0.0)
            actual_ovh   = (1.0 - pat.signal_density()) * 100.0
            actual_dil   = pat.dilution_factor()

            if def_ovh is None:
                status = "MISSING in def_layer"
            else:
                diff   = abs(actual_ovh - def_ovh)
                status = "OK" if diff < 0.05 else f"MISMATCH (diff={diff:.2f}%)"

            print(f"  {lname:<10} {def_str:>11} {actual_ovh:>8.2f}% {actual_dil:>9.4f}  {status}")

            if def_ovh is None or abs(actual_ovh - def_ovh) >= 0.05:
                if session.layers.has_layer(lid):
                    dir_str  = "H" if session.layers.get_layer_dir(lid) == buda.LayerDir.HORIZONTAL else "V"
                    type_str = " TOP" if session.layers.is_top(lid) else ""
                    print(f"    -> suggested: def_layer {lid} {lname} {dir_str}{type_str} {actual_ovh:.2f}")
        else:
            print(f"  {lname:<10} {def_str:>11} {'(no pattern)':>9}  {'':>9}  no track pattern defined")


def cmd_add_grid_override(session, cmd, args, cmd_line):
    # Usage: add_grid_override <layer_id> <x1> <y1> <x2> <y2> <origin> [<type> <w> <sp>] ...
    if len(args) < 6:
        print("Error: add_grid_override requires layer_id x1 y1 x2 y2 origin "
              "[slots...]\n"
              f"  Usage: {_ADD_GRID_OVERRIDE_USAGE}")
        return
    layer_id = require_layer_id("add_grid_override", args[0], session,
                                usage=_ADD_GRID_OVERRIDE_USAGE)

    def _coord(what, tok):
        # The override REGION is integer geometry (PatternOverride::region is a
        # Rect), so a fractional coordinate was silently truncated — same defect
        # as add_keepout's, and it moves the seam between two track patterns.
        return require_distance(
            "add_grid_override", what, tok, session, integer=True,
            usage=_ADD_GRID_OVERRIDE_USAGE,
            why="The region bounds a pattern seam, not a track.")
    x1, y1 = _coord("<x1>", args[1]), _coord("<y1>", args[2])
    x2, y2 = _coord("<x2>", args[3]), _coord("<y2>", args[4])
    origin = require_distance("add_grid_override", "<origin>", args[5],
                              session, usage=_ADD_GRID_OVERRIDE_USAGE)
    slots = _parse_slots(session, "add_grid_override", args[6:],
                         _ADD_GRID_OVERRIDE_USAGE)
    if not slots:
        print("Error: add_grid_override requires at least one slot triple")
        return
    pat = buda.TrackPattern(origin=origin, slots=slots)
    if session.routing_grid is None:
        session.routing_grid = buda.RoutingGridStack()
    session.routing_grid.add_override(layer_id, x1, y1, x2, y2, pat)
    session._persist_grid_override(layer_id, x1, y1, x2, y2, pat)
    print(f"[RoutingGrid] Override on layer {layer_id} "
          f"region=({x1},{y1})-({x2},{y2}): "
          f"{len(slots)} slots, unit_pitch={pat.unit_pitch():.3f}")


COMMANDS = {
    "def_track_pattern": cmd_def_track_pattern,
    "report_overhead": cmd_report_overhead,
    "add_grid_override": cmd_add_grid_override,
}
