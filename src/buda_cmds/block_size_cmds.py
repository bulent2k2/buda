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

"""`emit_block_size` — the block-SIZING half of the LibreLane block-side
handoff (docs/internal/librelane_hier_flow.md §5, §8 step 3c).  The rule
itself is `buda_session.block_size`; this is its command surface."""
from buda_session.util import resolve_script_path
from buda_script import leading_path_and_options

from ._options import reject_unknown_options

_OPTS = ("area", "util", "aspect", "margin", "metrics", "inst", "faces")


def cmd_emit_block_size(session, cmd, args, cmd_line):
    # Usage: emit_block_size <file.json> <block-or-cell> [area <um2>]
    #        [util <pct>] [aspect <w/h>] [margin <um>] [metrics <file.json>]
    #        [inst <name>] [faces on|off]
    if len(args) < 2:
        print("Error: emit_block_size requires an output path and a block or "
              "cell name: emit_block_size <file.json> <block-or-cell> "
              "[area <um2>] [util <pct>] [aspect <w/h>] [margin <um>] "
              "[metrics <file.json>] [inst <name>] [faces on|off]")
        return
    # A QUOTED path may contain spaces; unquoted this is the old split.
    path, rest = leading_path_and_options(cmd_line, _OPTS)
    if not rest:
        print("Error: emit_block_size requires a block or cell name after "
              "the output path")
        return
    target, opts = rest[0], rest[1:]
    kw = {"area": None, "util": None, "aspect": None, "margin": 0.0,
          "metrics_path": None, "inst": None, "use_faces": True}
    i = 0
    while i < len(opts):
        k = opts[i].lower()
        if k in ("area", "util", "aspect", "margin") and i + 1 < len(opts):
            try:
                v = float(opts[i + 1])
            except ValueError:
                print(f"Error: emit_block_size {k} must be a number, got "
                      f"'{opts[i + 1]}'")
                return
            kw[k], i = v, i + 2
        elif k == "metrics" and i + 1 < len(opts):
            kw["metrics_path"], i = resolve_script_path(
                session, opts[i + 1], is_read=True), i + 2
        elif k == "faces" and i + 1 < len(opts):
            v = opts[i + 1].lower()
            if v not in ("on", "off"):
                print(f"Error: emit_block_size faces must be on or off, got "
                      f"'{opts[i + 1]}'")
                return
            kw["use_faces"], i = v == "on", i + 2
        elif k == "inst" and i + 1 < len(opts):
            kw["inst"], i = opts[i + 1], i + 2
        else:
            reject_unknown_options("emit_block_size", [k], _OPTS)
            return
    from buda_session.block_size import emit_block_size
    emit_block_size(session, resolve_script_path(session, path), target, **kw)


COMMANDS = {"emit_block_size": cmd_emit_block_size}
