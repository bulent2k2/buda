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

"""Script control: source, exit.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import os
import sys


def cmd_source(session, cmd, args, cmd_line):
    if not args:
        msg = "Error: source command requires a file path"
        print(msg); session._log_write(msg)
        return

    raw_path = args[0]
    if not raw_path.endswith('.buda') and not os.path.exists(raw_path):
        raw_path += '.buda'

    # Resolve path relative to the current executing script (if any)
    if session._script_stack:
        parent_dir = os.path.dirname(session._script_stack[-1])
        full_path = os.path.normpath(os.path.join(parent_dir, raw_path))
    else:
        full_path = os.path.abspath(raw_path)

    if not os.path.exists(full_path):
        # Fail fast (like an unknown command): a missing/typo'd source
        # silently continuing would leave the design misconfigured — e.g.
        # no def_layers loaded, so run_planner falls back to its M4/M5
        # default and routes on the wrong metal with no obvious cause.
        where = (f" in {os.path.basename(session._script_stack[-1])}"
                 if session._script_stack else "")
        msg = (f"Error: sourced file not found{where}: {full_path} "
               f"('{cmd_line.strip()}').")
        print(msg); session._log_write(msg)
        sys.exit(1)

    if session.script_path is None:
        session.script_path = full_path

    # --log archive: snapshot the script as it is about to run (top-level and
    # every nested source alike — the tweak under exploration often lives in a
    # sourced file, not the entry script).
    session._archive_script(full_path)

    session._script_stack.append(full_path)
    # Track whether this source frame sits at its parent's tail; a command is the
    # flow's LAST only when every ancestor source was at its tail AND it is the
    # last real command here. `_print_end_report` uses this to decide whether a
    # `visualize` may emit the runtime summary before its window blocks.
    session._at_tail_stack.append(session._at_last_command)
    try:
        with open(full_path, 'r') as f:
            lines = f.readlines()
        def _is_cmd(l):
            s = l.strip()
            return bool(s) and not s.startswith('#')
        last_i = max((i for i, l in enumerate(lines) if _is_cmd(l)), default=-1)
        parent_tail = all(session._at_tail_stack)
        for i, line in enumerate(lines):
            if not line.strip().startswith('#'):
                # run_command times + log-routes each command when a flow log is
                # active; it falls back to do_command (raw, unlogged) for
                # interactive/embedded callers.
                session._at_last_command = parent_tail and (i == last_i)
                session.run_command(line)
    finally:
        session._at_tail_stack.pop()
        session._script_stack.pop()


def cmd_exit(session, cmd, args, cmd_line):
    # Stop the run mid-script (handy for debugging a flow incrementally).
    # Optional integer exit code (default 0 = clean stop).
    code = 0
    if args:
        try:
            code = int(args[0])
        except ValueError:
            print(f"Error: exit code must be an integer, got '{args[0]}'")
            code = 1
    session._flush_bdb_writeback()  # persist an armed fixture before stopping
    where = (f" in {os.path.basename(session._script_stack[-1])}"
             if session._script_stack else "")
    print(f"Exiting on 'exit' command{where} (code {code}).")
    sys.exit(code)


COMMANDS = {
    "source": cmd_source,
    "exit": cmd_exit,
}
