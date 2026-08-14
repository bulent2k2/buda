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

"""Every NDR check must be able to FAIL.

The NDR feature produced four faults of one shape: a check whose expectation
was derived from the same number as the thing it checked, so the two agreed by
construction and the check could not fail.

  * the R9 width audit compared covered slot centres against `width_slots`,
    both from the quantization the placement used (#721);
  * `def_ndr_layer`'s R3 probe left `metal_quant` default and passed no pitch,
    so it returned ok unconditionally (#717);
  * the conservative maximum was cached before a per-layer override existed,
    so the rule reported and charged a number no longer conservative (#717);
  * `ensure_abs_quantization` discarded the realizability verdict and cached
    the CLAMP, which then fit by construction (#717).

Each was found individually, by review, after shipping.  These tests look for
the shape directly instead: **perturb the placed result and assert the check
notices**.  A check that cannot detect any corruption of what it guards is
not protecting anything, whatever its code says.

METHOD NOTE, learned the hard way.  A mutation that silently fails to apply
reads exactly like a vacuous check — the first run of this sweep reported the
shield audit as blind because `del rows[i]` mutated a pybind COPY and never
reached the result.  So every perturbation here VERIFIES it landed before the
audit is consulted; the write-back check in `_put_segments` is not ceremony,
it is the difference between a finding and a false alarm.  (The same by-value trap produced a real defect in
`NdrSpec.per_layer` — see test_ndr_metal_quantization.)
"""
import pytest

import buda


_STACK = """add_block a 0 0 200 200
add_block b 900 0 1100 200
add_bus em_[4] a.p b.q
def_layer 3 M3 H TOP 20
def_layer 4 M4 V 20
def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1
"""
_RUN = """set_ndr em_ em
run_bundler STRICT
generate_topologies
set_track_pitch 3
run_planner 1
run_nuts
run_detailed_nuts
"""

# The rule forms the checks key on.
CHANNEL = "def_ndr em width x2\n"
METAL = "def_ndr em width 3 metal\n"
SHIELDED = "def_ndr em width x2 shield bus net GND\n"
GUARDED = "def_ndr em width x2 spacing x3\n"
BONDED = "def_ndr em width x2 shield bus net GND bond\n"


def _session(decl):
    import sys
    sys.path[:0] = ["build", "src", "tools"]
    import buda_cli
    s = buda_cli.BudaSession()
    for raw in (_STACK + decl + _RUN).splitlines():
        line = raw.strip()
        if line:
            s.do_command(line)
    return s


def _audit(session):
    from buda_cmds import ndr_cmds
    w = next(x for x in session.bundles if x.input.ndr.active())
    return ndr_cmds.audit_ndr_dnuts(session, w)


def _kinds(violations):
    """The TYPED kind, not the message prefix.

    `check_design` groups and counts by the typed kind, and a methodology
    gates on it — so that is what these tests must assert.  Reading the
    prefix out of the message instead let a probe that renamed the kind
    (leaving the text alone) slip past this guard while running it."""
    return sorted({v.kind.name for v in violations})


def _put_segments(session, segs):
    """Write the row list BACK.  pybind hands out a copy, so mutating the
    returned list alone changes nothing — the false-negative trap."""
    session.detailed_result.net_segments = segs
    return len(session.detailed_result.net_segments) == len(segs)


def _clean_session(decl):
    s = _session(decl)
    assert not _audit(s), "the honest route must be clean before perturbing it"
    return s


def test_width_audit_notices_a_narrowed_bit_under_either_reading():
    for decl in (CHANNEL, METAL):
        s = _clean_session(decl)
        segs = list(s.detailed_result.net_segments)
        target = next(r for r in segs if not r.is_shield)
        target.width = 1.0
        assert _put_segments(s, segs)
        assert any(abs(r.width - 1.0) < 1e-9
                   for r in s.detailed_result.net_segments), "mutation lost"
        assert "NDR_WIDTH" in _kinds(_audit(s)), decl


def test_shield_audit_notices_a_deleted_shield():
    s = _clean_session(SHIELDED)
    segs = list(s.detailed_result.net_segments)
    before = len(segs)
    segs = [r for i, r in enumerate(segs)
            if not (r.is_shield and all(not x.is_shield for x in segs[:i]))]
    assert len(segs) == before - 1
    assert _put_segments(s, segs)
    assert "NDR_SHIELD" in _kinds(_audit(s))


