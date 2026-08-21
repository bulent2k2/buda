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

"""The routing grid is part of the checkpoint (schema v29).

Track patterns and keepouts were the last physical-design facts with no
table — pure session state, rebuilt by whoever declared them.  When that
"whoever" is `import_def_lef`, a hier resume loses them, because it HOLDS the
import (a replayed one is a duplicate-instance error).

`run_detailed_nuts` refused for want of a grid, which was the only loud
moment; the quiet half is worse.  `run_nuts` and the healers run on before
it, so the resumed session re-solves against a coarser, blockage-free model
than the checkpoint was routed under — the `def_layer` overhead figure
instead of the DEF's own pitch, and no obstruction at all.  Measured on
`flow/ariane133` before this landed: the build reports 20 keepout-seated
segments and the resume 18, with `[DEF] tracks installed` absent from the
resume log entirely.
"""
import contextlib
import io
from pathlib import Path

import pytest

import buda
import buda_cli

_ROOT = Path(__file__).resolve().parents[2]
_DEF = _ROOT / "flow" / "def"


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
    return s, buf.getvalue()


def _pattern_sig(grid, lid):
    """Everything about a layer's pattern that the width model reads."""
    p = grid.get_layer_grid(lid).global_pattern()
    return (round(p.origin, 9), round(p.unit_pitch(), 9),
            round(p.signal_density(), 9), p.bounded,
            round(p.bound_lo, 9), round(p.bound_hi, 9),
            [(s.type, s.label, s.width, s.space_after) for s in p.slots])


# ── the declaration round-trips ────────────────────────────────────────────

def test_a_declared_grid_comes_back_on_reopen(tmp_path):
    db = str(tmp_path / "g.bdb")
    setup = ["def_layer 4 M4 H LOW 30", "def_layer 5 M5 V LOW 30"]
    s1, _ = _session(setup + [
        f"open_bdb {db}",
        "def_track_pattern 4 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1",
        "def_track_pattern 5 0.5 _ 1 1 _ 1 1",
        "add_grid_override 4 0 0 100 100 0 _ 2 2",
        "add_keepout 10 10 50 50 M4",
    ])
    s2, out = _session(setup + [f"open_bdb {db}"])

    assert "restored the routing grid" in out
    assert s2.routing_grid is not None
    for lid in (4, 5):
        assert _pattern_sig(s1.routing_grid, lid) == _pattern_sig(s2.routing_grid, lid)
    # The override and the zone, not just the global patterns.
    assert (s1.routing_grid.get_layer_grid(4).effective_pattern_at(50, 50).unit_pitch()
            == s2.routing_grid.get_layer_grid(4).effective_pattern_at(50, 50).unit_pitch())
    assert len(s2.fp.get_keepout_zones()) == len(s1.fp.get_keepout_zones()) == 1


def test_the_session_that_declares_wins_over_the_stored_one(tmp_path):
    """Precedence is a rule about WHO declared a value, which is why `source`
    is stored — a pattern alone cannot say."""
    db = str(tmp_path / "g.bdb")
    setup = ["def_layer 4 M4 H LOW 30"]
    _session(setup + [f"open_bdb {db}", "def_track_pattern 4 0 _ 1 1"])
    # Re-declared AFTER the open: the flow's own line must win, and must not
    # be refused as a duplicate of what the checkpoint handed back.
    s, out = _session(setup + [f"open_bdb {db}",
                               "def_track_pattern 4 0 _ 4 4"])
    assert "duplicate def_track_pattern" not in out
    assert s.routing_grid.get_layer_grid(4).global_pattern().unit_pitch() == 8.0


def test_re_running_a_flow_does_not_grow_the_tables(tmp_path):
    """Keyed by geometry, so the second run upserts.  A DEF import declares
    ~13k zones; accumulating a set per run would be a table that only grows."""
    db = str(tmp_path / "g.bdb")
    cmds = ["def_layer 4 M4 H LOW 30", f"open_bdb {db}",
            "def_track_pattern 4 0 _ 1 1", "add_keepout 10 10 50 50 M4"]
    counts = []
    for _ in range(2):
        s, _out = _session(cmds)
        counts.append((len(s.bdb.track_patterns()), len(s.bdb.keepouts())))
    assert counts[0] == counts[1] == (1, 1)


