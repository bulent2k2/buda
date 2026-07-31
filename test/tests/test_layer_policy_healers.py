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

"""Layer-policy healer compliance + advisories, Phase 4
(docs/internal/hier_layer_caps.md §13).

The deliverable: healers never violate a cap or exceed a share, and
violations are impossible to MISS by audit — the check_design LAYER_CAP /
LAYER_SHARE advisory reports in-effect metal against every governed
bundle's band (unpinned out-of-band = LOUD, should-be-impossible; pinned
exceptions surfaced as the documented override), `dump_hbundles` shows each
governed bundle's band/shares, and the planner's escalation warnings name
the cell band so a small layer set explains itself.  No policy anywhere =
completely silent (corpus-guarded byte-identity).
"""
import contextlib
import io
import sys
from pathlib import Path

import buda

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402


def _cmd(s, line):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(line)
    return buf.getvalue()


def _quiet(s, *lines):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in lines:
            s.do_command(c)


def _flat_session(nbits=8):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "def_layer 2 M2 H LOW 50", "def_layer 3 M3 V LOW 50",
           "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
           "add_block A 0 1000 200 1400",
           "add_block B 2400 1000 2600 1400",
           f"add_bus x[{nbits}] A.p B.p",
           "run_bundler", "generate_topologies")
    return s


# ── the LAYER_CAP advisory ───────────────────────────────────────────────────

def test_advisory_silent_on_clean_capped_plan():
    """A governed plan inside its band: check_design prints Success with no
    policy lines — the advisory only speaks when there is something to
    say."""
    s = _flat_session()
    w = s.bundles[0]
    w.input.allowed_layers = [2, 3]
    w.input.layer_cap = 3
    _quiet(s, "run_planner")
    out = _cmd(s, "check_design topo")
    assert "Success" in out
    assert "LAYER_CAP" not in out and "pinned above-cap" not in out


def test_advisory_silent_without_policy():
    """No policy in the session: the advisory adds NOTHING to check_design
    output (the print-silence half of the byte-identity guarantee)."""
    s = _flat_session()
    _quiet(s, "run_planner")
    out = _cmd(s, "check_design topo")
    assert "LAYER_CAP" not in out and "cell band" not in out


def test_advisory_reports_pinned_exception():
    """A pinned above-cap layer routes (pins override the mask) and the
    advisory surfaces it as the documented exception — visible, never
    silent, and NOT counted as an impossible violation."""
    s = _flat_session()
    w = s.bundles[0]
    w.input.allowed_layers = [2, 3]
    w.input.layer_cap = 3
    n_segs = len(w.input.candidates[0].segments)
    pins = [-1] * n_segs
    pins[0] = 4 if (w.input.candidates[0].segments[0].start.y ==
                    w.input.candidates[0].segments[0].end.y) else 5
    w.input.pinned_seg_layers = pins
    _quiet(s, "select_topology 1 1", "run_planner")
    out = _cmd(s, "check_design topo")
    assert "pinned above-cap exception(s) honored" in out
    assert "outside the cell band [min..3]" in out
    assert "NO pin" not in out           # not the impossible-violation path


def test_advisory_flags_unpinned_violation():
    """Metal outside the band with no pin should be impossible (the mask is
    enforced everywhere) — when constructed by hand, the advisory reports
    it LOUD with the please-report warning."""
    s = _flat_session()
    _quiet(s, "run_planner")             # unmasked plan lands on M4/M5
    w = s.bundles[0]
    assert any(l in (4, 5) for l in w.plan.seg_layers)
    w.input.allowed_layers = [2, 3]      # mask applied AFTER the fact
    w.input.layer_cap = 3
    out = _cmd(s, "check_design topo")
    assert "LAYER_CAP: Bundle" in out
    assert "NO pin" in out and "should make this impossible" in out


def test_advisory_stage_aware_reads_placed_metal():
    """nuts/dnuts stages judge the PLACED TrackSegments, so a post-plan
    escalation is audited by where the metal actually ended up."""
    s = _flat_session()
    w = s.bundles[0]
    w.input.allowed_layers = [2, 3]
    w.input.layer_cap = 3
    _quiet(s, "run_planner", "run_nuts")
    out = _cmd(s, "check_design nuts")
    assert "LAYER_CAP" not in out        # placed metal stayed in-band
    for ts in s.nuts_result.segments:
        assert ts.layer in (2, 3)


# ── policy-aware reporting ───────────────────────────────────────────────────

def _hier_policy_session(path=":memory:"):
    db = buda.BDB(path)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 520, 0)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
    buda.BustermGen(db).derive(1)
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    _quiet(s,
           "def_layer 2 M2 H 50", "def_layer 3 M3 V 50",
           "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
           "def_track_pattern 2 0 _ 1 5", "def_track_pattern 3 0 _ 1 5",
           # M4: 4 signal slots/period so a 50% share keeps 2 (a 1-slot
           # pattern would floor 50% to zero and error at declaration).
           "def_track_pattern 4 0 _ 1 1 _ 1 1 _ 1 1 _ 1 1",
           "def_track_pattern 5 0 _ 1 5",
           "set_cell_layer_cap proc_cell M3",
           "set_cell_layer_share proc_cell M4 50",
           "run_hier_bundler", "generate_hier_topologies")
    return s


