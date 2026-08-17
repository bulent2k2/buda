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

"""How a `.buda` LINE is read — the rules the engine and every tool share.

The engine is not the only thing that parses `.buda`.  `tools/buda2tcl.py`
translates a flow, `tools/buda2bdb.py` ingests one as a cell,
`tools/qor_corpus.py` walks one for feature coverage, `tools/scan_fanin.py`
scans one for net shapes — and each follows `source` into the next file.  A
rule that lives only in the engine is therefore a rule the tools will
DISAGREE with, and the disagreement is silent: a tool resolves a different
file, or none, and reports on a script nobody wrote.

Standalone and dependency-free for the reason `slot_groups.py`,
`bus_names.py` and `tcl_quote.py` are: these are TEXT rules, and a tool that
only reads a script must not have to import the compiled extension to know
what the script says.  `buda_session.util` (and through it `buda_cli`)
re-exports them, so a command handler reaches the same code without a cycle
— `buda_session` imports the engine, which is exactly what a tool cannot.
"""


def strip_inline_comment(line):
    """Strip a `#` comment from a script line: everything from the first `#`
    that begins a token (start of line, or preceded by whitespace) to the end
    of the line is removed. This lets a command be commented out partially —
    `run_bundler # strict` runs `run_bundler`, `def_layer … 0.0 # note` drops
    the note. A `#` embedded in a token (no preceding whitespace, e.g. a path
    fragment) is left intact so it can't silently swallow real arguments."""
    for i, ch in enumerate(line):
        if ch == '#' and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def sole_path_arg(cmd_line):
    """The whole rest of the line as ONE path, for a command whose argument
    IS a single path.  Returns "" when the line carries no argument.

    The engine splits a command line on whitespace, so `args[0]` is a path
    only up to its first space: `source my dir/flow.buda` looked for `my`, and
    the error named a file the author never wrote.  It bit the FLOW's own path
    hardest, since that is the one path a user does not choose — `bin/buda
    "my dir/x.buda"` reaches the engine as `source my dir/x.buda`, and
    `btcl -i` sends the same line — so a checkout under `~/My Designs/` could
    not be run at all.

    Deliberately NOT quoting in the tokenizer.  `.buda` is whitespace-split
    with no quoting anywhere, the Tcl bridge documents that it does not
    re-quote (a command must mean the same thing from Tcl as from a script),
    and teaching the splitter about quotes would change how EVERY command
    reads its arguments to fix a handful that take a path.  A command that
    takes exactly one path has no ambiguity to resolve: everything after the
    verb is the path, which is what `include` means in most languages.

    So this is applicable ONLY where the argument list is exactly one path —
    `source`.  It is NOT usable for `open_bdb <path> [writeback]`,
    `import_def_lef <def> <lef> …` or any other command with a second token,
    where the rest of the line is genuinely ambiguous and the trailing
    argument would be swallowed into the filename.

    A path spelled with surrounding matched quotes is accepted too, because
    that is the instinctive spelling (and the norm on Windows) and would
    otherwise fail as a near-miss with the quotes inside the filename.
    """
    rest = strip_inline_comment(cmd_line).strip().split(None, 1)
    if len(rest) < 2:
        return ""
    path = rest[1].strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in "\"'":
        path = path[1:-1]
    return path