# ── the real thing: a DEF's grid survives a resume ─────────────────────────

@pytest.mark.mid
def test_a_def_imported_grid_and_its_keepouts_survive_a_resume(tmp_path):
    """The acceptance criterion, on the smallest vehicle that exercises the
    path: the grid a DEF supplied is there for a session that never reads the
    DEF — which is every hier stage-resume, since it holds the import."""
    db = str(tmp_path / "chip.bdb")
    stack = ["set_import_scale dbu"]
    build = stack + [
        f"open_bdb {db}",
        f"import_lef_tech {_DEF / 'chip.lef'}",
        f"import_def_lef {_DEF / 'chip.def'} {_DEF / 'chip.lef'}",
    ]
    s1, out1 = _session(build)
    assert s1.routing_grid is not None, "the DEF/LEF gave this session a grid"
    built = {lid: _pattern_sig(s1.routing_grid, lid)
             for lid in sorted(s1._pattern_source)}
    assert built, "no patterns installed — vehicle no longer exercises this"
    n_zones = len(s1.fp.get_keepout_zones())

    # The RESUME: the stack, the checkpoint, and no import at all.
    s2, out2 = _session(stack + [f"open_bdb {db}"])
    assert s2.routing_grid is not None, (
        "a resumed session has no routing grid — `run_detailed_nuts` would "
        "refuse and everything before it would silently re-solve against the "
        "def_layer overhead figure instead of the DEF's own pitch")
    assert {lid: _pattern_sig(s2.routing_grid, lid) for lid in built} == built
    assert len(s2.fp.get_keepout_zones()) == n_zones


@pytest.mark.mid
def test_the_resumed_grid_answers_the_question_dnuts_asks(tmp_path):
    """Not just "a grid exists" — the same TRACKS in the same places.  This is
    the query DetailedNUTS admission is decided by, so if it agrees, the
    resumed session places bits where the checkpointed one did."""
    db = str(tmp_path / "chip.bdb")
    stack = ["set_import_scale dbu"]
    s1, _ = _session(stack + [
        f"open_bdb {db}",
        f"import_lef_tech {_DEF / 'chip.lef'}",
        f"import_def_lef {_DEF / 'chip.def'} {_DEF / 'chip.lef'}",
    ])
    lids = sorted(s1._pattern_source)
    s2, _ = _session(stack + [f"open_bdb {db}"])

    probes = [(0, 0, 20000), (5000, 0, 40000), (1000, 10000, 30000)]
    for lid in lids:
        g1 = s1.routing_grid.get_layer_grid(lid)
        g2 = s2.routing_grid.get_layer_grid(lid)
        for x, lo, hi in probes:
            a = [(round(pos, 6), slot.type)
                 for pos, slot in g1.signal_tracks_in(x, lo, hi)]
            b = [(round(pos, 6), slot.type)
                 for pos, slot in g2.signal_tracks_in(x, lo, hi)]
            assert a == b, f"layer {lid} probe {(x, lo, hi)}: {len(a)} vs {len(b)}"


# ── the derived layer facts, in every declaration order ────────────────────

_PAT = "def_track_pattern 4 0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1"


@pytest.mark.parametrize("order, why", [
    (["def_layer 4 M4 H LOW 30", "OPEN"], "the stack first"),
    (["OPEN", "def_layer 4 M4 H LOW 30"], "the checkpoint first"),
])
def test_the_restored_width_model_does_not_depend_on_declaration_order(
        tmp_path, order, why):
    """A pattern's derived facts (dilution, per-bit pitch, NDR geometry) are
    pushed onto the LayerStack, which has no row for a layer that is declared
    LATER — so with `open_bdb` before `def_layer` they went nowhere and the
    session kept the overhead-derived dilution: the coarser model this whole
    change exists to replace, silently, in exactly the ordering the target
    flow uses (`flow/ariane133/ariane133_heal.buda` opens its BDB well
    before declaring its stack).  Measured then: 40.0 declared, 17.14
    restored.

    `def_layer` now picks up a pattern the restore already installed."""
    db = str(tmp_path / "o.bdb")
    built, _ = _session(["def_layer 4 M4 H LOW 30", f"open_bdb {db}", _PAT])
    declared = built.layers.eff_bus_width(8, 12.0, 4)

    s, _ = _session([f"open_bdb {db}" if c == "OPEN" else c for c in order])
    assert s.layers.eff_bus_width(8, 12.0, 4) == declared, why


