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

"""The stage-b healers counting cross-bundle shorts (issue #962).

The healers' stage-b metric was (DNUTS opens, NUTS overlaps): a short cost
a trial nothing.  On bigHalf the healers reach a clean endpoint through a
state carrying 33 shorts their metric read as 10 opens — so making DNUTS
avoid or remove shorts closed that path and left the flow dirty.  The
healer score leaves placement alone and puts the shorts into what the
healers read instead — ON by default since it measured best, with
BUDA_HEAL_SHORTS=0 turning it off:

  * `_dn_opens` — the one reading of "opens" every stage-b accept uses —
    adds one per short still in the result;
  * the open-segment walks add each shorted segment, so the shorts'
    bundles become contenders and their windows contention sites (and
    negotiate charges them);
  * the parallel sweep's C++ metric counts them too (`count_shorts`), so a
    sweep scores a move exactly as the replay it certifies.

Off, every one of those reads exactly what it read before the flip.
"""
import contextlib
import io
import os

import pytest

import buda
import buda_cli
import buda_session.ripup as ripup_mod

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _session():
    """Two 8-bit buses routed to NUTS — enough bundles and segments for the
    open-segment walks to have something to walk."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 5 M5 V TOP 50",
        "def_layer 4 M4 H TOP 50",
        "def_layer 7 M7 V TOP 50",
        "add_block D1 0 1000 200 1400",
        "add_block D2 400 1000 600 1400",
        "add_block R 2400 1000 2600 1400",
        "add_bus a[8] D1.p R.p",
        "add_bus b[8] D2.p R.p",
        "run_bundler", "generate_topologies", "run_planner", "run_nuts",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _result(s, shorts=(), missing=()):
    """A detailed result with every bit of every selected segment placed,
    except the (bundle, seg, bit)s in `missing`, carrying `shorts` — each a
    ((bundle, seg, bit), (bundle, seg, bit)) pair."""
    r = buda.DetailedNUTSResult()
    rows = []
    for w in s.bundles:
        bid = w.input.original_bundle.id
        nbits = len(w.input.original_bundle.get_net_names())
        topo = w.input.candidates[w.plan.selected_topology_index]
        for si in range(len(topo.segments)):
            for bit in range(nbits):
                if (bid, si, bit) in missing:
                    continue
                ns = buda.NetSegment()
                ns.bundle_id, ns.seg_idx, ns.bit_index = bid, si, bit
                ns.layer, ns.track_position, ns.width = 4, 10.0 * bit, 1.0
                ns.span_lo, ns.span_hi = 0.0, 100.0
                rows.append(ns)
    r.net_segments = rows
    r.num_unplaced = len(missing)
    out = []
    for (a, b) in shorts:
        cs = buda.CrossShort()
        cs.bundle_a, cs.seg_a, cs.bit_a = a
        cs.bundle_b, cs.seg_b, cs.bit_b = b
        cs.layer = 4
        out.append(cs)
    r.cross_shorts = out
    return r


def _bids(s):
    return sorted(w.input.original_bundle.id for w in s.bundles)


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setenv("BUDA_HEAL_SHORTS", "0")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.delenv("BUDA_HEAL_SHORTS", raising=False)   # the default


# ── the default, and the knob off: every read as before the flip ───────────

@pytest.mark.parametrize("value, want", [
    (None, True), ("1", True), ("", True), ("0", False)])
def test_the_healer_score_is_on_by_default(monkeypatch, value, want):
    if value is None:
        monkeypatch.delenv("BUDA_HEAL_SHORTS", raising=False)
    else:
        monkeypatch.setenv("BUDA_HEAL_SHORTS", value)
    assert ripup_mod.RipupMixin._heal_counts_shorts() is want


def test_off_a_short_is_not_an_open(off):
    s = _session()
    b1, b2 = _bids(s)
    s.detailed_result = _result(s, shorts=[((b1, 0, 3), (b2, 0, 5))])
    assert s._dn_opens() == 0
    assert s._open_segments() == []
    assert s._rr_open_bundles() == []
    stage, metric = s._rr_stage_metric()
    assert stage == 'b' and metric()[0] == 0


# ── the knob on: a short reads as an open ──────────────────────────────────

def test_on_each_short_counts_one_open(on):
    s = _session()
    b1, b2 = _bids(s)
    s.detailed_result = _result(
        s, shorts=[((b1, 0, 3), (b2, 0, 5)), ((b1, 0, 4), (b2, 0, 6))],
        missing={(b1, 0, 0)})
    assert s._dn_opens() == 1 + 2            # one unplaced bit, two shorts
    stage, metric = s._rr_stage_metric()
    assert metric()[0] == 3


def test_on_both_bundles_of_a_short_become_contenders(on):
    s = _session()
    b1, b2 = _bids(s)
    s.detailed_result = _result(s, shorts=[((b1, 0, 3), (b2, 0, 5))])
    assert set(s._rr_open_bundles()) == {b1, b2}
    exp = 8
    assert sorted(s._open_segments()) == [(b1, 0, 1, exp), (b2, 0, 1, exp)]


def test_on_a_shorted_open_segment_is_one_entry(on):
    # A segment both missing a bit and carrying a shorted one is ONE open
    # entry with both counted: negotiate keys its history on the window,
    # and two entries would charge it twice per iteration.
    s = _session()
    b1, b2 = _bids(s)
    s.detailed_result = _result(s, shorts=[((b1, 0, 3), (b2, 0, 5))],
                                missing={(b1, 0, 0)})
    segs = [e for e in s._open_segments() if e[:2] == (b1, 0)]
    assert segs == [(b1, 0, 2, 8)]
    # ... and the open bundle is listed ahead of the one only shorted.
    assert s._rr_open_bundles() == [b1, b2]


def test_a_wire_in_two_shorts_is_one_wire(on):
    s = _session()
    b1, b2 = _bids(s)
    s.detailed_result = _result(
        s, shorts=[((b1, 0, 3), (b2, 0, 5)), ((b1, 0, 3), (b2, 0, 6))])
    assert s._dn_opens() == 2                 # two shorts
    assert sorted(s._open_segments()) == [(b1, 0, 1, 8), (b2, 0, 2, 8)]


def test_the_open_segment_memo_follows_the_knob(monkeypatch):
    # The walk is memoized on the result object; one read under each
    # setting of the same result must not serve the other.
    s = _session()
    b1, b2 = _bids(s)
    s.detailed_result = _result(s, shorts=[((b1, 0, 3), (b2, 0, 5))])
    monkeypatch.setenv("BUDA_HEAL_SHORTS", "0")
    assert s._open_segments() == []
    monkeypatch.delenv("BUDA_HEAL_SHORTS", raising=False)
    assert len(s._open_segments()) == 2
    monkeypatch.setenv("BUDA_HEAL_SHORTS", "0")
    assert s._open_segments() == []


# ── the bottom-up merge: a template's short, at every copy ─────────────────

def _template_session():
    """proc_cell — two children and TWO cell-local 4-bit buses, one each
    way — placed twice on the track pitch, marked bottom-up and routed to
    NUTS: DNUTS solves the reference instance and copies it to the other."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 500, 300)
    for k in (1, 2):
        for i in range(4):
            db.add_net_pins(f"ab{k}_{i}", f"proc_i{k}/pa_i.out",
                            [f"proc_i{k}/pb_i.in"])
            db.add_net_pins(f"ba{k}_{i}", f"proc_i{k}/pb_i.out",
                            [f"proc_i{k}/pa_i.in"])
    buda.BustermGen(db).derive(1)
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    cmds = ["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
            "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
    cmds += [f"def_track_pattern {lid} 0 SIGNAL 1 1" for lid in (6, 7, 4, 5)]
    cmds += ["run_hier_bundler", "generate_hier_topologies",
             "set_bottom_up proc_cell", "run_planner hier", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def test_the_bottom_up_merge_counts_a_template_short_at_every_copy(
        monkeypatch):
    # A short between two bundles of one template is copied with them into
    # every sibling instance.  The reference solve has no copies and the
    # rest solve drops a pair of two fixed bits, so the merged result's
    # shorts — what the healers read — must be counted over the merged
    # route, or the copy's is missed (Codex P1 on #966).  The route here
    # routes clean, so the short is PLANTED in the reference solve's
    # output: one of bundle B's bits laid on bundle A's metal.
    s = _template_session()
    ref_ids, copy_specs, skip_ids = s._bottom_up_dnuts_plan()
    assert len(ref_ids) == 2 and len(skip_ids) == 2
    real = buda.DetailedNUTSEngine
    planted = []

    class Planting(real):
        def run(self, segs, *args, **kwargs):
            res = real.run(self, segs, *args, **kwargs)
            if {b.bundle_id for b in segs} != ref_ids:
                return res
            wires = list(res.net_segments)
            a_id, b_id = sorted(ref_ids)
            # A bit of B moved onto a wire of A's on its own layer, so its
            # direction (and the copy's transform) is unchanged.
            for bi, bw in enumerate(wires):
                if bw.bundle_id != b_id or bw.is_shield:
                    continue
                aw = next((w for w in wires if w.bundle_id == a_id
                           and w.layer == bw.layer and not w.is_shield),
                          None)
                if aw is None:
                    continue
                bw.track_position = aw.track_position
                bw.span_lo, bw.span_hi = aw.span_lo, aw.span_hi
                wires[bi] = bw
                planted.append((a_id, b_id))
                break
            res.net_segments = wires
            return res

    monkeypatch.setattr(buda, "DetailedNUTSEngine", Planting)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    assert planted, "the reference solve was not seen"
    got = {(c.bundle_a, c.bundle_b) for c in s.detailed_result.cross_shorts}
    sib = {ref: sib for ref, sib, *_ in copy_specs}
    a_id, b_id = planted[0]
    # Once in the reference, once in the copy — the copy named by the
    # sibling's own bundles — and exactly what the audit reads.
    assert got == {(a_id, b_id), tuple(sorted((sib[a_id], sib[b_id])))}
    assert len(s.detailed_result.cross_shorts) == _short_count(s) == 2


# ── a real flow: mix ends on shorts alone, and the knob heals it ───────────

def _run_flow(rel):
    s = buda_cli.BudaSession()
    s.no_viz = True
    d, base = os.path.split(os.path.join(_ROOT, rel))
    cwd = os.getcwd()
    try:
        os.chdir(d)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            s.do_command(f"source {base}")
    finally:
        os.chdir(cwd)
    return s


def _short_count(s):
    bit_nets, shield_nets = s._wire_nets()
    return len(buda.check_dnuts_cross_shorts(
        s.detailed_result, bit_nets, shield_nets).violations)


@pytest.mark.mid
def test_mix_heals_its_shorts_by_default(monkeypatch):
    # Before the flip `rnr/mix` ended 0 overlaps / 0 unplaced with 12
    # cross-bundle shorts no healer could see (#964's 0 -> 6 dirty bundles).
    # Counting them, stage b's negotiate + ripup clear every one.
    monkeypatch.setenv("BUDA_HEAL_SHORTS", "0")
    monkeypatch.delenv("BUDA_DNUTS_SHORT_GUARD", raising=False)
    base = _run_flow("flow/rnr/mix.buda")
    assert _short_count(base) == 12
    assert base.detailed_result.num_cross_shorts == 12
    monkeypatch.delenv("BUDA_HEAL_SHORTS", raising=False)
    s = _run_flow("flow/rnr/mix.buda")
    assert _short_count(s) == 0
    assert list(s.detailed_result.cross_shorts) == []
    assert s.detailed_result.num_unplaced == 0
    assert s.nuts_result.num_overlaps == 0


@pytest.mark.mid
def test_parallel_sweep_scores_shorts_like_the_replay(monkeypatch):
    # The sweep's C++ metric must count what the sequential trial counts,
    # or a move that only clears shorts is never replayed (or a replay
    # disagrees): the whole flow, parallel against sequential, must make
    # the same decisions with the healers counting shorts.
    monkeypatch.delenv("BUDA_HEAL_SHORTS", raising=False)
    monkeypatch.delenv("BUDA_DNUTS_SHORT_GUARD", raising=False)
    monkeypatch.setenv("BUDA_SWEEP_THREADS", "2")
    par = _run_flow("flow/rnr/mix.buda")
    monkeypatch.setattr(ripup_mod, "_RR_PARALLEL_SWEEP_DEFAULT", False)
    seq = _run_flow("flow/rnr/mix.buda")

    def sel(s):
        return {w.input.original_bundle.id: w.plan.selected_topology_index
                for w in s.bundles}
    assert sel(par) == sel(seq)
    assert (par.detailed_result.num_unplaced, par.nuts_result.num_overlaps,
            len(par.detailed_result.cross_shorts)) == \
        (seq.detailed_result.num_unplaced, seq.nuts_result.num_overlaps,
         len(seq.detailed_result.cross_shorts))
