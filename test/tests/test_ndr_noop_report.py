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

"""A declared NDR rule that constrains NOTHING must say so.

`opens_ndr.md` §2's residual: "a rule that resolves to one slot resolves to NO
rule".  An absolute width below one signal slot's worth of channel quantizes to
`width_slots 1 / guard_slots 0`, and with no shield that spec is INACTIVE — so
every consumer short-circuits on `!active()` and the governed bus routes
exactly as an ungoverned one.  `def_ndr` refuses that rule when it is spelled
out (default width, default spacing, no shield); arrived at through the GRID it
used to be silent from end to end.

Two ids, because a methodology gates on them differently:

  * **BUDA-1913** (WARNING) — dead on EVERY layer the rule can reach.  The rule
    does nothing at all, anywhere.
  * **BUDA-1914** (INFO) — dead on SOME.  The layer already gives the declared
    width with a default wire, which under the channel reading is a satisfied
    rule rather than a short one; what it costs is that no NDR audit covers
    that metal.

METHOD NOTE (the lesson of test_ndr_audit_vacuity): every "it fires" assertion
here has a twin that pins the SAME fixture staying silent when the rule
genuinely bites, so the tests are about the rule and not about a report that
fires unconditionally.
"""
import sys

import pytest

import buda_diag

# At MODULE scope, deliberately.  `buda_cli` calls `faulthandler.enable()` on
# import, which needs a real file descriptor — and capsys has replaced stdout
# and stderr with objects that have none, so importing it inside a capsys test
# raises `io.UnsupportedOperation: fileno`.
sys.path[:0] = ["build", "src", "tools"]
import buda_cli  # noqa: E402


# Two layers per direction, one pair FINE and one COARSE, so one declared width
# can bite on one pair and not the other -- which is the partial case, and is
# also why an absolute rule needs per-layer resolution at all.
#
#   fine   (M3/M4): period 30 over 12 signal slots -> 2.5 units of channel/slot
#   coarse (M5/M6): period 40 over  4 signal slots -> 10 units of channel/slot
_STACK = """add_block a 0 0 200 200
add_block b 900 600 1100 800
add_bus g_[4] a.p b.q
def_layer 3 M3 H 20
def_layer 4 M4 V 20
def_layer 5 M5 H TOP 20
def_layer 6 M6 V TOP 20
def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_track_pattern 5 0 VDD 10 2 (_ 2 2)x4 GND 10 2
def_track_pattern 6 0 VDD 10 2 (_ 2 2)x4 GND 10 2
"""
_BUNDLE = "set_ndr g_ r\nrun_bundler STRICT\n"


def _run(capsys, decl, tail=""):
    """Declare `decl`, bundle, and return everything printed."""
    s = buda_cli.BudaSession()
    for raw in (_STACK + decl + _BUNDLE + tail).splitlines():
        line = raw.split("#")[0].strip()
        if line:
            s.do_command(line)
    return s, capsys.readouterr().out


def _ids(out):
    """The message ids printed, as a set -- the typed identity, not a
    substring of the prose around it (test_ndr_audit_vacuity's lesson: a
    message whose id changed while its wording stayed would slip past a text
    search, and the id is what a methodology gates on)."""
    return {buda_diag.id_of_line(ln) for ln in out.splitlines()
            if buda_diag.id_of_line(ln)}


# ── the headline: dead everywhere ────────────────────────────────────────

@pytest.mark.parametrize("decl,fires", [
    # width 8 on the COARSE pair -> ceil(8/10) = 1 slot: dead on M5 and M6,
    # the only layers it can reach.
    ("def_ndr r width 8 layers M5,M6\n", True),
    # The SAME layers, a width that genuinely needs two slots there.  The
    # twin that keeps the test about the rule rather than the fixture.
    ("def_ndr r width 12 layers M5,M6\n", False),
])
def test_dead_everywhere_warns_and_a_biting_rule_does_not(capsys, decl, fires):
    _, out = _run(capsys, decl)
    assert ("BUDA-1913" in _ids(out)) is fires, out


