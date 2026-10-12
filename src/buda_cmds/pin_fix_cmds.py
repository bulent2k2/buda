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

"""Fixed-pin commands — fix_pin / dump_pin_fixes (docs/internal/fixed_pin_design.md).

A block's pins are not always free to land anywhere on its boundary: a hard
macro's pins sit where its layout put them.  `fix_pin` states which landings a
block still admits — faces, one declared rect, a window along the face — and
the generator, NUTS, DetailedNUTS and the audit all read that as the block's
LANDING geometry (`Floorplan.landing_rects`).  What the block BLOCKS
(`obstruction_rects`) never changes: a fix narrows where a wire may attach, it
does not make the macro's body routable.

This is the first cut: a fix governs a BLOCK (every pin of it), in the
placed (die) frame.  Per-pin selectors, cell-level fixes with the orientation
transform, layers, bit order and persistence are the next changes.
"""
import re

# BUDA-1922 (no candidate lands on a fixed pin) is emitted by the topology
# generator itself, where the pool is judged; this module declares the fix.

_FACE_BITS = {"north": 1, "south": 2, "east": 4, "west": 8}
_FACE_ALIAS = {"n": "north", "s": "south", "e": "east", "w": "west",
               "h": "h", "v": "v"}
_FACE_NAMES = ((1, "north"), (2, "south"), (4, "east"), (8, "west"))


def _faces_text(mask):
    if not mask:
        return "all"
    return ",".join(n for b, n in _FACE_NAMES if mask & b)


def _rect_count(fp, block):
    return max(1, len(fp.get_block_rects(block)))


def _declared(fix):
    """The fix as the user typed it (what dump_pin_fixes prints)."""
    parts = [_faces_text(fix["faces"])]
    if fix["rect"] >= 0:
        parts.append(f"rect {fix['rect'] + 1}")
    if fix["has_window"]:
        parts.append(f"at {fix['lo']}..{fix['hi']}")
    return " ".join(parts)


def _apply(session, block, fix):
    session.fp.set_pin_fix(block, fix["faces"], fix["rect"], fix["has_window"],
                           fix["lo"], fix["hi"])


