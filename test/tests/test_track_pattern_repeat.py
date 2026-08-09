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

"""`( <slots> )x<count>` repetition groups in def_track_pattern / add_grid_override.

A dense pattern is mostly one slot repeated, and spelling out twelve identical
`_ 1 1` triples buries the intent (and hides a typo in the middle of them).
`(_ 1 1)x12` says it once.  The expansion is purely syntactic: the grouped form
must produce a slot list byte-identical to the longhand it replaces, which is
what most of these tests assert.
"""
import ast
import contextlib
import io
import re
from pathlib import Path

import buda_cli

_BASE = ("def_layer 4 M4 H 50", "def_layer 5 M5 V 50")


def _run(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            for c in _BASE + cmds:
                s.do_command(c)
        except SystemExit as e:
            code = e.code
    return s, code, out.getvalue()


def _err(out):
    return next((l for l in out.splitlines() if l.startswith("Error:")), "")


def _slots(*cmds, layer=4):
    """Layer's global-pattern slots as (type, label, width, space_after)."""
    s, code, out = _run(*cmds)
    assert code is None, f"unexpected error: {_err(out)}"
    g = s.routing_grid.get_layer_grid(layer).global_pattern()
    return [(sl.type, sl.label, sl.width, sl.space_after) for sl in g.slots]


# ── expansion is exactly the longhand ────────────────────────────────────────

def test_group_expands_to_the_same_slots_as_longhand():
    grouped = _slots("def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1")
    longhand = _slots("def_track_pattern 4 0 VDD 2 1 " + "_ 1 1 " * 12
                      + "GND 2 1")
    assert grouped == longhand
    assert len(grouped) == 14


def test_group_preserves_unit_pitch_and_density():
    s, code, _ = _run("def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1",
                      "def_track_pattern 5 0 VDD 2 1 " + "_ 1 1 " * 12
                      + "GND 2 1")
    assert code is None
    a = s.routing_grid.get_layer_grid(4).global_pattern()
    b = s.routing_grid.get_layer_grid(5).global_pattern()
    assert a.unit_pitch() == b.unit_pitch()
    assert a.signal_density() == b.signal_density()


def test_multiple_groups_with_a_lone_slot_between_them():
    # The symmetric case: a wider slot centred in a run of narrow ones.
    slots = _slots("def_track_pattern 4 0 (_ 1 1)x5 _ 2 1 (_ 1 1)x5")
    assert [w for _t, _l, w, _s in slots] == [1] * 5 + [2] + [1] * 5


def test_group_may_hold_several_slots():
    slots = _slots("def_track_pattern 4 0 (VDD 2 1 _ 1 1)x3 GND 2 1")
    assert [t for t, _l, _w, _s in slots] == [
        "POWER", "SIGNAL", "POWER", "SIGNAL", "POWER", "SIGNAL", "GROUND"]


def test_count_may_be_spaced_and_capital_x():
    a = _slots("def_track_pattern 4 0 (_ 1 1)x3")
    b = _slots("def_track_pattern 4 0 (_ 1 1)x 3")
    c = _slots("def_track_pattern 4 0 ( _ 1 1 ) X 3")
    assert a == b == c
    assert len(a) == 3


def test_count_of_one_is_a_plain_slot():
    assert (_slots("def_track_pattern 4 0 (_ 1 1)x1")
            == _slots("def_track_pattern 4 0 _ 1 1"))


def test_paren_free_pattern_is_untouched():
    # The normalize/split round-trip must not perturb the classic form.
    slots = _slots("def_track_pattern 4 0 POWER 2 1 SIGNAL 1 1 GROUND 2 1")
    assert slots == [("POWER", "power", 2, 1), ("SIGNAL", "signal", 1, 1),
                     ("GROUND", "ground", 2, 1)]


def test_add_grid_override_supports_groups_too():
    # Both slot-list commands share one parser, so the syntax cannot drift.
    s, code, out = _run("def_track_pattern 4 0 _ 1 1",
                        "add_grid_override 4 0 0 50 50 0 (_ 1 1)x6 GND 2 1")
    assert code is None, _err(out)
    assert "7 slots" in out


# ── malformed groups fail LOUD (never silently mis-expand) ───────────────────

def test_zero_count_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1)x0")
    assert code == 1
    assert "not a positive integer" in _err(out)


