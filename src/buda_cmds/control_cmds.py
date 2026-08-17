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

"""Script control: source, require_file, exit.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import os
import sys

import buda_diag
from buda_session.util import (resolve_script_path, sole_path_arg,
                               split_quoted_args)


def cmd_source(session, cmd, args, cmd_line):
    # The whole rest of the line, not `args[0]`: `source` takes exactly one
    # path, so a space in it is part of the filename rather than an argument
    # separator (see `sole_path_arg` for why this is not tokenizer quoting).
    # Identical for every path without spaces — `args[0]` and the rest of the
    # line are the same string there — which is what keeps every existing
    # flow byte-for-byte unchanged.
    raw_path = sole_path_arg(cmd_line)
    if not raw_path:
        msg = "Error: source command requires a file path"
        print(msg); session._log_write(msg)
        return

    # Resolve relative to the current executing script's dir (else CWD), and
    # make the '.buda'-suffix decision against the RESOLVED candidates — not
    # os.path.exists(raw_path) in the process CWD (audit P5-04): a nested
    # `source foo` broke whenever an unrelated file/dir named `foo` happened
    # to sit in the invocation CWD, even though `foo.buda` lives beside the
    # parent script. os.path.isfile also rejects a same-named directory.
    base_dir = (os.path.dirname(session._script_stack[-1])
                if session._script_stack else os.getcwd())

    def _resolve(rel):
        return os.path.normpath(os.path.join(base_dir, rel))

    full_path = _resolve(raw_path)
    if not os.path.isfile(full_path) and not raw_path.endswith('.buda'):
        cand = _resolve(raw_path + '.buda')
        if os.path.isfile(cand):
            full_path = cand

    if not os.path.isfile(full_path):
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


def cmd_require_file(session, cmd, args, cmd_line):
    # require_file <path> [<path> ...] [hint <text ...>]
    #
    # State a flow's INPUT PRECONDITION, with the remedy, at the top of the
    # script instead of letting the reader that needs the file fail six
    # commands later.  The reader's own complaint is about the file it could
    # not open; only the flow knows where the file COMES FROM — a fetch
    # script, a prior stage's output, a site path — and that is the half a
    # user needs.  `flow/ariane133` is the case in point: two of its inputs
    # are fetched from upstream by `fetch.py` and are deliberately not
    # checked in, so a fresh clone runs three commands and stops.
    #
    # Fails FAST, like `source` and an unknown command, and for the same
    # reason: continuing past a missing input leaves the design
    # misconfigured, and the run that follows describes a design nobody has.
    # A MALFORMED declaration stops the run too (Codex P2 on #743).  The
    # tempting reading is that a usage error is the author's problem and the
    # flow may carry on — but this command's whole job is to have CHECKED
    # something, and a declaration that named nothing checked nothing.  So
    # continuing produces the exact shape this command exists to remove: a
    # run that verified no precondition, reached the end, and exited 0.
    def _fail(msg):
        print(msg); session._log_write(msg)
        session._flush_bdb_writeback()
        sys.exit(1)

    # A path may contain spaces when QUOTED (buda_script.split_quoted_args).
    # This command takes a LIST of paths, so there is no rest-of-line rule to
    # be had: between two paths nothing but a quote can say where one ends.
    # Unquoted, `argv` is exactly the `args` the dispatcher split.
    argv = split_quoted_args(cmd_line)
    if not argv:
        _fail("Error: require_file requires at least one path "
              "(require_file <path> [<path> ...] [hint <text>])")

    # `hint` ends the path list, so several files can share one remedy.  The
    # hint is the trailing WORDS joined — which is what it always was (the
    # ariane133 flows' double-spaced hint already came out single-spaced), and
    # it is also what makes the Tcl spelling work: `buda::_join_args` re-quotes
    # a whitespace-bearing argument, and the tokenizer hands the quoted run
    # back whole instead of leaving the quotes in the message.
    paths, hint = argv, ""
    if "hint" in argv:
        cut = argv.index("hint")
        paths = argv[:cut]
        hint = " ".join(argv[cut + 1:]).strip()
    if not paths:
        _fail("Error: require_file: no path before 'hint' "
              "(require_file <path> [<path> ...] [hint <text>])")

    # Resolved by THE path rule (the script's own directory), so a required
    # path means the same file as the import command that will read it.
    #
    # `isfile`, not `exists` (Codex P2 on #743): a DIRECTORY of the required
    # name satisfies `exists` and the precondition passes silently, leaving
    # the reader to fail on it later without the hint — which is the whole
    # failure this command removes, reintroduced by a laxer test.  `source`
    # uses isfile for the same reason.  The two cases are reported
    # separately because they have different remedies: one file is absent,
    # the other is present and is not a file.
    checked = [(p, resolve_script_path(session, p, is_read=True))
               for p in paths]
    absent = [(p, full) for p, full in checked if not os.path.exists(full)]
    notfile = [(p, full) for p, full in checked
               if os.path.exists(full) and not os.path.isfile(full)]
    if not absent and not notfile:
        return          # silent: a satisfied precondition is not news

    # Every bad path at once.  Reporting one per run turns fetching two
    # inputs into two failed runs.
    where = (f" ({os.path.basename(session._script_stack[-1])})"
             if session._script_stack else "")
    n = len(absent) + len(notfile)
    what = "not found" if not notfile else "not usable"
    lines = [buda_diag.format(
        "BUDA-1905",
        f"{n} required input file(s) {what}{where}:")]
    for p, full in absent:
        lines.append(f"    {p}" + (f"   → {full}" if full != p else "")
                     + ("   (not found)" if notfile else ""))
    for p, full in notfile:
        kind = "a directory" if os.path.isdir(full) else "not a regular file"
        lines.append(f"    {p}" + (f"   → {full}" if full != p else "")
                     + f"   ({kind})")
    if hint:
        lines.append(f"  {hint}")
    for ln in lines:
        print(ln); session._log_write(ln)
    session._flush_bdb_writeback()
    sys.exit(1)


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
    "require_file": cmd_require_file,
    "exit": cmd_exit,
}
