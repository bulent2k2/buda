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
import re
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


def _bundle_overflow(planner_out):
    """Parse the per-bundle overflow the planner reports on its `-> topo` line
    (e.g. `... [H→M4]  overflow=12`). Returns the max over all bundle lines."""
    ovs = [int(m) for m in re.findall(r"overflow=(\d+)", planner_out)]
    assert ovs, f"no bundle overflow line in planner output:\n{planner_out}"
    return max(ovs)


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


def test_signal_tracks_without_track_pattern_is_a_hard_error():
    """Requesting signal_tracks with no def_track_pattern is a hard error (exit 1),
    NOT a silent fall-back to the width model — planning quietly with a different
    capacity model than the one asked for would hide that the signal-track
    accounting never happened."""
    s = _small_session(with_pattern=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with pytest.raises(SystemExit) as ei:
            s.do_command("run_planner 5 signal_tracks")
    assert ei.value.code == 1
    out = buf.getvalue()
    assert "no def_track_pattern is defined" in out, out
    assert "signal-track mode" not in out, out               # never enabled the mode
    # It bailed out rather than silently planning with the width model.
    assert s.planner is None or s.nuts_result is None


# --- the mechanism, deterministically (CPU-invariant) -----------------------

def _starved_band_session(mode_cmd):
    """A tiny 12-bit H bus whose M4 band is starved to a fixed, INTEGER signal-track
    count via `add_grid_override` (POWER-heavy, one signal per 42-unit unit → ~8
    signal tracks over the 400-unit band). The bus is 12 bits — WIDER than the
    band's signal tracks but far inside its geometric length.

    This is the exact case the feature exists for: the width model charges band
    capacity as geometric length (blind to the override — the planner never sees
    the RoutingGridStack in width mode), so it reports `overflow=0` and would open
    the surplus bits only at DetailedNUTS. The signal-track model reads the actual
    grid and prices the shortfall as planner overflow up front. Both quantities are
    exact integers (track count, bit count), so unlike an end-to-end open-count
    delta this is invariant across CPUs / `-march=native` rounding."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    rich = "POWER 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  GROUND 2 1"
    setup = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
        f"def_track_pattern 4 0.0  {rich}",
        f"def_track_pattern 5 0.0  {rich}",
        # Starve M4's signal supply over the whole bus band (width model is blind).
        "add_grid_override 4 0 0 1200 400 0.0  POWER 40 2  SIGNAL 1 2",
        "add_block A 0 0 200 400",
        "add_block B 1000 0 1200 400",
        "add_bus d[12] A.p B.p",
        "run_bundler",
        "generate_topologies",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in setup:
            s.do_command(c)
    return s, _run(s, mode_cmd)


def test_signal_tracks_surfaces_shortfall_the_width_model_calls_clean():
    """The core contract, asserted deterministically: on a band whose geometric
    width fits but whose integer signal-track count is short of the bit count, the
    default width planner commits `overflow=0` (a silent shortfall that only
    DetailedNUTS would discover) while `signal_tracks` prices it as planner
    overflow up front, so the escalation ladder can act on it.

    Integer track-count vs bit-count → no float sensitivity, so this replaces the
    machine-sensitive end-to-end open-count comparison that used to live in the
    mix repro (see that test's note)."""
    _, width_out = _starved_band_session("run_planner 5")
    assert "signal-track mode" not in width_out                  # opt-in: width by default
    assert _bundle_overflow(width_out) == 0, \
        f"width model should be blind to the track shortfall:\n{width_out}"

    _, st_out = _starved_band_session("run_planner 5 signal_tracks")
    assert "signal-track mode" in st_out, st_out
    assert _bundle_overflow(st_out) > 0, \
        f"signal_tracks should surface the track shortfall as overflow:\n{st_out}"
    # With no escape layer the ladder can't clear it, so the shortfall is reported
    # LOUD (an overflow WARNING) rather than committed silently.
    assert any("WARNING" in ln and "overflow=" in ln for ln in st_out.splitlines()), st_out


# --- real-fixture integration (@mid) ----------------------------------------

def _mix_to_dnuts(planner_cmd):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {_RNR / 'mix_tracks.buda'}")
        s.do_command(f"open_bdb {_RNR / 'mix.bdb.sql'}")
        s.do_command("derive_busterms 2")
        s.do_command("add_blocks_from_bdb 0")
        s.do_command("add_blocks_from_bdb 1 skip")
        s.do_command("add_blocks_from_bdb 2 skip")
        s.do_command("run_hier_bundler depth 2")
        # Midpoint-only pool: this test measures the signal-track capacity
        # model against the fixture its 156->128 numbers were collected on.
        # Under the hanan_loci default-on pool the mix repro shifts (width
        # 300 / signal_tracks 332 — the benefit inverts on this corpus; part
        # of the mix default-on regression recorded in wishlist-topo piece
        # (a)), so the generation corpus is pinned pre-flip via the opt-out.
        s.do_command("generate_hier_topologies no_hanan_loci")
        s.do_command(planner_cmd)
        s.do_command("run_nuts")
        s.do_command("run_detailed_nuts")
    return s


@pytest.mark.mid
def test_signal_tracks_completes_on_mix_repro():
    """`run_planner hier ... signal_tracks` drives the whole hier flow to
    DetailedNUTS on a real fixture (flow/rnr/mix): the mode engages, the plan is
    complete and usable, and the width baseline has real capacity-short bands to
    model (nonzero opens). This is the integration smoke — the deterministic proof
    that signal_tracks does the RIGHT thing (surfaces a shortfall the width model
    misses) lives in test_signal_tracks_surfaces_shortfall_the_width_model_calls_clean,
    which is CPU-invariant.

    Note (issue #441): the QoR *benefit* (signal_tracks avoids capacity-short
    bands, historically 156 -> 128 DNUTS opens on the reference host) is a
    machine-sensitive metric — the double-based planner/NUTS math rounds
    differently across CPUs under -march=native, and the per-band gain is a sub-1
    signal-track quantization term that only sums to a robust win statistically
    across a large design. An earlier revision asserted `st_opens < width_opens`
    directly here; the margin has since compressed into the cross-CPU noise floor
    (topology-generation churn moved it), so that strict inequality failed on other
    hosts. Measure the benefit same-machine with tools/qor_corpus.py instead — that
    is what a QoR delta belongs in, not a cross-machine unit assertion."""
    st = _mix_to_dnuts("run_planner hier 5 signal_tracks")
    assert st._planner_is_hier
    assert st.detailed_result is not None                        # DNUTS completed
    # There is a real shortfall on this fixture for the mode to act on.
    base = _mix_to_dnuts("run_planner hier 5")
    assert base.detailed_result.num_unplaced > 0, "expected a nonzero width-plan open baseline"
