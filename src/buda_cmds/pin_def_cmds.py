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

"""`emit_pin_def` — the block-side handoff of the LibreLane hierarchical
flow (docs/internal/librelane_hier_flow.md §5, §8 step 3b).  The writer
itself is `buda_session.pin_def`; this is its command surface."""
from buda_session.util import resolve_script_path
from buda_script import leading_path_and_options

from ._options import reject_unknown_options

_OPTS = ("unrouted", "depth", "grid", "lef")


def cmd_emit_pin_def(session, cmd, args, cmd_line):
    # Usage: emit_pin_def <file.def> <block-or-cell> [unrouted <N|S|E|W> [<layer>]]
    #                     [depth <um>] [grid <dbu>] [lef <file.lef>]
    if len(args) < 2:
        print("Error: emit_pin_def requires an output path and a block or "
              "cell name: emit_pin_def <file.def> <block-or-cell> "
              "[unrouted <N|S|E|W> [<layer>]] [depth <um>] [grid <dbu>] "
              "[lef <file.lef>]")
        return
    # A QUOTED path may contain spaces; unquoted this is the old split.
    path, rest = leading_path_and_options(cmd_line, _OPTS)
    if not rest:
        print("Error: emit_pin_def requires a block or cell name after the "
              "output path")
        return
    target, opts = rest[0], rest[1:]
    unrouted, unrouted_layer, depth, grid, lef = "S", None, None, None, None
    i = 0
    while i < len(opts):
        kw = opts[i].lower()
        if kw == "unrouted" and i + 1 < len(opts):
            unrouted = opts[i + 1].upper()
            i += 2
            # An optional layer NAME after the edge: `unrouted S met2`.
            if i < len(opts) and opts[i].lower() not in _OPTS:
                unrouted_layer, i = opts[i], i + 1
        elif kw == "depth" and i + 1 < len(opts):
            try:
                depth = float(opts[i + 1])
            except ValueError:
                print(f"Error: emit_pin_def depth must be a number of "
                      f"microns, got '{opts[i + 1]}'")
                return
            i += 2
        elif kw == "lef" and i + 1 < len(opts):
            # The cell's LEF, for its full pin set (a quoted value is ONE
            # token, so this is the same indexing as `depth`).
            lef, i = resolve_script_path(session, opts[i + 1],
                                         is_read=True), i + 2
        elif kw == "grid" and i + 1 < len(opts):
            try:
                grid = int(opts[i + 1])
            except ValueError:
                print(f"Error: emit_pin_def grid must be a whole number of "
                      f"database units, got '{opts[i + 1]}'")
                return
            i += 2
        else:
            reject_unknown_options("emit_pin_def", [kw], _OPTS)
            return
    from buda_session.pin_def import emit_pin_def
    emit_pin_def(session, resolve_script_path(session, path), target,
                 unrouted=unrouted, unrouted_layer=unrouted_layer,
                 depth_um=depth, grid=grid, lef_path=lef)


COMMANDS = {"emit_pin_def": cmd_emit_pin_def}