def test_shield_audit_notices_a_shield_moved_out_of_its_gap():
    s = _clean_session(SHIELDED)
    segs = list(s.detailed_result.net_segments)
    target = next(r for r in segs if r.is_shield)
    target.track_position += 100.0
    assert _put_segments(s, segs)
    assert "NDR_SHIELD" in _kinds(_audit(s))


def test_spacing_audit_notices_a_foreign_wire_inside_the_run():
    """NDR_SPACING's claim is that the run is EXCLUSIVE, which is why it was
    never one of the self-agreeing checks: 'is anyone else's metal in here'
    is measured from the placed rows, not derived from the rule."""
    for decl in (GUARDED, SHIELDED):
        s = _clean_session(decl)
        segs = list(s.detailed_result.net_segments)
        gov = sorted((r for r in segs if not r.is_shield),
                     key=lambda r: r.track_position)
        intruder = buda.NetSegment()
        intruder.bundle_id = 9999                 # a DIFFERENT bundle
        intruder.seg_idx, intruder.bit_index = 0, 0
        intruder.layer = gov[0].layer
        intruder.track_position = 0.5 * (gov[0].track_position
                                         + gov[1].track_position)
        intruder.width = 1.0
        intruder.span_lo, intruder.span_hi = gov[0].span_lo, gov[0].span_hi
        segs.append(intruder)
        assert _put_segments(s, segs)
        assert "NDR_SPACING" in _kinds(_audit(s)), decl


def test_bond_audit_notices_a_shield_losing_ALL_its_straps():
    """`bond` stride 1 straps every crossing, so removing ONE via leaves the
    shield bonded and the audit is right to stay quiet.  The floating-metal
    condition the check exists for is a shield with NO strap — a too-weak
    perturbation is how a working check gets mistaken for a blind one."""
    s = _clean_session(BONDED)
    vias = list(s.detailed_result.net_vias)
    target = next(((v.bundle_id, v.from_seg, v.bit_index)
                   for v in vias if v.to_seg < 0), None)
    assert target is not None, "the vehicle must actually emit straps"
    keep = [v for v in vias
            if not (v.to_seg < 0
                    and (v.bundle_id, v.from_seg, v.bit_index) == target)]
    assert len(keep) < len(vias)
    s.detailed_result.net_vias = keep
    assert len(s.detailed_result.net_vias) == len(keep), "mutation lost"
    assert "NDR_BOND" in _kinds(_audit(s))


def test_a_vanished_bit_is_UNPLACED_not_an_NDR_violation():
    """Division of labour, pinned so neither side silently drops it.

    The NDR audit deliberately does NOT fire when a governed bit disappears:
    it measures its expectation against the segment's INTENDED membership, so
    the shield arrangement stays correct and a culled bit must not read as a
    spurious NDR_SHIELD on top of the honest open.  The connectivity audit
    owns the missing bit, and this pins that it really does — a gap here
    would be invisible from either side alone."""
    s = _clean_session(SHIELDED)
    before = s._check_design("dnuts")["by_kind"].get("UNPLACED", 0)
    segs = list(s.detailed_result.net_segments)
    n_rows = len(segs)
    for i, r in enumerate(segs):
        if not r.is_shield:
            del segs[i]
            break
    assert len(segs) == n_rows - 1
    assert _put_segments(s, segs)
    # The NDR audit stays quiet…
    assert not _audit(s)
    # …and the connectivity audit reports it, by TYPED KIND.
    #
    # Counted before and after, not searched for in the report text (Codex P2
    # on #728).  A substring assertion on the final report passes whenever
    # ANY unplaced bit exists — including one the fixture already had, or one
    # this deletion did not cause — so it could not prove the connectivity
    # half of this guard works.  A vacuous assertion inside the vacuity
    # sweep, which is exactly the shape the file exists to find.
    after = s._check_design("dnuts")["by_kind"].get("UNPLACED", 0)
    assert after == before + 1, (before, after)


def test_the_sweep_would_notice_a_check_that_stopped_working():
    """The guard's own guard.

    Every test above asserts a check FIRES.  If the perturbation stopped
    applying they would all fail, which is the safe direction — but a
    perturbation that lands on an ALREADY-dirty design proves nothing, so
    `_clean_session` asserts the honest route is clean first.  This pins that
    the fixture really is clean, i.e. that the assertions above are about the
    perturbation and not about pre-existing violations."""
    for decl in (CHANNEL, METAL, SHIELDED, GUARDED, BONDED):
        s = _session(decl)
        assert not _audit(s), decl
