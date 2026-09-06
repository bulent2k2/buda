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

"""BUDA CLI command registry (the do_command registry split).

Each submodule owns one pipeline stage's command handlers and exports a
COMMANDS dict {command_name: handler(session, cmd, args, cmd_line)}.  This
package assembles them into the single COMMANDS registry buda_cli dispatches
through; KNOWN_COMMANDS is derived from it, so a command exists if and only
if it is dispatchable — the two can no longer drift.
"""
from . import (setup_cmds, bundling_cmds, topologies_cmds, edit_cmds,
               planner_cmds, nuts_cmds, grid_cmds, verify_viz_cmds,
               bdb_cmds, control_cmds, ndr_cmds, pin_def_cmds, block_size_cmds)

MODULES = (setup_cmds, bundling_cmds, topologies_cmds, edit_cmds,
           planner_cmds, nuts_cmds, grid_cmds, verify_viz_cmds,
           bdb_cmds, control_cmds, ndr_cmds, pin_def_cmds, block_size_cmds)

COMMANDS = {}
for _m in MODULES:
    for _name, _handler in _m.COMMANDS.items():
        if _name in COMMANDS:
            raise RuntimeError(f"duplicate command registration: {_name!r} "
                               f"in {_m.__name__}")
        COMMANDS[_name] = _handler