def test_missing_count_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1)")
    assert code == 1
    assert "missing its count" in _err(out)


def test_non_numeric_count_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1)xz")
    assert code == 1
    assert "not a positive integer" in _err(out)


def test_unterminated_group_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1 GND 2 1")
    assert code == 1
    assert "unterminated '('" in _err(out)


def test_unmatched_close_paren_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 _ 1 1)x3")
    assert code == 1
    assert "unmatched ')'" in _err(out)


def test_empty_group_is_a_hard_error():
    _s, code, out = _run("def_track_pattern 4 0 ()x3")
    assert code == 1
    assert "empty repetition group" in _err(out)


def test_partial_triple_in_group_is_a_hard_error():
    # `(_ 1)x3` would silently expand to a mis-aligned slot stream.
    _s, code, out = _run("def_track_pattern 4 0 (_ 1)x3")
    assert code == 1
    assert "not a whole number of" in _err(out)


def test_nested_groups_are_a_hard_error():
    # The grammar is deliberately flat, so say so rather than mis-parse.
    _s, code, out = _run("def_track_pattern 4 0 ((_ 1 1)x2)x3")
    assert code == 1
    assert "nested '('" in _err(out)


def test_absurd_count_is_a_hard_error_not_an_oom():
    # The compact form is the only way to ASK for a billion slots (you cannot
    # type them), so the count is bounded before the expansion allocates.
    _s, code, out = _run("def_track_pattern 4 0 (_ 1 1)x100000000")
    assert code == 1
    assert "exceeds the maximum" in _err(out)


def test_count_at_the_cap_is_accepted():
    # The bound is a typo guard, not a policy — the largest allowed count is
    # still a legal (if unreasonable) pattern.
    from buda_cmds.grid_cmds import _MAX_SLOT_REPEAT
    slots = _slots(f"def_track_pattern 4 0 (_ 1 1)x{_MAX_SLOT_REPEAT}")
    assert len(slots) == _MAX_SLOT_REPEAT
    _s, code, out = _run(
        f"def_track_pattern 4 0 (_ 1 1)x{_MAX_SLOT_REPEAT + 1}")
    assert code == 1
    assert "exceeds the maximum" in _err(out)


# ── every reader of a pattern LINE must share the one parser ────────────────
#
# The compact syntax is understood by `slot_groups.expand_slot_groups` and
# nothing else.  Any code that reads a `.buda` track-pattern line as TEXT and
# walks it three tokens at a time reads `0.25)x7` as a width — which is not a
# hypothetical: it broke 158 tests when the corpus was rewritten, and then
# broke CI a second time when a fourth reader (`test_gds_export::_scaled_tracks`)
# landed on main during that same review.  Nothing structural stops a fifth,
# so this guard is the thing that catches it at authoring time instead.

_SIGNATURE = re.compile(
    r"""(?:startswith|match|search|fullmatch|re\.compile)\s*\(\s*"""
    r"""r?["'][^"']*\b(?:def_track_pattern|add_grid_override)\b""")

# slot_groups.py DEFINES the expansion, so it cannot call it.  This file quotes
# the offending idioms verbatim as fixtures (that is the point of the signature
# test), so the scan would otherwise flag its own evidence.
_EXEMPT = {"slot_groups.py", Path(__file__).name}

_REPO = Path(__file__).resolve().parents[2]
# Scan the WHOLE repository, not a list of roots.  A root list has to be
# remembered, and the readers are exactly the code nobody remembered: `demo/`
# and `flow/` hold ~28 generator scripts that emit `.buda` and could grow a
# reader tomorrow.  Excluded instead are the two directories that are not
# maintained source — build products and bytecode caches.
_EXCLUDED_DIRS = {"build", ".git", "__pycache__"}


def _sources():
    for path in sorted(_REPO.rglob("*.py")):
        if not _EXCLUDED_DIRS.intersection(path.relative_to(_REPO).parts):
            yield path