def test_dump_hbundles_shows_band_and_shares():
    s = _hier_policy_session()
    _quiet(s, "run_planner hier")
    out = _cmd(s, "dump_hbundles expanded")
    assert "band=[min..3]" in out
    assert "shares={L4:50%}" in out


def test_ladder_warning_names_the_band():
    """A governed bundle escalated past STRICT names its band in the
    WARNING, so the small layer set explains itself (§9.2).  512 bits over
    the LOW pair overflow every in-band choice deterministically."""
    s = _flat_session(nbits=512)
    w = s.bundles[0]
    w.input.allowed_layers = [2, 3]
    w.input.layer_cap = 3
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("run_planner")
    out = buf.getvalue()
    assert "WARNING" in out and "[cell band [min..3]]" in out


# ── healer compliance over the full policy vector (the deliverable) ──────────

def test_healers_respect_caps_and_shares_end_to_end():
    """Capped + shared bottom-up flow through BOTH healer stages: after
    negotiate_congestion + ripup_reroute at stage a and b, every governed
    bundle's placed metal is inside its band (or on a leased shared
    layer), the DNUTS bits comply, and the advisory reports nothing —
    healers never violate a cap or exceed a share."""
    s = _hier_policy_session()
    _quiet(s, "run_planner hier", "run_nuts",
           "negotiate_congestion", "ripup_reroute",
           "run_detailed_nuts",
           "negotiate_congestion", "ripup_reroute")
    governed = [w for w in s.bundles if w.input.allowed_layers]
    assert governed
    allowed = {2, 3, 4}                  # band [min..3] + leased M4
    for w in governed:
        for l in w.plan.seg_layers:
            assert l in allowed, (w.input.original_bundle.id, l)
    gids = {w.input.original_bundle.id for w in governed}
    for ts in s.nuts_result.segments:
        if ts.bundle_id in gids:
            assert ts.layer in allowed
    for ns in s.detailed_result.net_segments:
        if ns.bundle_id in gids:
            assert ns.layer in allowed
    unpinned, pinned_ex, share_over = s._layer_policy_advisories("dnuts")
    assert unpinned == [] and share_over == 0
    out = _cmd(s, "check_design")
    assert "NO pin" not in out


def test_flat_ripup_respects_the_mask():
    """The flat healer path: masked bundles stay in-band through
    ripup_reroute (index alternates + global pass replan through the same
    plan_bundle chokepoint)."""
    s = _flat_session()
    for w in s.bundles:
        w.input.allowed_layers = [2, 3]
        w.input.layer_cap = 3
    _quiet(s, "run_planner", "run_nuts", "ripup_reroute")
    for w in s.bundles:
        assert all(l in (2, 3) for l in w.plan.seg_layers)
    unpinned, _pins, _so = s._layer_policy_advisories("nuts")
    assert unpinned == []


# ── Codex #549 round: share-only audit scope, pre-planner dump ───────────────

def test_advisory_audits_share_only_cells():
    """Codex #549: a cell with shares but NO band leaves allowed_layers
    empty by design — the advisory must still re-audit its collective
    lease (a pinned non-STRICT commit past a share-only cell's budget was
    silently missed by the mask-only filter)."""
    s = _flat_session()
    w = s.bundles[0]
    eff = s.layers.eff_bus_width(8, w.input.width, 4)
    w.input.layer_shares = [(4, 0.5)]          # share-only: no mask
    w.input.share_group = "leaf@i0"
    w.input.share_budgets = [(4, 0.5 * eff)]   # less than one commit
    n_segs = len(w.input.candidates[0].segments)
    pins = []
    for si in range(n_segs):
        seg = w.input.candidates[0].segments[si]
        pins.append(4 if seg.start.y == seg.end.y else 5)
    w.input.topology_pinned = True
    w.plan.selected_topology_index = 0
    w.input.pinned_seg_layers = pins
    _quiet(s, "run_planner")
    unpinned, pinned_ex, share_over = s._layer_policy_advisories("topo")
    assert share_over >= 1                     # the lease excess is seen
    assert unpinned == []                      # no band => no band check
    out = _cmd(s, "check_design topo")
    assert "over their collective lease" in out


def test_dump_hbundles_annotates_before_planner():
    """Codex #549: the dump is documented as usable right after the
    bundler — the band/shares annotation must appear on a PRE-planner
    dump, not only after run_planner hier resolved the masks."""
    s = _hier_policy_session()
    out = _cmd(s, "dump_hbundles")             # no run_planner hier yet
    assert "band=[min..3]" in out
    assert "shares={L4:50%}" in out
