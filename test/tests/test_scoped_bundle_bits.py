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

"""SCOPED bundle bit bound: `set_max_bundle_bits <N|off> for <prefix>`.

The global cap is all-or-nothing.  On a design where ONE bus is
width-doomed — its bits cannot fit any layer's supply at its seat, the
issue #536 residual class — capping every wide bundle makes the whole
design pay for one bundle's problem: measured on
`flow/rnr/mix2_fast_on_aligned_sql.buda`, the global `8` clears the seat
but costs **+59% abstract wirelength**, while the same cap scoped to the
doomed bus alone costs **+0.4%** and still takes the design's
supply-doomed TOP seats to zero.

Longest matching prefix wins (the `set_bundling` idiom).  A scope of `0`
EXEMPTS its bundles from the global cap.
"""
import contextlib
import io

import buda
import buda_cli


def _quiet(s, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)


def _say(s, cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(cmd)
    return out.getvalue()


def _flat_session():
    """Two equally wide buses on SEPARATE block pairs — `wide[12]` on A->B
    and `other[12]` on C->D.  Separate pairs matter: STRICT bundles by
    (driver block, receiver blocks), so two buses between the same pair
    would merge into one bundle and there would be nothing to target."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "add_block A 0 0 100 100",
           "add_block B 400 0 500 100",
           "add_block C 0 400 100 500",
           "add_block D 400 400 500 500",
           "add_bus wide[12] A.out B.in",
           "add_bus other[12] C.out D.in")
    return s


def _parts(s, prefix):
    """Bundles whose first net starts with `prefix`, by bit count."""
    out = []
    for w in s.bundles:
        nets = list(w.input.original_bundle.get_net_names())
        if nets and nets[0].startswith(prefix):
            out.append(len(nets))
    return sorted(out)


# ── the scoped cap itself ────────────────────────────────────────────────────

def test_scope_splits_only_the_named_bus():
    """The whole point: `other` is just as wide as `wide` and must be left
    alone.  This is the difference between +0.4% and +59% WL on the vehicle
    that motivated the knob."""
    s = _flat_session()
    _quiet(s, "set_max_bundle_bits 6 for wide", "run_bundler")
    assert _parts(s, "wide") == [6, 6]        # split
    assert _parts(s, "other") == [12]         # untouched


def test_scope_reports_itself_as_scoped():
    s = _flat_session()
    _quiet(s, "set_max_bundle_bits 6 for wide")
    log = _say(s, "run_bundler")
    assert "(scoped)" in log, log


def test_no_scope_is_identity():
    """Nothing declared: byte-identical to no cap at all."""
    s = _flat_session()
    _quiet(s, "run_bundler")
    assert all("|SPLIT:" not in w.input.original_bundle.reason
               for w in s.bundles)


def test_longest_prefix_wins():
    """`wide` and `wide_hi` both match; the longer scope governs."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "add_block A 0 0 100 100", "add_block B 400 0 500 100",
           "add_block C 0 400 100 500", "add_block D 400 400 500 500",
           "add_bus wide_hi[12] A.out B.in",
           "add_bus wide_lo[12] C.out D.in",
           "set_max_bundle_bits 6 for wide",
           "set_max_bundle_bits 3 for wide_hi",
           "run_bundler")
    assert _parts(s, "wide_hi") == [3, 3, 3, 3]   # the longer scope
    assert _parts(s, "wide_lo") == [6, 6]         # the shorter one


def test_scope_zero_exempts_from_the_global_cap():
    """A global cap plus a `0` scope: everything splits except the exempt
    bus — the inverse targeting, for a bus a design-wide bound would
    needlessly fragment."""
    s = _flat_session()
    _quiet(s, "set_max_bundle_bits 6", "set_max_bundle_bits 0 for wide",
           "run_bundler")
    assert _parts(s, "wide") == [12]          # exempt
    assert _parts(s, "other") == [6, 6]       # global cap still applies


def test_unscoped_bundles_fall_back_to_the_global_cap():
    s = _flat_session()
    _quiet(s, "set_max_bundle_bits 6", "set_max_bundle_bits 4 for wide",
           "run_bundler")
    assert _parts(s, "wide") == [4, 4, 4]     # scope
    assert _parts(s, "other") == [6, 6]       # global


# ── command surface ──────────────────────────────────────────────────────────

def test_off_for_prefix_clears_one_scope():
    s = _flat_session()
    _quiet(s, "set_max_bundle_bits 6 for wide")
    log = _say(s, "set_max_bundle_bits off for wide")
    assert "cleared" in log, log
    _quiet(s, "run_bundler")
    assert _parts(s, "wide") == [12]


def test_off_for_unknown_scope_is_an_error():
    """Silently succeeding would hide a typo'd prefix — the scope would
    stay active and the user would think they had cleared it."""
    s = _flat_session()
    log = _say(s, "set_max_bundle_bits off for nosuchbus")
    assert "Error" in log, log


def test_global_off_clears_scopes_too():
    s = _flat_session()
    _quiet(s, "set_max_bundle_bits 6 for wide", "set_max_bundle_bits off",
           "run_bundler")
    assert _parts(s, "wide") == [12]


def test_auto_has_no_scoped_form():
    """The AUTO cap is already per-bundle (each bundle's own busterm edges),
    so a scoped `auto` would be meaningless — say so instead of accepting it."""
    s = _flat_session()
    log = _say(s, "set_max_bundle_bits auto for wide")
    assert "Error" in log and "auto" in log, log


