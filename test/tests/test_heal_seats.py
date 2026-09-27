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

"""`set_heal_seats`: keepout and supply-doomed seats in the stage-a score.

The stage-a healers (after `run_nuts`) score NUTS overlaps.  A bus segment
seated ON a keepout, or on a seat with fewer signal tracks than its bits,
is a fault the detailed stage pays for and that score cannot see.  With the
knob on each such segment counts as one more overlap, the seated bundles
become contenders, negotiate charges the seat's window, and a measured
layer move lifts seated LOW segments first.
"""
import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
import buda_cli  # noqa: E402

_FLOW = _ROOT / "flow" / "leaf_blockage.buda"
_SOC = _ROOT / "flow" / "tcl" / "soc.tcl"
_POLICY = ("set_leaf_blockage policy", "set_cell_layer_cap B M2")


def _session_at_nuts():
    """The leaf-blockage vehicle WITHOUT its policy, run to abstract NUTS:
    the bus has no way around leaf B and no TOP layer to climb to, so its
    segment is committed onto B's keepout."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for line in _FLOW.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line in _POLICY:
                continue
            if line.startswith("run_detailed_nuts"):
                break
            s.do_command(line)
    return s


def test_the_engine_names_the_segments_it_counts():
    s = _session_at_nuts()
    r = s.nuts_result
    assert r.num_keepout_conflicts == 1
    assert len(r.keepout_seats) == r.num_keepout_conflicts
    (bid, si), = r.keepout_seats
    assert any(ts.bundle_id == bid and ts.seg_idx == si for ts in r.segments)


def test_the_stage_a_score_counts_seats_only_when_asked():
    s = _session_at_nuts()
    faults = s._seat_faults()
    assert (tuple(s.nuts_result.keepout_seats[0])) in faults
    assert s._stage_a_metric() == s.nuts_result.num_overlaps  # knob off
    s.do_command("set_heal_seats on")
    assert s._heal_seats_on()
    assert s._stage_a_metric() == s.nuts_result.num_overlaps + len(faults)
    s.do_command("set_heal_seats off")
    assert s._stage_a_metric() == s.nuts_result.num_overlaps


def test_ripup_lists_a_seated_bundle_as_a_contender():
    s = _session_at_nuts()
    bid = s.nuts_result.keepout_seats[0][0]
    assert bid not in list(s._rr_contention_sources('a'))
    s.do_command("set_heal_seats on")
    assert bid in list(s._rr_contention_sources('a'))


def _soc(*extra):
    r = subprocess.run(["tclsh", str(_SOC), "2", "-LAYOUT", "compact",
                        "-PAD", "10", "-GAP", "4", "-M", "4", "-abstract",
                        *extra], capture_output=True, encoding="utf-8",
                       timeout=600)
    return r.stdout + r.stderr


@pytest.mark.mid
def test_soc_abstract_heals_its_seats():
    """The sweet spot's knobs at NQ 2: the overlaps-only healers end on six
    seat faults; with the seats in the score the layer move takes three off
    before negotiate starts and the run ends clean."""
    before = _soc()
    m = re.search(r"^soc.tcl: FAILED \(abstract\) -- 0 overlaps, (\d+) audit "
                  r"violations, (\d+) seat faults", before, re.M)
    assert m and int(m.group(2)) > 0, before[-2000:]
    after = _soc("-healseats")
    assert "seat layer move:" in after, after[-3000:]
    assert "NUTS overlaps + seat faults" in after, after[-3000:]
    assert re.search(r"^soc.tcl: clean \(abstract\) -- 0 overlaps, 0 audit "
                     r"violations, 0 seat faults", after, re.M), after[-2000:]
