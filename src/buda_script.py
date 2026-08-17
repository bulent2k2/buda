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
import re

_TOKEN = re.compile(r"\S+")


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


def sole_path_arg(cmd_line, skip=1):
    """The whole rest of the line as ONE path, for a command whose argument
    IS a single path.  Returns "" when the line carries no argument.

    `skip` is how many leading words are the COMMAND rather than the path —
    1 for `source <path>`, 2 for a sub-verb form like `def_gds_layer file
    <path>`, where the rest of the line is a path for the same reason.

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
    `source`, `import_verilog`, `save_bdb`.  Every other path-taking command
    (a path followed by OPTIONS, or SEVERAL adjacent paths) reads its
    arguments through `split_quoted_args` below, where a spaced path is
    spelled with quotes.

    A path spelled with surrounding matched quotes is accepted too, because
    that is the instinctive spelling (and the norm on Windows) and would
    otherwise fail as a near-miss with the quotes inside the filename.
    """
    rest = strip_inline_comment(cmd_line).strip().split(None, skip)
    if len(rest) <= skip:
        return ""
    return unquote(rest[skip].strip())


def unquote(s):
    """Strip ONE matched surrounding pair of quotes, if present."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def split_quoted_args(cmd_line, skip=1):
    """The argument tokens of a command line, with QUOTED runs kept whole.

    The one rule for every path-taking command that `sole_path_arg` cannot
    serve — a path followed by OPTIONS (`open_bdb <path> [writeback]`), and
    SEVERAL adjacent paths (`import_def_lef <def> <lef>`, `require_file
    <path> …`).  Rest-of-line serves neither: it swallows the options into
    the filename, and between two paths there is nothing to swallow toward.

    A spaced path is therefore QUOTED, which is a deliberate refusal to
    guess.  Consider `export_gds out.gds bogus_option 1`: no token is a known
    keyword, so a rest-of-line rule reads the whole thing as a filename, the
    unknown-option error disappears, and a typo silently writes a file with a
    garbage name.  Measured — it broke `test_labels_off_and_cli_options`.
    Nothing in the line distinguishes that from a genuinely spaced path, so
    the ambiguity is real and the quote is how the author resolves it.  Two
    adjacent paths are the same problem with no keyword to appeal to at all.

    A quote is honoured only where a token BEGINS, which is what keeps this
    identical to `.split()` for every line that does not use one — the
    property that leaves every existing flow and every existing diagnostic
    untouched (`test_no_checked_in_flow_parses_differently` measures it over
    the whole tree, rather than asserting it).  An unterminated quote falls
    through to the plain split, so it cannot swallow the rest of the line.

    `skip` is how many leading words are the COMMAND rather than an argument
    — 1 normally, 2 for a sub-verb form.
    """
    rest = strip_inline_comment(cmd_line)
    out, i, n = [], 0, len(rest)
    while i < n:
        if rest[i].isspace():
            i += 1
            continue
        if rest[i] in "\"'":
            end = rest.find(rest[i], i + 1)
            if end > 0:
                out.append(rest[i + 1:end])
                i = end + 1
                continue
        j = i
        while j < n and not rest[j].isspace():
            j += 1
        out.append(rest[i:j])
        i = j
    return out[skip:]


def leading_path_and_options(cmd_line, option_words=None):
    """(path, remaining_tokens) for `<cmd> <path> [<option> …]`.

    `remaining_tokens` IS the old `args[1:]` whenever nothing is quoted, so
    each handler keeps its own option parsing and its own error messages.
    `option_words` is vestigial — the first cut needed a closed option
    vocabulary to find the end of the path, and the quote is what replaced
    it.  Kept so the call sites still read as the shape they are.
    """
    toks = split_quoted_args(cmd_line)
    if not toks:
        return "", []
    return toks[0], toks[1:]
