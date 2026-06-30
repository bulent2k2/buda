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

"""`run_planner ... signal_tracks` — opt-in signal-track band capacity (Gap A part 2).

The planner's default band capacity is geometric layout width; DetailedNUTS places
bits on discrete SIGNAL tracks, so a band whose width fit but whose integer
signal-track count is short of the bit count becomes a silent DNUTS open. With the
`signal_tracks` keyword the planner charges capacity in signal-track count (× bit
pitch), so the shortfall surfaces as overflow at planning time and the planner
avoids it — fewer opens up front. The keyword is opt-in: without it the plan is
byte-identical to before.
"""
import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

_ROOT = Path(__file__).parents[2]
_RNR = _ROOT / "flow" / "rnr"


def _small_session(with_pattern=True):
    """A tiny flat 8-bit bus on a single H/V stack, optionally with track patterns."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
    ]
    if with_pattern:
        cmds += [
            "def_track_pattern 4 0.0  POWER 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  GROUND 2 1",
            "def_track_pattern 5 0.0  POWER 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  GROUND 2 1",
        ]
    cmds += [
        "add_block A 0 0 200 400",
        "add_block B 1000 0 1200 400",
        "add_bus d[8] A.p B.p",
        "run_bundler",
        "generate_topologies",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


# --- opt-in / banner / safety -----------------------------------------------

def test_signal_tracks_prints_banner_and_keyword_is_opt_in():
    s = _small_session(with_pattern=True)
    out_default = _run(s, "run_planner 5")
    assert "signal-track mode" not in out_default, out_default
    assert "min_band_cap=" in out_default                    # width units by default

    out_st = _run(s, "run_planner 5 signal_tracks")
    assert "signal-track mode" in out_st, out_st
    assert "min_signal_tracks=" in out_st                    # track units when enabled
    # The plan is still usable: NUTS places the selected topologies.
    _run(s, "run_nuts")
    assert s.nuts_result is not None


def test_signal_tracks_flat_no_iteration_arg_parses():
    """`run_planner signal_tracks` (keyword only, no iteration count) must parse —
    the iteration count defaults to 5, not int('signal_tracks')."""
    s = _small_session(with_pattern=True)
    out = _run(s, "run_planner signal_tracks")
    assert "signal-track mode" in out, out
    assert s.planner is not None


def test_signal_tracks_requires_track_pattern():
    """Without any def_track_pattern the keyword warns and falls back to width."""
    s = _small_session(with_pattern=False)
    out = _run(s, "run_planner 5 signal_tracks")
    assert "requires a routing grid" in out, out
    assert "signal-track mode" not in out, out               # fell back to width
    # Still produced a usable plan.
    _run(s, "run_nuts")
    assert s.nuts_result is not None


# --- the real-world effect (@mid) -------------------------------------------

def _mix_to_dnuts(planner_cmd):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {_RNR / 'mix_tracks.buda'}")
        s.do_command(f"open_bdb {_RNR / 'mix.b_db'}")
        s.do_command("derive_busterms 2")
        s.do_command("add_blocks_from_bdb 0")
        s.do_command("add_blocks_from_bdb 1 skip")
        s.do_command("add_blocks_from_bdb 2 skip")
        s.do_command("run_hier_bundler depth 2")
        s.do_command("generate_hier_topologies")
        s.do_command(planner_cmd)
        s.do_command("run_nuts")
        s.do_command("run_detailed_nuts")
    return s


@pytest.mark.mid
def test_signal_tracks_reduces_opens_on_mix_repro():
    """On flow/rnr/mix.buda the signal-track planner avoids capacity-short bands the
    width model misses, so DNUTS opens drop with no ripup (validated 236 -> 162),
    while the default width plan is unchanged."""
    base = _mix_to_dnuts("run_planner hier 5")
    assert base.detailed_result.num_unplaced == 236, "width baseline drifted from 236"

    st = _mix_to_dnuts("run_planner hier 5 signal_tracks")
    assert st._planner_is_hier
    assert st.detailed_result.num_unplaced < base.detailed_result.num_unplaced, \
        f"signal_tracks {st.detailed_result.num_unplaced} not < width {base.detailed_result.num_unplaced}"
