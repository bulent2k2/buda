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

"""`( <slots> )x<count>` repetition groups in a track-pattern slot list.

Standalone and dependency-free ON PURPOSE.  The CLI is not the only reader of a
`.buda` track-pattern line -- `tools/double_track_density.py` rewrites slot
lists in place and the track-geometry tests parse the checked-in fixtures
directly -- and each had its own naive triple-splitter, so the compact syntax
was invisible to them: they read `0.25)x7` as a width and died.  One expansion,
imported by all three, is the only arrangement that stays true as fixtures are
rewritten.

It lives OUTSIDE `buda_cmds` because importing anything from that package runs
its `__init__`, which imports every command module and therefore the compiled
`buda` extension.  A text-rewriting tool should not need a built extension to
read a slot list.
"""
import sys


# Upper bound on a single `( … )x<count>` repetition, checked before the
# expansion allocates.  Sized as a typo guard: a track pattern is one repeating
# unit that tiles across the layer, so real periods run to tens of slots — this
# is orders of magnitude above any legitimate pattern while still catching a
# stray-zeros count that would otherwise exhaust memory.
MAX_SLOT_REPEAT = 4096


def expand_slot_groups(cmd_name, toks, usage=""):
    """Expand `( <slots> )x<N>` repetition groups into a flat token list.

    PUBLIC because the CLI is not the only reader of a `.buda` track-pattern
    line: `tools/double_track_density.py` rewrites slot lists in place, and the
    track-geometry tests parse the checked-in fixtures directly.  Each had its
    own naive triple-splitter, so the compact syntax was invisible to them --
    they read `0.25)x7` as a width and died.  One expansion, imported by all
    three, is the only way that stays true as fixtures are rewritten.

    A dense pattern is mostly one slot repeated: spelling out twelve identical
    `_ 1 1` triples buries the intent (and any typo in the middle of them).
    `(_ 1 1)x12` says it once.  The group may hold SEVERAL slots — `(VDD 2 1
    _ 1 1)x4` — since that is the same parse and strictly more expressive.

    Spacing is free: `)x12`, `)x 12` and `) x 12` are the same, because the
    structural parens are split out before scanning.  Groups do NOT nest —
    the grammar stays flat so the error messages can stay specific.

    Byte-identical for a paren-free list (the normalize/split round-trips it).
    """
    # Split the structural parens off whatever they are glued to, so `(_`,
    # `1)x12` and `( _ 1 1 ) x 12` all reduce to the same token stream.
    toks = " ".join(toks).replace("(", " ( ").replace(")", " ) ").split()
    if "(" not in toks and ")" not in toks:
        return toks

    def die(msg):
        print(f"Error: {cmd_name} {msg}\n  Usage: {usage}")
        sys.exit(1)

    out, i, n = [], 0, len(toks)
    while i < n:
        t = toks[i]
        if t == ")":
            die("unmatched ')' in the slot list — a repetition group is "
                "written `( <slots> )x<count>`")
        if t != "(":
            out.append(t)
            i += 1
            continue
        # ── a group: collect up to the matching ')' ──────────────────────
        i += 1
        group = []
        while i < n and toks[i] != ")":
            if toks[i] == "(":
                die("nested '(' in the slot list — repetition groups do not "
                    "nest; repeat the expanded slots instead")
            group.append(toks[i])
            i += 1
        if i >= n:
            die("unterminated '(' in the slot list — expected `)x<count>`")
        i += 1                                   # consume ')'
        if not group:
            die("empty repetition group '()' in the slot list")
        # ── the count: `x12`, `x 12`, or `X12` ──────────────────────────
        if i >= n or not toks[i][:1] in ("x", "X"):
            die("repetition group is missing its count — write "
                "`( <slots> )x<count>`, e.g. `(_ 1 1)x12`")
        digits = toks[i][1:]
        i += 1
        if not digits:                           # the count is the next token
            if i >= n:
                die("repetition group is missing its count — write "
                    "`( <slots> )x<count>`, e.g. `(_ 1 1)x12`")
            digits = toks[i]
            i += 1
        if not digits.isdigit() or int(digits) < 1:
            die(f"repetition count '{digits}' is not a positive integer — "
                f"write `( <slots> )x<count>`, e.g. `(_ 1 1)x12`")
        # Bound the count BEFORE expanding.  `group * count` materializes the
        # whole token list, so a fat-fingered `x100000000` would be an OOM or a
        # hang instead of a diagnostic — a failure mode the longhand could not
        # have (you cannot type 100M tokens).  The cap is a TYPO GUARD, not a
        # policy: a track period is one repeating unit, so even a hundred slots
        # is unusual and this sits orders of magnitude above any real pattern.
        if int(digits) > MAX_SLOT_REPEAT:
            die(f"repetition count {digits} exceeds the maximum "
                f"{MAX_SLOT_REPEAT} — a track pattern is ONE repeating unit, "
                f"so this is almost certainly a typo; the pattern tiles across "
                f"the layer on its own")
        if len(group) % 3 != 0:
            die(f"repetition group has {len(group)} token(s), not a whole "
                f"number of `<type> <width> <space_after>` triples")
        out.extend(group * int(digits))
    return out
