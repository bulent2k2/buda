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

"""NDR x BOTTOM-UP composition — requirement R13.

A governed cell template marked `set_bottom_up` is solved ONCE on its
reference instance and copied verbatim to every sibling, "shield wires
included".  Nothing exercised that claim until `flow/ndr_bottom_up.buda`,
and running it exposed two shield-counting faults in the bottom-up paths
— the same class as the #616 masking bug, in code that fix never reached:

1. the copy path's unplaced accounting compared `bit_width` (signal bits)
   against a placed count that INCLUDED shield rows, so a governed cell
   SUBTRACTED from num_unplaced (measured: -6 on this vehicle) — a
   negative count that masks real opens;
2. the cull-risk survival predictor counted shield rows as placed member
   bits, so a governed segment with bits genuinely stranded could read
   `placed >= need` and skip the escalation it needs.

These tests pin the requirement (copies carry shields, counts stay
honest) and regression 1, which this vehicle reproduces directly.

Regression 2 is a by-inspection consistency fix with NO dedicated test:
its predicate sits behind `wmap`, which excludes `hier.locked` bundles,
so the bottom-up instances never reach it — observing it needs a
governed NON-locked segment on a LOW layer whose seat is starved yet
still realizable (a coarse pattern instead hard-errors at R3).  The rule
it now follows — shield rows are metal, not member bits — is the same
one `_rr_open_bundles` and the viz panels adopted in #616.
"""

import contextlib
import io
import pathlib

import pytest

pytestmark = pytest.mark.mid

import buda
import buda_cli

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FLOW = _ROOT / "flow" / "ndr_bottom_up.buda"


def _run_vehicle():
    """Replay flow/ndr_bottom_up.buda in-session; return (session, output)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for raw in _FLOW.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            s.do_command(line)
    return s, buf.getvalue()


def _locked_governed(s):
    return [w for w in s.bundles
            if w.input.ndr.active() and w.hier.locked]


def test_bottom_up_template_is_solved_once_and_copied():
    s, out = _run_vehicle()
    # The class aligned, so DNUTS solved the reference and copied it.
    assert "ALIGNED" in out
    assert "reference bit(s) solved once" in out
    assert "copied to 3 sibling instance(s)" in out


def test_every_copied_instance_carries_its_shields():
    """R13's load-bearing claim: 'shield wires included'.  All four
    occurrences of the governed bottom-up template (the reference plus
    three copies) must carry the rule's two flanking shields — at their
    OWN instance coordinates, not the reference's."""
    s, _ = _run_vehicle()
    locked = _locked_governed(s)
    assert len(locked) == 4, "expected the 4 bottom-up instances governed"
    shields = {}
    tracks = {}
    for ns in s.detailed_result.net_segments:
        if ns.is_shield:
            shields[ns.bundle_id] = shields.get(ns.bundle_id, 0) + 1
            tracks.setdefault(ns.bundle_id, []).append(ns.track_position)
    for w in locked:
        bid = w.input.original_bundle.id
        assert shields.get(bid) == 2, f"bundle {bid} lost its shields"
    # Copies are transforms, not duplicates: the instances sit at
    # different places, so their shield tracks cannot all coincide.
    assert len({tuple(sorted(v)) for v in tracks.values()}) > 1


def test_copy_path_unplaced_count_is_honest():
    """Regression 1: shield rows are metal, not member bits.  Counting
    them against `bit_width` made this vehicle report -6 unplaced."""
    s, out = _run_vehicle()
    assert s.detailed_result.num_unplaced == 0
    assert s.detailed_result.num_unplaced >= 0     # never negative
    assert "-" not in out.split("bits unplaced")[0].split()[-1]


def test_governed_bottom_up_flow_is_clean():
    s, out = _run_vehicle()
    assert "no violations" in out
    assert "NDR_" not in out
    assert "NDR shield metal:" in out


