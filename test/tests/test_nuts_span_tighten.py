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

"""Post-convergence span re-tighten (relay_jog_phantom_span_2026-07.md).

An MST relay-jog connector with an unbounded perp window can be transiently
flung across the design during the abstract-NUTS sweep; do_span_adjustments'
extend-only coverage then bakes that transient into a permanent phantom span
tail on a neighbour.  tighten_spans_to_reach re-derives each abstract span to
the tight envelope of what it actually connects to, after convergence.

Repro: flow/hbundles/06_multipin_stress.buda bundle 30 (bus mp6_b), a
TRUNK_H+MST hybrid.  Pre-fix seg7 placed with abstract span [120,820] (a
670-unit dangling tail) while its real bit-wires span ~[790,821].
"""
import contextlib
import io
import os

import pytest

import buda_cli

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FLOW = "flow/hbundles/06_multipin_stress.buda"
_SKIP = ("visualize", "visualize_topologies", "exit", "report_wl",
         "report_wirelength")


def _run(rel):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = os.path.join(ROOT, rel)
    cwd = os.getcwd()
    os.chdir(os.path.join(ROOT, os.path.dirname(rel)))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for line in open(os.path.basename(rel)):
                c = line.strip()
                if not c or c.startswith('#') or c.split()[0] in _SKIP:
                    continue
                s.do_command(c)
    finally:
        os.chdir(cwd)
    return s


@pytest.mark.mid
def test_no_phantom_abstract_span_beyond_detailed_extent():
    """Every abstract segment's span must stay within realization-noise of the
    union of its own bit-wires' spans — a phantom tail (abstract span reaching
    far past where any bit goes) is exactly the bug this pass removes."""
    s = _run(FLOW)
    det = {}                                  # (bundle, seg) -> [lo, hi] over bits
    for ns in s.detailed_result.net_segments:
        k = (ns.bundle_id, ns.seg_idx)
        lo, hi = min(ns.span_lo, ns.span_hi), max(ns.span_lo, ns.span_hi)
        if k not in det:
            det[k] = [lo, hi]
        else:
            det[k][0] = min(det[k][0], lo)
            det[k][1] = max(det[k][1], hi)

    NOISE = 40.0                              # per-bit taper / min-stub slack
    worst = None
    for ts in s.nuts_result.segments:
        k = (ts.bundle_id, ts.seg_idx)
        if k not in det:
            continue                          # no placed bits (fan-in taper): skip
        a_lo, a_hi = min(ts.span_lo, ts.span_hi), max(ts.span_lo, ts.span_hi)
        d_lo, d_hi = det[k]
        overhang = max(d_lo - a_lo, a_hi - d_hi, 0.0)   # abstract past detailed
        if worst is None or overhang > worst[1]:
            worst = (k, overhang)
    assert worst is not None
    assert worst[1] <= NOISE, (
        f"phantom span tail: seg {worst[0]} abstract overhangs its detailed "
        f"extent by {worst[1]:.0f} units (> {NOISE})")


@pytest.mark.mid
def test_bundle30_mp6b_seg7_span_is_tight():
    """The reported repro: bundle 30 (mp6_b) seg7 must land at its junction
    envelope (~[797,820]), not the pre-fix [120,820]."""
    s = _run(FLOW)
    b30 = next(w for w in s.bundles
               if w.input.original_bundle.id == 30)
    assert any(n.startswith("mp6_b")
               for n in b30.input.original_bundle.get_net_names())
    seg7 = [ts for ts in s.nuts_result.segments
            if ts.bundle_id == 30 and ts.seg_idx == 7]
    assert seg7, "bundle 30 seg7 not placed"
    ts = seg7[0]
    length = abs(ts.span_hi - ts.span_lo)
    assert length < 100, (
        f"bundle 30 seg7 span {ts.span_lo}..{ts.span_hi} (len {length:.0f}) "
        f"still carries a phantom tail")
