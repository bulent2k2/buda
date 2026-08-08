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

"""Wrapper cross-field invariants + snapshot coverage (Phase C, R1/R3).

The mutable pybind surface means a bad Python-side write used to surface
stages later; validate_wrappers checks the historical bug classes at
stage entries (BUDA_VALIDATE=1, on for the whole suite via conftest).
The snapshot-coverage test is R3's contract: every bound wrapper field
must be explicitly classified snapshotted-or-exempt, so growing the
binding without classifying the new field is a loud failure here.
"""

import contextlib
import io

import pytest

import buda
import buda_cli
from buda_session.util import validate_wrappers


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ["def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 200 400", "add_block B 600 800 800 1200",
                  "add_bus d[8] A.p B.p",
                  "run_bundler", "generate_topologies", "run_planner 3"]:
            s.do_command(c)
    return s


def test_clean_session_validates():
    s = _session()
    assert validate_wrappers(s.bundles) == []


def test_unpin_hazard_fires_at_next_stage_entry():
    # The historical bug shape: forced per-segment layers surviving a
    # re-select onto a different-shaped candidate — the planner applies
    # pinned_seg_layers to ANY candidate.  (Forced layers matching the
    # selected candidate's shape are legitimate: edit_commit sets them
    # without a pin.)
    s = _session()
    w = s.bundles[0]
    sel = w.plan.selected_topology_index
    nseg = len(w.input.candidates[sel].segments)
    w.input.pinned_seg_layers = [4] * (nseg + 2)     # wrong shape
    bad = validate_wrappers(s.bundles)
    assert bad and "unpin hazard" in bad[0]
    with pytest.raises(RuntimeError, match="invariant"):
        s._validate_stage_entry("test")
    # run_nuts's entry is wired: the same violation aborts the command.
    with pytest.raises(RuntimeError, match="invariant"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command("run_nuts")


def test_selection_and_length_invariants():
    s = _session()
    w = s.bundles[0]
    w.plan.selected_topology_index = len(w.input.candidates) + 3
    assert any("out of range" in m for m in validate_wrappers(s.bundles))
    s2 = _session()
    w2 = s2.bundles[0]
    w2.plan.seg_layers = list(w2.plan.seg_layers) + [4]
    assert any("seg_layers len" in m for m in validate_wrappers(s2.bundles))


# ── R3 snapshot-coverage contract ────────────────────────────────────────────
# Every writable field of the wrapper structs is classified.  A new bound
# field fails here until it is added to exactly one set — with the reason
# thought through (does a healer trial mutate it? then _rr_snapshot must
# capture it too, and it belongs in SNAPSHOTTED).

SNAPSHOTTED = {
    # captured per wrapper by _rr_snapshot's 'wrap' tuple
    "plan.selected_topology_index", "input.topology_pinned",
    "plan.seg_layers", "plan.seg_perp", "plan.seg_net_pull",
    "plan.seg_slide_lo", "plan.seg_slide_hi",
    "input.assigned_v_layer", "input.assigned_h_layer",
    "input.pinned_seg_layers",
    # candidate pools: count-trimmed + the dogleg slot deep-copied (dl_cand)
    "input.candidates",
}
EXEMPT = {
    # immutable identity / setup-time state no trial mutates
    "input.original_bundle": "bundle identity — never trial-mutated",
    "input.width": "set at bundling; trials never write it",
    "input.allowed_layers": "layer policy — set at plan setup",
    "input.layer_cap": "layer policy", "input.layer_floor": "layer policy",
    "input.layer_shares": "layer policy",
    "input.ndr": "NDR rule policy — resolved at bundling (apply_ndr_specs), "
                 "trials re-pin topologies/layers but never rewrite the rule",
    "input.share_group": "layer policy", "input.share_budgets": "policy",
    "input.pinned_group": "super-candidate pin — trials never clear it "
                          "(negotiate/ripup preserve it by contract)",
    # BundleWrapper's own top-level members: sub-struct handles whose FIELDS
    # are classified individually above/below; wholesale replacement of a
    # handle is construction-only (the expansion/bundling assembly sites).
    "wrapper.input": "sub-struct handle — fields classified individually",
    "wrapper.plan": "sub-struct handle — fields classified individually",
    "wrapper.hier": "sub-struct handle — fields classified individually",
    "hier.locked": "bottom-up lock — set at plan, trials skip locked",
    # hier-expansion bookkeeping, written once by run_planner hier
    "hier.level": "expansion level ordering — plan-time only",
    "hier.priority": "plan-time ladder priority",
    "hier.has_reservation": "demand-reservation flag — plan-time only",
    "hier.res_x1": "reservation bbox — plan-time only",
    "hier.res_y1": "reservation bbox — plan-time only",
    "hier.res_x2": "reservation bbox — plan-time only",
    "hier.res_y2": "reservation bbox — plan-time only",
}


def _writable(obj, prefix):
    out = set()
    for name in dir(type(obj)):
        if name.startswith("_"):
            continue
        prop = getattr(type(obj), name, None)
        if isinstance(prop, property) and prop.fset is None:
            continue                      # read-only
        if callable(getattr(obj, name, None)):
            continue                      # method
        out.add(f"{prefix}.{name}")
    return out


def test_snapshot_coverage_contract():
    s = _session()
    w = s.bundles[0]
    fields = _writable(w, "wrapper") | _writable(w.input, "input") \
        | _writable(w.plan, "plan") | _writable(w.hier, "hier")
    unclassified = fields - SNAPSHOTTED - set(EXEMPT)
    assert not unclassified, (
        "New wrapper field(s) not classified snapshotted-or-exempt — decide "
        "whether healer trials can mutate each (then _rr_snapshot must "
        "capture it) and add it to the right set: "
        + ", ".join(sorted(unclassified)))
