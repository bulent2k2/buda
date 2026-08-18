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

The engine is not the only thing that parses `.buda`.  FIVE other readers
do, and this is the list to check before adding a sixth:

  * `tools/buda2tcl.py`      translates a flow to Tcl
  * `tools/buda2bdb.py`      ingests one as a BDB cell
  * `tools/qor_corpus.py`    walks one for feature coverage
  * `tools/scan_fanin.py`    scans one for net shapes
  * `tools/buda_interact.tcl`  reads the RECORDED lines of a `btcl -i`
                             session — and is written in **Tcl**

Each follows `source` into the next file.  A rule that lives only in the
engine is therefore a rule the readers will DISAGREE with, and the
disagreement is silent: a reader resolves a different file, or none, and
reports on a script nobody wrote.

The four Python readers import this module, so a fix here reaches them.  The
Tcl one CANNOT, and that is what made it the last to be fixed: the quoting
rule (#771/#772/#775) landed here and looked complete, while
`buda_interact.tcl` went on splitting a recorded `open_bdb "my designs/x.bdb"`
on whitespace — which cost it the `.trace` beside every spaced checkpoint,
so each later `btcl -i <flow> <ckpt> <stage>` refused and told the user to
run the build session that had just run (#783).  A reader in another
language needs a TWIN whose agreement is measured (`_split_args` there, and
`test_btcl_quoted_paths.py` runs the same cases through both), because
sharing the code is not available to it.

Standalone and dependency-free for the reason `slot_groups.py`,
`bus_names.py` and `tcl_quote.py` are: these are TEXT rules, and a tool that
only reads a script must not have to import the compiled extension to know
what the script says.  It imports nothing, so a command HANDLER imports it
directly with no risk of a cycle — `buda_session.util` re-exports two names
only because `buda_cli` has always exposed them under its own.
"""


def _scan(line):
    """(tokens, cut) — ONE walk of a `.buda` line, serving both rules.

    `tokens` are the whitespace-separated arguments with a QUOTED run kept
    whole and unwrapped; `cut` is where an inline comment begins (len(line)
    when there is none).  Both callers below are views on this walk, so the
    tokenizer and the comment stripper cannot come to disagree about where a
    quoted run is — and they MUST agree: the dispatcher strips the comment to
    derive `args` and to write the `BUDA_RECORD` line, while the handler
    re-parses the raw `cmd_line`, so a divergence would record a path the
    handler did not read.

    A quote is honoured only where a token BEGINS.  That is what makes an
    apostrophe inside a word (`a's_block`) an ordinary character rather than
    the start of a run that swallows the rest of the line, and it is the same
    invariant that keeps the tokenizer identical to `.split()`.  An
    unterminated quote is likewise just a character.
    """
    out, i, n, at_start = [], 0, len(line), True
    while i < n:
        ch = line[i]
        if ch.isspace():
            at_start = True
            i += 1
            continue
        if ch == '#' and at_start:
            return out, i
        if at_start and ch in "\"'":
            end = line.find(ch, i + 1)
            if end >= 0:                   # unterminated falls through
                out.append(line[i + 1:end])
                i = end + 1
                at_start = False
                continue
        j = i
        while j < n and not line[j].isspace():
            j += 1
        out.append(line[i:j])
        i, at_start = j, True
    return out, n


def strip_inline_comment(line):
    """Strip a `#` comment from a script line: everything from the first `#`
    that begins a token (start of line, or preceded by whitespace) to the end
    of the line is removed. This lets a command be commented out partially —
    `run_bundler # strict` runs `run_bundler`, `def_layer … 0.0 # note` drops
    the note. A `#` embedded in a token (no preceding whitespace, e.g. a path
    fragment) is left intact so it can't silently swallow real arguments.

    A `#` inside a QUOTED run is part of the path, not a comment (Codex #775
    P2): `require_file "inputs/rev #2/top.v"` is a legal filename on every
    filesystem, and cutting at the space before it left the handler resolving
    `"inputs/rev` — the quoted-path support failing on exactly the spelling it
    exists for.  Quoting is the escape for a `#` the same way it is for a
    space; unquoted, the comment still wins."""
    return line[:_scan(line)[1]]


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

    Rest-of-line rather than quoting, because a command taking exactly one
    path has no ambiguity to resolve: everything after the verb IS the path,
    which is what `include` means in most languages.  Quotes are accepted but
    never needed here.

    (This paragraph used to argue that `.buda` is whitespace-split with no
    quoting anywhere and that the Tcl bridge never re-quotes.  Both were true
    when it was written and neither is now — `split_quoted_args` quotes, and
    `buda::_join_args` re-quotes a whitespace-bearing argument — so the
    reasoning is restated on the one ground that still holds.)

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
    return _scan(cmd_line)[0][skip:]


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
