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

"""Shared option-validation for the CLI command handlers.

Many commands read a FIXED vocabulary of optional keyword/enum tokens by a
membership test (`"signal_tracks" in args`, `args[0] == "hier"`, …) and used to
SILENTLY IGNORE anything they didn't recognize — so a typo like
`run_planner singnal_tracks` or a stray `generate_topologies foo` ran with the
feature the user thought they enabled quietly off.  `reject_unknown_options`
turns that into a hard error that stops the flow, exactly like `do_command`'s
unknown-COMMAND guard.  Validate BEFORE any state/prerequisite check so a
malformed command is rejected regardless of session state.
"""
import difflib
import math
import sys


def reject_unknown_options(cmd, tokens, valid, *, also_accepted=()):
    """Stop the flow (non-zero exit) if any of `tokens` is not a known option.

    `cmd`           the command name, for the message.
    `tokens`        the argument tokens that are SUPPOSED to be options — the
                    caller removes required positionals / numeric values / free-
                    form names (block/net/file/…) first, so only genuine option
                    keywords remain.
    `valid`         the user-facing option vocabulary (ordered; printed verbatim).
    `also_accepted` tokens honored but not advertised (legacy no-ops); accepted
                    silently, kept out of the printed list and the suggestions.

    On an unknown token it prints the offending token(s), the valid options, and
    a close-match "Did you mean …?" hint, then `sys.exit(1)`.  `run_command`
    re-raises the SystemExit, so the script halts (matching the unknown-command
    behavior).  No-op when every token is recognized.
    """
    allowed = set(valid) | set(also_accepted)
    unknown = [t for t in tokens if t not in allowed]
    if not unknown:
        return
    hint = ""
    for u in unknown:
        m = difflib.get_close_matches(u, list(valid), n=1)
        if m:
            hint = f" Did you mean '{m[0]}'?"
            break
    plural = "s" if len(unknown) > 1 else ""
    valid_str = " ".join(valid) if valid else "(none)"
    print(f"Error: {cmd}: unknown option{plural} "
          f"{', '.join(repr(u) for u in unknown)}. "
          f"Valid options: {valid_str}.{hint}")
    sys.exit(1)


def looks_numeric(tok):
    """True if `tok` parses as an int or float — i.e. a numeric VALUE argument
    (an iteration count, threshold, pitch), not an option keyword.  Used to
    exclude numeric args before option validation."""
    try:
        float(tok)
        return True
    except (TypeError, ValueError):
        return False


def _usage_line(usage):
    return f"\n  Usage: {usage}" if usage else ""


def require_layer_id(cmd, tok, session=None, *, usage=""):
    """Parse a layer-ID positional, or stop the flow with an actionable error.

    These commands take the layer *id* — the number in
    `def_layer <id> <name> …` — but the *name* ('M4') is what the rest of a
    script reads back, so reaching for the name here is the single most common
    beginner slip.  A bare `int(tok)` answered it with a raw
    `ValueError: invalid literal for int() with base 10: 'M4'` traceback, which
    names neither the command, nor the argument, nor the fix.

    Resolve the token through the session's `def_layer` name map so the message
    can name the exact id to type instead ("use layer id 4, not the name 'M4'"),
    and list the declared layers when the name isn't one of them.  Returns the
    int id.
    """
    try:
        return int(tok)
    except (TypeError, ValueError):
        pass
    name_map = dict(getattr(session, "_layer_name_map", {}) or {})
    lid = name_map.get(tok)
    if lid is None:                       # tolerate a case slip too ('m4')
        lower = {n.lower(): i for n, i in name_map.items()}
        lid = lower.get(str(tok).lower())
    if lid is not None:
        # Front-load the correction: the runtime summary truncates a headline
        # to the terminal column, so "use layer id 4" has to come before the
        # explanation or the beginner never sees the fix without the log.
        print(f"Error: {cmd}: use layer id {lid}, not the name '{tok}' "
              f"(this command takes the numeric id from "
              f"def_layer <id> <name> …)."
              + _usage_line(usage))
        sys.exit(1)
    known = ", ".join(f"{n}={i}" for n, i in sorted(name_map.items(),
                                                    key=lambda kv: kv[1]))
    known_str = (f"  Declared layers (name=id): {known}." if known
                 else "  No layers are declared yet — declare one with "
                      "def_layer <id> <name> <H|V> … first.")
    print(f"Error: {cmd}: expected a numeric layer id, got '{tok}'.\n"
          + known_str + _usage_line(usage))
    sys.exit(1)