def cmd_fix_pin(session, cmd, args, cmd_line):
    # fix_pin <block>[.*] [north|south|east|west|H|V ...] [rect <n>] [at <lo>..<hi>]
    # fix_pin <block> off
    if not args:
        print("Error: usage: fix_pin <block> [north|south|east|west|H|V ...] "
              "[rect <n>] [at <lo>..<hi>]   |   fix_pin <block> off")
        return
    target = args[0]
    block, _, pin = target.partition(".")
    if pin not in ("", "*"):
        print(f"Error: fix_pin '{target}': a per-pin selector is not supported "
              f"yet — a fix governs the whole block; name it as '{block}' or "
              f"'{block}.*'")
        return
    fixes = session._pin_fixes
    if len(args) == 2 and args[1].lower() == "off":
        if block in fixes:
            del fixes[block]
            session.fp.clear_pin_fix(block)
            print(f"[PinFix] {block}: fix removed — every face admits landings again")
        else:
            print(f"[PinFix] {block}: no fix declared")
        return

    faces, rect, window = 0, -1, None
    i = 1
    while i < len(args):
        tok = args[i].lower()
        tok = _FACE_ALIAS.get(tok, tok)
        if tok in _FACE_BITS:
            faces |= _FACE_BITS[tok]
        elif tok == "h":
            faces |= _FACE_BITS["east"] | _FACE_BITS["west"]
        elif tok == "v":
            faces |= _FACE_BITS["north"] | _FACE_BITS["south"]
        elif tok == "rect":
            if i + 1 >= len(args) or not args[i + 1].isdigit() or int(args[i + 1]) < 1:
                print("Error: fix_pin: 'rect' takes a 1-based rect number"); return
            rect = int(args[i + 1]) - 1
            i += 1
        elif tok == "at":
            m = (re.fullmatch(r"(-?\d+)\.\.(-?\d+)", args[i + 1]) if i + 1 < len(args) else None)
            if not m:
                print("Error: fix_pin: 'at' takes a window lo..hi along the face "
                      "(block-local coordinates)"); return
            window = (int(m.group(1)), int(m.group(2)))
            if window[0] >= window[1]:
                print(f"Error: fix_pin: window {window[0]}..{window[1]} is empty"); return
            i += 1
        elif tok in ("layer", "order"):
            print(f"Error: fix_pin: '{tok}' is not supported yet"); return
        else:
            print(f"Error: fix_pin: unknown token '{args[i]}'"); return
        i += 1

    fp = session.fp
    known = fp.has_block(block)
    if known and rect >= _rect_count(fp, block):
        print(f"Error: fix_pin {block}: rect {rect + 1} out of range "
              f"(block has {_rect_count(fp, block)} rect(s))"); return
    fix = {"faces": faces, "rect": rect, "has_window": window is not None,
           "lo": window[0] if window else 0, "hi": window[1] if window else 0}
    prev = fixes.get(block)
    fixes[block] = fix
    _apply(session, block, fix)
    if known and not fp.pin_fix_strips(block):
        # Nothing admissible: a fix that admits no landing at all strands the
        # bus, which is never what was meant.  Refuse and put the old one back.
        if prev is None:
            del fixes[block]
            fp.clear_pin_fix(block)
        else:
            fixes[block] = prev
            _apply(session, block, prev)
        print(f"Error: fix_pin {block}: '{_declared(fix)}' admits no landing "
              f"on this block (a face interior to its rects, or a window "
              f"outside the face); the previous setting is kept")
        return
    n = len(fp.pin_fix_strips(block)) if known else 0
    print(f"[PinFix] {block}: {_declared(fix)}"
          + (f" -> {n} landing strip(s)" if known else " (block not declared yet)"))


def revalidate(session, block, fp=None):
    """A fix declared BEFORE its block exists could not be checked then.  Check it
    when the block arrives — on the session floorplan (`add_block`) or on a
    derived hier frame as each BDB component is projected: a rect out of range,
    or a fix that admits no landing, is refused now (and dropped everywhere —
    there is no previous setting to keep), not discovered later as a stranded
    bus."""
    fixes = session._pin_fixes
    fix = fixes.get(block)
    fp = session.fp if fp is None else fp
    if fix is None or not fp.has_block(block):
        return
    problem = None
    if fix["rect"] >= _rect_count(fp, block):
        problem = (f"rect {fix['rect'] + 1} out of range (block has "
                   f"{_rect_count(fp, block)} rect(s))")
    elif not fp.pin_fix_strips(block):
        problem = (f"'{_declared(fix)}' admits no landing on this block (a face "
                   f"interior to its rects, or a window outside the face)")
    if problem:
        del fixes[block]
        fp.clear_pin_fix(block)
        if fp is not session.fp:
            session.fp.clear_pin_fix(block)
        print(f"Error: fix_pin {block}: {problem}; the fix, declared before the "
              f"block, is dropped")


def cmd_dump_pin_fixes(session, cmd, args, cmd_line):
    fixes = session._pin_fixes
    if not fixes:
        print("[PinFix] no fixed pins declared")
        return
    print(f"[PinFix] {len(fixes)} fixed block(s):")
    for block in sorted(fixes):
        fix = fixes[block]
        print(f"  fix_pin {block} {_declared(fix)}")
        if session.fp.has_block(block):
            for (x1, y1, x2, y2, face) in session.fp.pin_fix_strips(block):
                name = {1: "north", 2: "south", 4: "east", 8: "west"}.get(face, "?")
                print(f"    {name:5s} strip [{x1},{y1} .. {x2},{y2}]")


COMMANDS = {
    "fix_pin": cmd_fix_pin,
    "dump_pin_fixes": cmd_dump_pin_fixes,
}
