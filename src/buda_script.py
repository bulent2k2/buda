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
    `source`, `import_verilog`, `save_bdb`.  A command that also takes
    OPTIONS (`open_bdb <path> [writeback]`) uses `leading_path_and_options`
    below, which needs a closed option vocabulary to find the boundary; one
    that takes TWO paths (`import_def_lef <def> <lef>`, `require_file <path>
    …`) has no rule at all — the split between them is genuinely ambiguous
    without quoting, so those still take one token each.

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


def leading_path_and_options(cmd_line, option_words):
    """(path, remaining_tokens) for `<cmd> <path> [<option> …]`.

    The commands that take a path AND a trailing option list — `open_bdb
    <path> [writeback]`, `import_lef_tech <path> [top <N>]`, `export_gds
    <path> [outline <l>] …`.  `sole_path_arg` cannot serve them: rest-of-line
    would swallow the options into the filename.

    A spaced path here must be QUOTED, and that is a deliberate refusal to
    guess.  Consider `export_gds out.gds bogus_option 1`: no token is a known
    keyword, so a rest-of-line rule reads the whole thing as a filename, the
    unknown-option error disappears, and a typo silently writes a file with a
    garbage name.  Measured — it broke `test_labels_off_and_cli_options`.
    Nothing in the line distinguishes that from a genuinely spaced path, so
    the ambiguity is real and the quote is how the author resolves it.

    Unquoted, the split is EXACTLY what it always was — first token is the
    path, the rest are options — so every existing flow and every existing
    diagnostic is untouched.  Quoted, the path runs to the matching quote and
    the options follow it.

    `source`, `import_verilog` and `save_bdb` need no quotes because they take
    no options: there the rest of the line is unambiguously the path, which is
    what `sole_path_arg` implements.
    """
    rest = strip_inline_comment(cmd_line).strip()
    parts = rest.split(None, 1)
    if len(parts) < 2:
        return "", []
    tail = parts[1].strip()
    if tail[:1] in ("'", '"'):
        end = tail.find(tail[0], 1)
        if end > 0:                    # unterminated quote falls through
            return tail[1:end], tail[end + 1:].split()
    toks = tail.split()
    return toks[0], toks[1:]


def option_value(tokens, i, _option_words=None):
    """(value, next_index) for an option whose value may be a QUOTED path.

    `emit_guides out.json tcl "my scripts/g.tcl"` — the tokens arrive already
    split, so a quoted value is reassembled here by consuming to the token
    that closes the quote.  Unquoted, it is the single token it always was.
    Only the commands whose option values are paths need this; a numeric
    option (`margin 5`, `top 3`) is unaffected either way.
    """
    j = i + 1
    if j >= len(tokens):
        return "", j
    first = tokens[j]
    if first[:1] in ("'", '"'):
        q = first[0]
        if len(first) > 1 and first.endswith(q):
            return first[1:-1], j + 1
        k = j + 1
        while k < len(tokens) and not tokens[k].endswith(q):
            k += 1
        if k < len(tokens):
            return " ".join([first[1:]] + tokens[j + 1:k + 1])[:-1], k + 1
    return first, j + 1