def require_int(cmd, what, tok, *, usage="", why=""):
    """Parse an INTEGER positional, rejecting a fractional value outright.

    For the coordinates of block / keepout / override geometry, which is an
    INTEGER grid all the way down (`Rect` in topology.h is `int x1,y1,x2,y2`,
    and the Hanan grid is built from those edges).  The two ways this was
    handled before were both wrong in opposite directions:

      * `int(tok)` (add_block) raised a bare `ValueError: invalid literal for
        int()` traceback naming neither the command nor which of four
        positionals was bad;
      * `int(float(tok))` (add_keepout, add_grid_override) SILENTLY TRUNCATED,
        so `add_keepout 0 0 10.9 10.9` blocked (0,0)-(10,10) — a keepout ~1
        unit smaller than written on both axes, i.e. routing that looks legal
        over ground the user meant to block.  Silently shrinking a blockage is
        the worst direction to be wrong in.

    A value that is integral in a non-integer spelling (`100.0`, `1e2`) is
    ACCEPTED — it names an exact integer, so nothing is lost by taking it.
    Only a genuinely fractional (or non-finite) value is refused, because that
    is the only case where continuing would change the number.  `why` adds one
    clause of command-specific context to that message.
    """
    try:
        val = float(tok)
    except (TypeError, ValueError):
        print(f"Error: {cmd}: expected an integer for {what}, got '{tok}'."
              + _usage_line(usage))
        sys.exit(1)
    if not math.isfinite(val) or val != int(val):
        why_str = f" {why}" if why else ""
        print(f"Error: {cmd}: {what} must be a whole number, got '{tok}' — "
              f"coordinates are an integer grid.{why_str} Scale the design "
              f"(e.g. state it in nm rather than um) instead of rounding here."
              + _usage_line(usage))
        sys.exit(1)
    return int(val)


def require_distance(cmd, what, tok, session, *, integer=False, usage="",
                     why=""):
    """Parse a script-declared DISTANCE, accepting an optional `um` suffix.

    Resolves opens_interchange.md item 6's ergonomic half.  A bare number is
    a distance in LAYOUT UNITS, exactly as before — the engine is
    unit-agnostic and `set_import_scale` deliberately does not rescale script
    distances (that is what keeps the ~59 downstream int(round()) sites
    correct by construction; see engine_units.md).  The trap that leaves is
    that changing the import scale silently changes what every hand-typed
    number MEANS.  A `um` suffix writes the intent down instead:

        corner_margin dx 2um        # 2 microns, at ANY import scale

    converts through the declared scale (`lu_per_um`, i.e. BDB
    import_scale; 1.0 with no BDB open — the nominal-micron default) at
    PARSE time.  Declare `set_import_scale` before the first suffixed
    distance; using one earlier is reported when the scale later changes
    (the session records `_um_suffix_used`).

    `integer=True` demands the CONVERTED value land on the integer layout
    grid (0.5um at scale 2000 is 1000 — fine; at scale 1.0 it is 0.5 and
    refused, naming the scale), within 1e-6 for float-dust from the
    multiply.  No suffix + integer delegates to `require_int` untouched.
    """
    s = str(tok)
    if s.lower().endswith("um") and s.lower() != "um":
        num = s[:-2]
        try:
            val = float(num)
        except (TypeError, ValueError):
            print(f"Error: {cmd}: expected a number before 'um' for {what}, "
                  f"got '{tok}'." + _usage_line(usage))
            sys.exit(1)
        scale = 1.0
        if getattr(session, "bdb", None) is not None:
            # `set_import_scale dbu` DEFERS the factor until import_def_lef
            # reads the DEF's UNITS — in that window import_scale() still
            # returns the stale value, so converting here would silently
            # mean the wrong thing (Codex P1 on #666).  Refuse, naming the
            # two ways out.
            if session.bdb.import_scale_pending():
                print(f"Error: {cmd}: '{tok}' — the import scale is `dbu`, "
                      f"which resolves from the DEF's UNITS at import_def_lef; "
                      f"a `um` distance cannot convert before then.  Move "
                      f"this line after the import, or declare a numeric "
                      f"scale (set_import_scale <lu_per_um>).")
                sys.exit(1)
            scale = float(session.bdb.import_scale()) or 1.0
        session._um_suffix_used = True
        lu = val * scale
        if not integer:
            return lu
        snap = round(lu)
        if not math.isfinite(lu) or abs(lu - snap) > 1e-6:
            print(f"Error: {cmd}: {what} = {num}um is {lu:g} layout units at "
                  f"import scale {scale:g}, not a whole number — coordinates "
                  f"are an integer grid.{' ' + why if why else ''}"
                  + _usage_line(usage))
            sys.exit(1)
        return int(snap)
    if integer:
        return require_int(cmd, what, tok, usage=usage, why=why)
    return require_number(cmd, what, tok, usage=usage)