def test_bad_scoped_usage_is_rejected():
    s = _flat_session()
    assert "Error" in _say(s, "set_max_bundle_bits 6 for")
    assert "Error" in _say(s, "set_max_bundle_bits 6 for a b")
    assert "Error" in _say(s, "set_max_bundle_bits x for wide")
    assert "Error" in _say(s, "set_max_bundle_bits -1 for wide")


# ── hier path ────────────────────────────────────────────────────────────────

def _hier_bdb(width):
    """One cell type instantiated twice, plus a wide top-level bus — so a
    scope can hit the top bus while leaving the cell template alone."""
    db = buda.BDB(":memory:")
    db.add_cell("core_cell", 300, 220)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("core_cell", "a_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("core_cell", "b_i", "pipe_cell", 175, 60)
    db.add_inst("core_i1", "core_cell", "", 0, 0)
    db.add_inst("core_i2", "core_cell", "", 0, 400)
    for i in range(width):
        db.add_net_pins(f"c1_bus_{i}", "core_i1/a_i.out", ["core_i1/b_i.in"])
        db.add_net_pins(f"c2_bus_{i}", "core_i2/a_i.out", ["core_i2/b_i.in"])
        db.add_net_pins(f"top_bus_{i}", "core_i1/b_i.out", ["core_i2/a_i.in"])
    buda.BustermGen(db).derive(1)
    return db


def test_hier_scope_splits_only_the_named_bundle():
    """The hier split runs on TEMPLATE bundles before per-instance expansion;
    a scope must select among them the same way."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = _hier_bdb(12)
    _quiet(s, "set_max_bundle_bits 4 for top_bus", "run_hier_bundler")
    assert _parts(s, "top_bus") == [4, 4, 4]
    # The cell-local template keeps all 12 bits.
    assert _parts(s, "c1_bus") == [12]


def test_hier_no_scope_match_is_identity():
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = _hier_bdb(12)
    _quiet(s, "set_max_bundle_bits 4 for nothing_matches", "run_hier_bundler")
    assert all("|SPLIT:" not in w.input.original_bundle.reason
               for w in s.bundles)


# ── template↔replica lockstep (Codex P1 on #582) ─────────────────────────────
#
# Occurrences of ONE cell-local bundle carry instance-specific net names —
# `c1_bus_*` on the template, `c2_bus_*` on its replica.  Resolving a scope
# per bundle would let a scope naming one instance cap only that occurrence:
# the two split into different part counts, the part-for-part parent linkage
# cannot be made, and every replica part gets pinned to template part 1 — so
# `_expand_hier_bundles` either falls back to the reference instance's nets
# for the later parts or overwrites donors and drops replica bits.  The cap is
# therefore resolved ONCE for the whole occurrence group.

def _occurrences(s):
    """(template_parts, replica_parts) by their net-name instance prefix."""
    t = [w.input.original_bundle for w in s.bundles
         if list(w.input.original_bundle.get_net_names())[0].startswith("c1_")]
    r = [w.input.original_bundle for w in s.bundles
         if list(w.input.original_bundle.get_net_names())[0].startswith("c2_")]
    return t, r


def test_scope_naming_the_template_also_splits_the_replica():
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = _hier_bdb(12)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command("set_max_bundle_bits 4 for c1_bus")
        s.do_command("run_hier_bundler")
    t, r = _occurrences(s)
    assert len(t) == 3 and len(r) == 3, (len(t), len(r))
    assert "parent linkage pinned to part 1" not in out.getvalue()


def test_scope_naming_only_the_replica_still_splits_in_lockstep():
    """The asymmetric direction: `c2_bus` names the REPLICA's nets only.  The
    group is one logical bundle, so the scope governs the class."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = _hier_bdb(12)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command("set_max_bundle_bits 4 for c2_bus")
        s.do_command("run_hier_bundler")
    t, r = _occurrences(s)
    assert len(t) == 3 and len(r) == 3, (len(t), len(r))
    assert "parent linkage pinned to part 1" not in out.getvalue()


def test_replica_parts_link_part_for_part_to_template_parts():
    """The linkage the lockstep exists to preserve: replica part k's parent is
    template part k, never all-pinned-to-part-1."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = _hier_bdb(12)
    _quiet(s, "set_max_bundle_bits 4 for c1_bus", "run_hier_bundler")
    t, r = _occurrences(s)
    assert sorted(x.parent_id for x in r) == sorted(x.id for x in t)


def test_equal_length_scopes_resolve_to_the_tighter_cap():
    """`c1_bus` and `c2_bus` are equally specific and both match the group, so
    the resolution must be defined: the most restrictive cap wins (it also
    satisfies the looser request)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = _hier_bdb(12)
    _quiet(s, "set_max_bundle_bits 6 for c1_bus",
           "set_max_bundle_bits 3 for c2_bus", "run_hier_bundler")
    t, r = _occurrences(s)
    assert len(t) == 4 and len(r) == 4      # ceil(12/3), not ceil(12/6)


def test_equal_length_scopes_ignore_an_exempt_tie():
    """`0` is unbounded, so it loses a tie rather than cancelling the cap."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = _hier_bdb(12)
    _quiet(s, "set_max_bundle_bits 4 for c1_bus",
           "set_max_bundle_bits 0 for c2_bus", "run_hier_bundler")
    t, r = _occurrences(s)
    assert len(t) == 3 and len(r) == 3