def _calls_the_expansion(text):
    """True if the source CALLS expand_slot_groups (not merely mentions it).

    A substring test would accept `# TODO: call expand_slot_groups`, a
    docstring, or an import that is never used — all of which leave a naive
    three-token walk misreading grouped patterns.  The evidence has to be a
    call node.  An unparseable file falls back to the substring, since a
    syntax error there is someone else's failure to report."""
    try:
        tree = ast.parse(text)
    except SyntaxError:                      # pragma: no cover - none today
        return "expand_slot_groups" in text
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = (fn.id if isinstance(fn, ast.Name) else
                    fn.attr if isinstance(fn, ast.Attribute) else None)
            if name == "expand_slot_groups":
                return True
    return False


def test_the_signature_detects_the_historical_offenders():
    """A grep-based guard is worthless if it silently stops matching, so pin it
    against the exact code that shipped the bug — both idioms, verbatim."""
    # tools/double_track_density.py, before the fix
    assert _SIGNATURE.search(
        r'PAT = re.compile(r"^(\s*def_track_pattern\s+(\S+)\s+(\S+)\s+)(.*?)(\s*)$")')
    # test_gds_export.py / test_mirror_symmetric_tracks.py, before the fix
    assert _SIGNATURE.search('        if line.startswith("def_track_pattern"):')
    assert _SIGNATURE.search("    if not line.startswith('add_grid_override'):")
    # …and does NOT fire on an assertion about output text, which is not parsing
    assert not _SIGNATURE.search(
        '    assert "def_layer" in sc and "def_track_pattern" in sc')


def test_every_pattern_line_reader_uses_the_shared_expansion():
    """File-level, and deliberately so: it asks whether a file that matches a
    pattern line calls the expansion AT ALL.  That is exactly the shape of all
    four historical offenders — each was written with no awareness the syntax
    existed.  It would NOT catch a file that expands in one place and walks
    triples naively in another; proving that statically is a different
    problem, and the cheap check covers the failure mode that actually
    happened, twice."""
    offenders = [
        p.relative_to(_REPO) for p in _sources()
        if p.name not in _EXEMPT
        and _SIGNATURE.search(t := p.read_text())
        and not _calls_the_expansion(t)
    ]
    assert not offenders, (
        "these files match a `.buda` track-pattern line and parse it "
        "themselves, but never call expand_slot_groups — they will read "
        "`0.25)x7` as a width:\n  "
        + "\n  ".join(str(p) for p in offenders)
        + "\n\nFix: `from slot_groups import expand_slot_groups` and expand "
          "the slot tokens before walking them in threes.")


def test_the_guard_would_catch_a_new_offender():
    """The scan is only meaningful if a fresh naive reader trips it."""
    bad = ('if line.startswith("def_track_pattern"):\n'
           '    tok = line.split()[3:]\n')
    assert _SIGNATURE.search(bad) and not _calls_the_expansion(bad)


def test_mentioning_the_expansion_is_not_enough():
    """A comment, a docstring or an unused import all leave the naive walk in
    place, so compliance is a CALL node — not the substring."""
    for alibi in ('# TODO: call expand_slot_groups here\n',
                  '"""Should use expand_slot_groups."""\n',
                  'from slot_groups import expand_slot_groups\n'):
        src = alibi + 'if line.startswith("def_track_pattern"):\n    pass\n'
        assert "expand_slot_groups" in src          # the substring is there…
        assert not _calls_the_expansion(src)        # …but it is never called
    # the real thing, both spellings, does count
    assert _calls_the_expansion('x = expand_slot_groups("c", toks)\n')
    assert _calls_the_expansion('x = sg.expand_slot_groups("c", toks)\n')


def test_the_scan_covers_the_whole_repository():
    """A root LIST has to be remembered, and the readers are exactly the code
    nobody remembered.  Pin that the scan reaches the generator scripts under
    demo/ and flow/ — outside the four roots this originally walked — and that
    build products stay out of it."""
    scanned = {p.relative_to(_REPO) for p in _sources()}
    assert Path("demo/gen_ariane136.py") in scanned
    assert Path("flow/def/gen_chip.py") in scanned
    assert Path("src/slot_groups.py") in scanned
    assert not any(p.parts[0] == "build" for p in scanned)
    assert not any("__pycache__" in p.parts for p in scanned)