def test_the_warning_names_the_rule_and_the_arithmetic(capsys):
    """A verdict a user cannot check is half a message: the line has to say
    which layers were judged and what the declared value came to there."""
    _, out = _run(capsys, "def_ndr r width 8 layers M5,M6\n")
    line = next(ln for ln in out.splitlines()
                if buda_diag.id_of_line(ln) == "BUDA-1913")
    assert "'r'" in line
    assert "L5" in line and "L6" in line
    assert "8" in line                      # the declared width
    assert "10" in line                     # the pitch it was divided by


# ── the partial case ─────────────────────────────────────────────────────

def test_a_rule_dead_on_only_some_layers_is_an_INFO(capsys):
    """Unrestricted `width 3`: two slots on the fine pair, one on the coarse.
    The rule bites, so this is not a failure — but the metal it places on the
    coarse pair is governed in name only, and no audit covers it."""
    _, out = _run(capsys, "def_ndr r width 3\n")
    ids = _ids(out)
    assert "BUDA-1914" in ids and "BUDA-1913" not in ids, out
    line = next(ln for ln in out.splitlines()
                if buda_diag.id_of_line(ln) == "BUDA-1914")
    # BOTH halves: which layers are dead AND which are alive.  Naming only
    # the dead ones would read like the total case.
    assert "L5" in line and "L6" in line     # dead (coarse)
    assert "L3" in line and "L4" in line     # alive (fine)


def test_a_per_layer_override_can_be_the_no_op(capsys):
    """The shape nothing else could see.

    `_report_abs_metal` speaks only post-DNUTS, only for an absolute width,
    and only when the placed metal falls short.  A rule that bites everywhere
    EXCEPT one layer, where `def_ndr_layer` hands back the default width, is
    invisible to it — the value on that layer is a multiplier, so there is no
    absolute to compare metal against."""
    _, out = _run(capsys, "def_ndr r width x2\n"
                          "def_ndr_layer r M4 width x1\n")
    ids = _ids(out)
    assert "BUDA-1914" in ids, out
    line = next(ln for ln in out.splitlines()
                if buda_diag.id_of_line(ln) == "BUDA-1914")
    assert "L4" in line


def test_a_bundle_capped_onto_the_dead_layers_is_INERT_not_partial(capsys):
    """The verdict follows the BUNDLE, not the rule's union of layers.

    Which layers a governed bundle can reach is not a property of the rule
    alone — `set_cell_layer_cap` and the hier band resolution narrow
    `allowed_layers` per wrapper — so one rule can be inert for a capped
    bundle while its neighbour is merely partial.  Judged from the union, the
    capped bundle's verdict would be downgraded to the INFO, which is the one
    a reader can safely ignore.

    The cap is applied directly to the wrapper here rather than through a
    hierarchical vehicle: the aggregation is what is under test, and the two
    band resolvers already have their own tests."""
    from buda_cmds import ndr_cmds
    s, _ = _run(capsys, "def_ndr r width 3\n")          # partial: dead on M5,M6
    assert len(s.bundles) == 1
    w = s.bundles[0]
    assert w.input.ndr.rule_name == "r"
    # A band that leaves this bundle only the layers the rule is dead on.
    w.input.allowed_layers = [5, 6]
    s._ndr_noop_said = set()                            # re-judge from scratch
    ndr_cmds.report_noop_ndr_rules(s)
    out = capsys.readouterr().out
    assert "BUDA-1913" in _ids(out), out
    assert "BUDA-1914" not in _ids(out), out
    line = next(ln for ln in out.splitlines()
                if buda_diag.id_of_line(ln) == "BUDA-1913")
    assert "1 of 1 governed bundle(s)" in line, line


# ── what must stay silent ────────────────────────────────────────────────

def test_a_multiplier_rule_is_never_a_no_op(capsys):
    """`xN` is pattern-independent and `def_ndr` refuses N < 1, so a
    multiplier rule quantizes to >= 2 slots on every layer by construction.
    Silence here is a property, not luck."""
    _, out = _run(capsys, "def_ndr r width x2\n")
    assert not ({"BUDA-1913", "BUDA-1914"} & _ids(out)), out