def test_the_bit_pitch_is_per_signal_slot_not_per_period(tmp_path):
    """One derivation, four call sites.  Two of them passed `unit_pitch`
    whole where the rule is `unit_pitch / n_signal_slots` — invisible on the
    single-signal-slot patterns LEF and DEF synthesize, and wrong by the
    signal-slot count on any hand-declared one (this pattern has 2 of 4, so
    the restored bus came out 80 wide against 40 declared)."""
    db = str(tmp_path / "o.bdb")
    s1, _ = _session(["def_layer 4 M4 H LOW 30", f"open_bdb {db}", _PAT])
    s2, _ = _session([f"open_bdb {db}", "def_layer 4 M4 H LOW 30"])
    # unit_pitch 10 over 2 signal slots = 5 per bit; 8 bits = 40.
    assert s1.layers.eff_bus_width(8, 12.0, 4) == 40.0
    assert s2.layers.eff_bus_width(8, 12.0, 4) == 40.0


# ── the pre-v29 warning is detected from the data ──────────────────────────

def test_a_gridless_routed_checkpoint_is_reported(tmp_path):
    """BUDA-1503 keys on the DATA, not the schema version: opening a BDB
    MIGRATES it, so `schema_version()` already reads the current one by the
    time anything can ask, and a version test could never fire (Codex P2 on
    #782).  Simulated here by dropping the grid rows a v29 build wrote —
    which is precisely the state a pre-v29 checkpoint is in."""
    db = str(tmp_path / "r.bdb")
    s1, _ = _session([
        "def_layer 4 M4 H LOW 30", "def_layer 5 M5 V LOW 30", f"open_bdb {db}",
        _PAT, "def_track_pattern 5 0 _ 1 1",
        "add_block a 0 0 40 40", "add_block b 200 0 240 40",
        "add_bus d[4] a.tx b.rx",
        "run_bundler", "generate_topologies", "run_planner 1", "run_nuts",
        "run_detailed_nuts", "save_bdb",
    ])
    assert s1.bdb.net_segments("1"), "vehicle must be detail-routed"
    s1.bdb.clear_track_patterns()          # what a pre-v29 checkpoint holds
    del s1

    s2, out = _session(["def_layer 4 M4 H LOW 30", "def_layer 5 M5 V LOW 30",
                        "add_block a 0 0 40 40", "add_block b 200 0 240 40",
                        f"open_bdb {db}", "load_pipeline"])
    assert "BUDA-1503" in out
    assert "def_track_pattern" in out       # the remedy


def test_a_design_that_never_had_a_grid_is_not_accused(tmp_path):
    """The condition has to be exact: a flow with no track patterns routed
    without one legitimately, so restoring it must say nothing."""
    db = str(tmp_path / "n.bdb")
    _session(["def_layer 4 M4 H LOW 30", "def_layer 5 M5 V LOW 30",
              f"open_bdb {db}", "add_block a 0 0 40 40",
              "add_block b 200 0 240 40", "add_bus d[4] a.tx b.rx",
              "run_bundler", "generate_topologies", "run_planner 1",
              "run_nuts", "save_bdb"])
    _s, out = _session(["def_layer 4 M4 H LOW 30", "def_layer 5 M5 V LOW 30",
                        "add_block a 0 0 40 40", "add_block b 200 0 240 40",
                        f"open_bdb {db}", "load_pipeline"])
    assert "BUDA-1503" not in out
    assert "rehydrated" in out            # it really did restore
