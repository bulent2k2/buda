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

"""The unit-plausibility guard (Phase 1d of docs/internal/engine_units.md).

The gap it closes: the engine is unit-agnostic, so block coordinates on one
scale and track patterns on another do not crash — they produce a plan in
which every bus reserves a wrong fraction of the space it needs, and report
it as feasible.  The guard turns that from a silently optimistic plan into a
stop.

What has to hold: it must fire on a real mismatch, must NOT fire on any
design the corpus actually contains, and must reach the terminal — a stop
whose reason lives only in the flow log is a stop the user cannot act on.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from buda_session.util import (UNIT_TRACKS_MAX, UNIT_TRACKS_MIN,  # noqa: E402
                               unit_plausibility_faults)

_ROOT = Path(__file__).parents[2]
_CLI = _ROOT / "src" / "buda_cli.py"

_HEAD = """def_layer 4 M4 H TOP 30
def_layer 5 M5 V TOP 30
"""
_TAIL = """add_net n1 a.o b.i
run_bundler STRICT
generate_topologies
run_planner 1
"""


def _design(block_scale, pitch_slot):
    """A two-block design at `block_scale` with a `pitch_slot`-wide track
    pattern.  Consistent scales route; mixed ones are the fault case."""
    s = block_scale
    pat = (f"VDD {pitch_slot} {pitch_slot} _ {pitch_slot} {pitch_slot} "
           f"_ {pitch_slot} {pitch_slot} GND {pitch_slot} {pitch_slot}")
    return (_HEAD
            + f"def_track_pattern 4 0 {pat}\n"
            + f"def_track_pattern 5 0 {pat}\n"
            + f"add_block a 0 0 {s} {s}\n"
            + f"add_block b {200 * s} {200 * s} {201 * s} {201 * s}\n"
            + _TAIL)


def _run(tmp_path, text, name="t.buda"):
    p = tmp_path / name
    p.write_text(text)
    return subprocess.run([sys.executable, str(_CLI), "--no-viz", str(p)],
                          capture_output=True, text=True, cwd=str(tmp_path))


# ── the predicate, without a session ───────────────────────────────────────

def test_bounds_bracket_every_design_the_tree_contains():
    """Measured 2026-08 with `BUDA_UNIT_SIGNAL`: the tree's flows span 3.66
    (flow/four_blocks M7, a 150-unit toy on a 41-unit pitch) to 797.2
    (chip/chip_topdown M2).  The bounds must sit outside that with room to
    spare, or the guard becomes a tax on legitimate runs.

    The 3.66 end is the load-bearing one: the first calibration used the QoR
    corpus alone (minimum 24.4), set the bound at 4, and broke six unit-test
    fixtures — the corpus is not the same population as everything the repo
    runs."""
    assert UNIT_TRACKS_MIN < 3.6
    assert UNIT_TRACKS_MAX > 8.0e5      # past the widest reticle / finest pitch
    assert not unit_plausibility_faults([(7, 41.0, 3.66), (5, 18.0, 797.22)])


def test_predicate_names_the_side_it_fell_off():
    faults = unit_plausibility_faults(
        [(4, 1e-3, 1e9), (5, 1e6, 0.01), (6, 18.0, 100.0)])
    assert [(lid, side) for lid, _up, _n, side in faults] == \
        [(4, "high"), (5, "low")]


def test_the_physical_pitch_check_needs_a_declared_scale():
    """The ratio cannot separate a mis-scaled small design from a legitimate
    huge one — both read the same.  A DECLARED import scale supplies the
    missing anchor: `unit_pitch / lu_per_um` is then a pitch in real microns.

    But only a declaration licenses it.  At the default scale of 1.0 the
    corpus's 18-unit pitches would be "18 µm", which is a fine number for a
    synthetic design and an absurd one for real metal — judging it would fail
    every flow in the tree over a claim nobody made."""
    corpus_like = [(4, 18.0, 100.0)]
    assert not unit_plausibility_faults(corpus_like)              # scale 1.0
    assert not unit_plausibility_faults(corpus_like, 1.0)

    # Declared 2000 lu/µm: an 18-unit pitch is 0.009 µm — fine (9 nm is below
    # production but inside the deliberately generous bound), while the
    # half-converted case (a micron-scale pattern against DBU coordinates) is
    # 0.0005 µm and fails.
    assert not unit_plausibility_faults([(4, 18.0, 4e4)], 2000.0)
    faults = unit_plausibility_faults([(4, 1.0, 7.2e5)], 2000.0)
    assert [f[3] for f in faults] == ["pitch_lo"]
    # …and the mirror: a DBU-scale pattern against micron coordinates.
    assert [f[3] for f in unit_plausibility_faults([(4, 2e6, 30.0)], 2000.0)] \
        == ["pitch_hi"]


# ── end to end ─────────────────────────────────────────────────────────────

def test_consistent_scales_run_clean(tmp_path):
    """Both the small and the large consistent design must pass: the guard
    judges the RATIO, so it must be blind to which unit was chosen."""
    for scale, slot in ((10, 1), (10_000, 1_000)):
        r = _run(tmp_path, _design(scale, slot))
        assert r.returncode == 0, (scale, slot, r.stdout[-1500:])
        assert "UnitCheck" not in r.stdout


def test_mixed_scales_stop_the_run_with_the_reason_on_the_terminal(tmp_path):
    """Blocks in DBU-magnitude coordinates against a micron-magnitude track
    pattern — the exact shape a half-converted import produces."""
    r = _run(tmp_path, _design(20_000, 0.03))
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "implausible design scale" in out, out[-1500:]
    assert "different units" in out
    assert "track pitch" in out          # the numbers, not just a verdict
    assert "set_unit_check" in out       # and the way out


def test_warn_reports_without_stopping(tmp_path):
    r = _run(tmp_path, "set_unit_check warn\n" + _design(20_000, 0.03))
    assert r.returncode == 0, r.stdout[-1500:]


def test_off_is_silent(tmp_path):
    r = _run(tmp_path, "set_unit_check off\n" + _design(20_000, 0.03))
    assert r.returncode == 0, r.stdout[-1500:]
    assert "UnitCheck" not in r.stdout + r.stderr


def test_a_flow_with_no_track_pattern_is_never_judged(tmp_path):
    """There is no second scale to disagree with, so there is nothing to
    check — and inventing a verdict from the coordinates alone would be the
    calibration this signal exists to avoid."""
    r = _run(tmp_path, _HEAD + "add_block a 0 0 20000 20000\n"
             + "add_block b 4000000 4000000 4020000 4020000\n" + _TAIL)
    assert r.returncode == 0, r.stdout[-1500:]
    assert "UnitCheck" not in r.stdout


def test_track_pitch_auto_derives_the_gap_from_the_grid(tmp_path):
    """The inter-bus gap is the one physical default the grid does not
    already supply, so it is the one literal that silently means "one micron"
    on a micron design.  `auto` derives it: the pattern below is 4 slots of
    unit pitch 20 with 2 signals, so one signal-track pitch is 10."""
    pat = "VDD 2 1 _ 4 1 _ 4 1 GND 2 5"     # unit_pitch 20, 2 signal slots
    r = _run(tmp_path, _HEAD
             + f"def_track_pattern 4 0 {pat}\ndef_track_pattern 5 0 {pat}\n"
             + "set_track_pitch auto\n"
             + "add_block a 0 0 100 100\nadd_block b 2000 2000 2100 2100\n"
             + _TAIL + "run_nuts\n")
    assert r.returncode == 0, r.stdout[-1500:]
    log = (tmp_path / "log" / "t_flow.log").read_text(errors="replace")
    assert "set_track_pitch auto → 10 layout units" in log, log[-1500:]


def test_track_pitch_auto_refuses_rather_than_returning_the_literal(tmp_path):
    """With no pattern the shared helper falls back to the NUTS pitch — which
    here IS the literal `auto` exists to replace.  Returning it would report a
    derived value that was never derived."""
    r = _run(tmp_path, _HEAD + "set_track_pitch auto\n")
    assert r.returncode == 0            # a bad argument is not a crash
    log = (tmp_path / "log" / "t_flow.log").read_text(errors="replace")
    assert "needs a track pattern" in log, log[-1500:]


def test_a_pattern_added_after_the_first_check_re_arms_it(tmp_path):
    """"Once per session" has to mean "once per unchanged grid".  A plain
    done-flag set at `run_planner` permanently retires the `run_nuts` hook, so
    a design with a fine M4 pattern before planning and a wrong-scale M5 after
    it would be judged on M4 alone and pass — and declaring patterns late is
    exactly the flow shape the second hook exists for."""
    ok = "VDD 1 1 _ 1 1 _ 1 1 GND 1 1"          # unit pitch 8, ~250 across
    bad = "VDD 1e-7 1e-7 _ 1e-7 1e-7"           # ~2.5e9 across — implausible
    r = _run(tmp_path, _HEAD
             + f"def_track_pattern 4 0 {ok}\n"
             + "add_block a 0 0 100 100\nadd_block b 1900 1900 2000 2000\n"
             + "add_net n1 a.o b.i\nrun_bundler STRICT\n"
             + "generate_topologies\nrun_planner 1\n"     # M4 alone: fine
             + f"def_track_pattern 5 0 {bad}\n"           # …then a bad M5
             + "run_nuts\n")
    assert r.returncode != 0, r.stdout[-2000:]
    out = r.stdout + r.stderr
    assert "layer 5" in out, out[-1500:]


def test_patterns_declared_after_the_planner_are_still_checked(tmp_path):
    """demo/comprehensive_demo.buda declares its track patterns AFTER
    run_planner, so plan entry alone is not a gate — run_nuts is the second
    hook."""
    late = (_HEAD
            + "add_block a 0 0 20000 20000\n"
            + "add_block b 4000000 4000000 4020000 4020000\n"
            + "add_net n1 a.o b.i\nrun_bundler STRICT\n"
            + "generate_topologies\nrun_planner 1\n"
            + "def_track_pattern 4 0 VDD 0.03 0.03 _ 0.03 0.03\n"
            + "def_track_pattern 5 0 VDD 0.03 0.03 _ 0.03 0.03\n"
            + "run_nuts\n")
    r = _run(tmp_path, late)
    assert r.returncode != 0
    assert "run_nuts" in r.stdout + r.stderr
