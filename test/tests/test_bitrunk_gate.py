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

"""Legacy BITRUNK anchoring gate (filter_unanchored_bitrunk).

A legacy BITRUNK_H/BITRUNK_V whose endpoint blocks are ALL untapped covers every
block by a free-sliding trunk graze: it passes check_topo at nominal but opens
every bit at DetailedNUTS (bigHalf bus_038 — a 0-overlap/0-unplaced route that is
electrically open at every endpoint).  The generation gate drops such a
fully-degenerate candidate when a clean alternative survives, so the planner
falls to an anchored shape and the bundle routes cleanly.
"""
import contextlib
import io

import pytest

# Full-pipeline integration over a real flow file → mid tier.
pytestmark = pytest.mark.mid

import buda
import buda_cli


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _check_design_out(session):
    """Run check_design and return its printed report (the user-facing verdict:
    'Success: no violations found.' vs 'Total: N violation(s) ...')."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        session.do_command("check_design")
    return buf.getvalue()


# bus_038 in tc3a_flat_5x is a 56-bit fan-out (blk_04 -> blk_30, blk_26,
# io_pad_bl) whose legacy BITRUNK_H "covers" all four endpoints by a free-sliding
# H-trunk graze (tapped={}); before the gate it routed to 168 = 3x56 per-bit
# BUSTERM_OPENs at DNUTS with 0 NUTS overlaps / 0 unplaced bits.
_REPRO_CMDS = (
    "source ../tracks/tracks4top.buda",
    "source tc3a_flat_5x.buda",
    "run_bundler",
    "generate_topologies_for_bundle bus_038 no_hanan_loci",
)


def test_bus038_routes_clean_after_gate(monkeypatch):
    monkeypatch.chdir("flow/big_data_test")
    s = _session()
    _run(s, *_REPRO_CMDS, "run_planner", "run_nuts", "run_detailed_nuts")
    assert s.detailed_result is not None
    assert s.detailed_result.num_unplaced == 0          # placed...
    out = _check_design_out(s)
    assert "no violations" in out, out                  # ...AND electrically complete


def test_degenerate_bitrunk_h_dropped_from_pool(monkeypatch):
    # No fully-unanchored legacy BITRUNK survives generation (every endpoint block
    # covered only by a trunk graze, no busterm tap anywhere).
    monkeypatch.chdir("flow/big_data_test")
    s = _session()
    _run(s, *_REPRO_CMDS)
    w = next(b for b in s.bundles
             if b.input.original_bundle.get_net_names()[0].startswith("bus_038"))
    fp = s._make_topo_fp_resolver()(w)
    for c in w.input.candidates:
        if c.type not in ("BITRUNK_H", "BITRUNK_V"):
            continue
        ct = buda.ConnTopology(); ct.build(c, fp)
        tapped = {cn.block_name for cs in ct.segs() for cn in cs.conns
                  if cn.kind == buda.SegConn.BUSTERM}
        assert tapped, \
            f"a fully-unanchored {c.type} survived the gate (tapped set empty)"