def test_a_shield_keeps_a_one_slot_rule_ALIVE(capsys):
    """The predicate is `NdrSpec::active()`, not the width alone.

    `width 8` on the coarse pair resolves to one slot exactly as in the
    headline case — but `shield bus` keeps the spec active at any pitch, so
    the rule still does something and there is nothing to report.  Keying the
    check on `width_slots <= 1` instead would have warned about a rule that
    places shields."""
    _, out = _run(capsys, "def_ndr r width 8 shield bus layers M5,M6\n")
    assert not ({"BUDA-1913", "BUDA-1914"} & _ids(out)), out


def test_no_scopes_no_output(capsys):
    """R12: a design that declares no NDR is byte-identical, and that has to
    include the diagnostics."""
    s = buda_cli.BudaSession()
    for raw in (_STACK + "run_bundler STRICT\n").splitlines():
        line = raw.split("#")[0].strip()
        if line:
            s.do_command(line)
    out = capsys.readouterr().out
    assert "[NDR]" not in out and not _ids(out), out


# ── the verdict is said once, but a CHANGED verdict is said ──────────────

def test_the_verdict_is_reported_once_across_both_call_sites(capsys):
    """The judgement runs at bundling AND again before detailed NUTS (the
    second is the only one a resumed session reaches, since `load_pipeline`
    restores bundles without re-bundling).  A flow that runs both must not
    hear the same verdict twice."""
    _, out = _run(capsys, "def_ndr r width 8 layers M5,M6\n",
                  tail="generate_topologies\nset_track_pitch 3\n"
                       "run_planner 1\nrun_nuts\nrun_detailed_nuts\n")
    assert sum(1 for ln in out.splitlines()
               if buda_diag.id_of_line(ln) == "BUDA-1913") == 1, out


def test_a_verdict_that_CHANGES_is_reported(capsys):
    """Why the memo is keyed on the verdict and not on the rule name.

    A layer with no track pattern is not judged — nothing can be placed there
    at all — so a rule can pass bundling clean and become dead on that layer
    once its pattern arrives.  Keyed on the name, the second look would find
    the rule "already seen" and stay silent about a verdict that had changed
    underneath it."""
    s = buda_cli.BudaSession()
    # M4 gets no pattern until after bundling, and the rule hands back the
    # DEFAULT width there — so M4 is the layer whose verdict moves.
    for raw in ("""add_block a 0 0 200 200
add_block b 900 600 1100 800
add_bus g_[4] a.p b.q
def_layer 3 M3 H TOP 20
def_layer 4 M4 V TOP 20
def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_ndr r width x2
def_ndr_layer r M4 width x1
set_ndr g_ r
run_bundler STRICT
""").splitlines():
        line = raw.strip()
        if line:
            s.do_command(line)
    early = capsys.readouterr().out
    assert not ({"BUDA-1913", "BUDA-1914"} & _ids(early)), early
    for raw in ("def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1\n"
                "generate_topologies\nset_track_pitch 3\nrun_planner 1\n"
                "run_nuts\nrun_detailed_nuts\n").splitlines():
        line = raw.strip()
        if line:
            s.do_command(line)
    late = capsys.readouterr().out
    assert "BUDA-1914" in _ids(late), late
    line = next(ln for ln in late.splitlines()
                if buda_diag.id_of_line(ln) == "BUDA-1914")
    assert "L4" in line


# ── the catalogue ────────────────────────────────────────────────────────

def test_both_ids_are_registered_at_the_severity_they_are_emitted_at():
    """An id is a contract: `dump_messages` has to be able to print it, and a
    gate reading the severity off the LINE has to see what the catalogue
    promises."""
    assert buda_diag.severity_of(cid := "BUDA-1913") == buda_diag.WARNING, cid
    assert buda_diag.severity_of("BUDA-1914") == buda_diag.INFO
    for mid in ("BUDA-1913", "BUDA-1914"):
        assert buda_diag.severity_of_line(buda_diag.format(mid, "x")) == \
            buda_diag.severity_of(mid)