def require_number(cmd, what, tok, *, integer=False, usage=""):
    """Parse a numeric positional, or stop the flow naming the ARGUMENT.

    Same motivation as `require_layer_id`: a mistyped coordinate used to raise a
    bare `ValueError` traceback that never said which of a long positional line
    ('<x1> <y1> <x2> <y2> <origin> …') was wrong.  `what` is the argument's name
    in the usage string, e.g. "<x1>".
    """
    try:
        return int(float(tok)) if integer else float(tok)
    except (TypeError, ValueError):
        print(f"Error: {cmd}: expected a number for {what}, got '{tok}'."
              + _usage_line(usage))
        sys.exit(1)


def parse_rect_list(cmd, args, start, coord, usage=""):
    """Parse a `rect <x1> <y1> <x2> <y2> [rect ...]` run out of `args[start:]`.

    Returns `(rects, next_index)` — the rect quads in declaration order and the
    index of the first token past the run.  `coord(what, tok)` converts one
    coordinate, which is the ONLY thing that differs between the two callers:
    `add_block` lands in the integer Floorplan (its coordinates become Hanan
    lines) while `set_cell_rects` writes REAL micron values to the BDB like
    every other cell dimension.  A truncated rect stops the flow naming the
    missing argument, instead of the bare IndexError it raised before the two
    parsers were made one.
    """
    rects, i = [], start
    while i < len(args) and args[i].lower() == "rect":
        n = len(rects) + 1
        have = len(args) - (i + 1)          # coords present after this 'rect'
        if have < 4:
            missing = ("x1", "y1", "x2", "y2")[have]
            print(f"Error: {cmd}: rect {n} is missing <{missing}> — "
                  f"each rect takes exactly 4 coordinates "
                  f"(<x1> <y1> <x2> <y2>)." + _usage_line(usage))
            sys.exit(1)
        rects.append(tuple(
            coord(f"<{ax}> of rect {n}", args[i + k])
            for k, ax in enumerate(("x1", "y1", "x2", "y2"), start=1)))
        i += 5
    return rects, i


def validate_rect_list(cmd, subject, rects, usage=""):
    """Refuse the meaningless rect shapes, shared by every rect declaration.

    Degenerate (zero-extent) and DUPLICATE rects are hard errors; overlapping
    non-identical rects stay legal, because interior overlap is exactly how a
    rectilinear L/C-shape is drawn and edge-adjacent rects draw one footprint
    by abutment.  `subject` names what is being declared, e.g. "block 'L'" or
    "cell 'dsp'".
    """
    seen = {}
    for n, (rx1, ry1, rx2, ry2) in enumerate(rects, start=1):
        if rx1 == rx2 or ry1 == ry2:
            axis = "width (x1 == x2)" if rx1 == rx2 else "height (y1 == y2)"
            print(f"Error: {cmd}: rect {n} of {subject} is degenerate — "
                  f"zero {axis}. A rect must have positive extent on both "
                  f"axes; its edges become block faces and Hanan lines."
                  + _usage_line(usage))
            sys.exit(1)
        key = (min(rx1, rx2), min(ry1, ry2), max(rx1, rx2), max(ry1, ry2))
        if key in seen:
            print(f"Error: {cmd}: rect {n} of {subject} duplicates rect "
                  f"{seen[key]} ({key[0]},{key[1]})-({key[2]},{key[3]}) — a "
                  f"repeated rect adds no geometry and doubles its "
                  f"stubs/taps; remove one." + _usage_line(usage))
            sys.exit(1)
        seen[key] = n