def test_ungoverned_bottom_up_flow_is_unaffected():
    """R12 at the bottom-up boundary: the same vehicle with its scopes
    cleared routes with no shields and the same honest accounting — the
    fixes are shield-conditional, so an ungoverned bottom-up design is
    untouched."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for raw in _FLOW.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            s.do_command(line)
            if line.startswith("set_ndr t2x_"):
                s.do_command("set_ndr loc_ off")
                s.do_command("set_ndr t2x_ off")
    out = buf.getvalue()
    assert "reference bit(s) solved once" in out      # still bottom-up
    assert s.detailed_result.num_unplaced == 0
    assert not any(ns.is_shield for ns in s.detailed_result.net_segments)
    assert "no violations" in out


def test_bond_straps_ride_the_copy():
    """R6 x R13: `bond` straps are vias, so the copy path carries them to
    every sibling for free (transform_net_via moves a via wholesale) — at
    the sibling's OWN coordinates, and the merged count reports all of
    them, not just the reference's."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for raw in _FLOW.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("def_ndr locsh"):
                line += " bond"
            s.do_command(line)
    out = buf.getvalue()
    dr = s.detailed_result
    straps = [v for v in dr.net_vias if v.to_seg < 0]
    assert dr.n_shield_bond_vias == len(straps) > 0
    assert "NDR shield bond via(s)" in out
    locked_ids = {w.input.original_bundle.id for w in _locked_governed(s)}
    strapped = {v.bundle_id for v in straps}
    assert locked_ids <= strapped, "a copied instance lost its straps"
    # Copies are transforms: the four occurrences cannot share coordinates.
    by_bid = {}
    for v in straps:
        by_bid.setdefault(v.bundle_id, set()).add((v.x, v.y))
    assert len({frozenset(v) for v in by_bid.values()}) > 1
    assert "NDR_BOND" not in out


def _run_vehicle_lines(extra_after=None, patch=None):
    """Replay the vehicle with optional per-line rewriting and extra lines
    injected after a matching prefix."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for raw in _FLOW.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if patch:
                line = patch(line)
            s.do_command(line)
            for prefix, extra in (extra_after or {}).items():
                if line.startswith(prefix):
                    for e in extra:
                        s.do_command(e)
    return s, buf.getvalue()


def test_copied_bonds_are_revalidated_against_each_siblings_grid():
    """A strap's validity depends on the ADJACENT PERPENDICULAR layer's
    rails, and `check_template_tracks` compares track pools on the ROUTED
    layer only — so copy eligibility does NOT prove a sibling sees the same
    rails.  Transforming the reference's straps would hand such a sibling a
    via over the wrong supply while the NDR_BOND audit read the row as
    proof of bonding.  Straps are therefore RE-DERIVED per instance.

    The override here keeps layer 4's SIGNAL slots byte-identical (so the
    pools still match and the instance is still COPIED) and only flips the
    rail's identity GND -> VDD over u4's region.  A POWER rail can never
    bond a ground shield, so u4 must come out with zero straps and a LOUD
    NDR_BOND, while its three siblings stay bonded."""
    # The governed cell-local bus lands on M5, so BOTH adjacent
    # perpendicular layers (M4 and M6) have to be flipped to de-bond it.
    flips = [f"add_grid_override {lid} 490 490 720 670 0 "
             f"VDD 2 1 (_ 1 1)x12 VDD 2 1" for lid in (4, 6)]
    s, out = _run_vehicle_lines(
        patch=lambda l: l + " bond" if l.startswith("def_ndr locsh") else l,
        extra_after={"def_track_pattern 6": flips})

    # Still the bottom-up copy path: the pools match, so u4 is COPIED.
    assert "copied to 3 sibling instance(s)" in out, out
    straps = [v for v in s.detailed_result.net_vias if v.to_seg < 0]
    assert straps, "the unaffected instances must still be bonded"

    # Exactly the instances OUTSIDE the flipped region carry straps.
    governed = {w.input.original_bundle.id: w for w in _locked_governed(s)}
    assert len(governed) == 4
    boxes = {}
    for ns in s.detailed_result.net_segments:
        if ns.is_shield and ns.bundle_id in governed:
            boxes.setdefault(ns.bundle_id, []).append(
                (0.5 * (ns.span_lo + ns.span_hi), ns.track_position))
    inside = {bid for bid, pts in boxes.items()
              if all(490 <= x <= 720 and 490 <= y <= 670 for x, y in pts)}
    assert len(inside) == 1, f"expected one instance in the region: {inside}"
    strapped = {v.bundle_id for v in straps}
    assert not (inside & strapped), \
        "a sibling over POWER rails inherited the reference's ground straps"
    assert governed.keys() - inside <= strapped

    # …and the audit says so rather than reading the copy as bonded.
    audit = io.StringIO()
    with contextlib.redirect_stdout(audit):
        s.do_command("check_design")
    report = audit.getvalue()
    assert "NDR_BOND" in report and "floating metal" in report, report
