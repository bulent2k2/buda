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
"""select_topology / select_topologies accept a net-name hint (not just a
numeric bundle ID), stay backward compatible with numeric IDs + ranges, expose
id:/net: disambiguation, and print an error that names the bus and its candidate
count.  Regression for the flow/mst_bad.buda footgun (a bus whose bundle ID is
not its ordinal among the generated bundles)."""
import contextlib
import io

import buda_cli


def _sess(*extra):
    """Two buses (busA 2 bits, busB 3 bits) between three blocks; topologies
    generated for BOTH so each has a non-empty candidate pool."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
        "add_block A 0 0 100 100",
        "add_block B 400 0 500 100",
        "add_block C 200 300 300 400",
        "add_bus busA[2] A.p B.p",
        "add_bus busB[3] A.p B.p,C.p",
        "run_bundler",
        "generate_topologies",
        *extra,
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


def _pinned(s, net_prefix):
    for w in s.bundles:
        names = w.input.original_bundle.get_net_names()
        if names and names[0].startswith(net_prefix):
            return (w.input.topology_pinned, w.plan.selected_topology_index)
    return (None, None)


def test_numeric_bundle_id_still_works():
    s = _sess()
    out = _run(s, "select_topology 1 2")
    assert "Pinned bundle 1" in out
    assert _pinned(s, s.bundles[0].input.original_bundle.get_net_names()[0][:4])[0]


def test_net_name_hint_pins_the_right_bundle():
    s = _sess()
    out = _run(s, "select_topology busB 1")
    assert "Pinned" in out and "busB" in out
    assert _pinned(s, "busB") == (True, 0)
    # a bundle the hint did NOT name stays unpinned
    assert _pinned(s, "busA")[0] in (False, None)


def test_id_and_net_disambiguation_prefixes():
    s = _sess()
    assert "Pinned bundle 1" in _run(s, "select_topology id:1 2")
    assert "busA" in _run(s, "select_topology net:busA 1")


def test_error_names_bus_and_range_when_id_out_of_range():
    s = _sess()
    out = _run(s, "select_topology busA 9999")
    assert "invalid topology id 9999" in out
    assert "busA" in out and "valid range is 1.." in out


def test_error_when_bundle_has_no_candidates():
    # Generate for NO bundle, then pin — the pool is empty.
    s = buda_cli.BudaSession(); s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 100 100", "add_block B 400 0 500 100",
                  "add_bus busA[2] A.p B.p", "run_bundler"):
            s.do_command(c)
    out = _run(s, "select_topology busA 1")
    assert "no candidates" in out and "generate_topologies" in out


def test_error_when_hint_matches_nothing():
    s = _sess()
    out = _run(s, "select_topology busZZZ 1")
    assert "no bundle whose first net starts with 'busZZZ'" in out


def test_empty_net_prefix_is_rejected_not_matching_all():
    # `net:` with no prefix must NOT startswith()-match every bundle (Codex P2).
    s = _sess()
    out = _run(s, "select_topology net: 1")
    assert "empty net-name prefix" in out
    assert "Pinned" not in out
    assert _pinned(s, "busA")[0] in (False, None)
    assert _pinned(s, "busB")[0] in (False, None)


def test_select_topologies_mixes_hints_and_numeric_ranges():
    s = _sess()
    out = _run(s, "select_topologies busA,busB 1")
    assert out.count("Pinned") == 2
    assert _pinned(s, "busA") == (True, 0)
    assert _pinned(s, "busB") == (True, 0)

# ── type-spec selectors ─────────────────────────────────────────────────────
# `select_topology <sel> <type-spec>` — the pin that survives a regeneration
# whose new knobs (double_detour, multi_trunk, ...) renumbered every candidate
# id: shape matches case-insensitively, a coordinate matches the CLOSEST
# candidate locus (reported), and ambiguity resolves by the planner's REAL
# cost after run_planner, by estimated wirelength before it.


def _shapes(w):
    return [c.type.split("@")[0] for c in w.input.candidates]


def test_type_spec_pins_by_shape_and_reports():
    s = _sess()
    w = s.bundles[0]
    shape = _shapes(w)[len(w.input.candidates) // 2]
    out = _run(s, f"select_topology id:{w.input.original_bundle.id} {shape}")
    assert "[TopoSpec]" in out and "Pinned" in out
    pinned_idx = w.plan.selected_topology_index
    assert w.input.topology_pinned
    assert w.input.candidates[pinned_idx].type.split("@")[0] == shape


def test_type_spec_matches_closest_coordinate_and_says_how_far():
    s = _sess()
    w = s.bundles[0]
    # Find the candidate with the LARGEST value on some axis, then ask for a
    # coordinate 7 past it — the nearest match is that candidate, off by 7.
    best = None
    for i, c in enumerate(w.input.candidates):
        for comp in c.type.split("@")[1:]:
            ax = comp.rstrip("-0123456789.")
            if ax and comp[len(ax):].lstrip("-").replace(".", "").isdigit():
                v = float(comp[len(ax):])
                if best is None or v > best[3]:
                    best = (i, c.type.split("@")[0], ax, v)
    assert best is not None, "fixture pool has no coordinate-bearing candidate"
    i, shape, ax, v = best
    out = _run(s, f"select_topology id:{w.input.original_bundle.id} "
                  f"{shape}@{ax}{v + 7:g}")
    assert "nearest match (off by 7" in out, out
    assert w.plan.selected_topology_index == i, out


def test_type_spec_ambiguity_ranks_by_planner_cost_after_a_plan():
    s = _sess("run_planner 3")
    w = s.bundles[0]
    shapes = _shapes(w)
    shape = next((sh for sh in shapes if shapes.count(sh) > 1), None)
    assert shape is not None, "fixture pool has no repeated shape"
    out = _run(s, f"select_topology id:{w.input.original_bundle.id} {shape}")
    assert "chosen by REAL planner cost among" in out, out
    assert w.input.topology_pinned


def test_type_spec_ambiguity_ranks_by_wirelength_before_a_plan():
    s = _sess()
    w = s.bundles[0]
    shapes = _shapes(w)
    shape = next((sh for sh in shapes if shapes.count(sh) > 1), None)
    assert shape is not None, "fixture pool has no repeated shape"
    out = _run(s, f"select_topology id:{w.input.original_bundle.id} {shape}")
    assert "chosen by estimated wirelength (no plan yet)" in out, out


def test_type_spec_unknown_shape_lists_the_pool():
    s = _sess()
    w = s.bundles[0]
    out = _run(s, f"select_topology id:{w.input.original_bundle.id} Q_NOPE")
    assert "no candidate of shape 'Q_NOPE'" in out
    assert _shapes(w)[0] in out          # the remedy names real shapes
    assert not w.input.topology_pinned


def test_type_spec_survives_a_regeneration_that_renumbers_ids():
    # The motivating case: regenerate with a knob that grows the pool (ids
    # renumber), and the SAME spec still lands on the same shape.
    s = _sess()
    w = s.bundles[0]
    shape = _shapes(w)[0]
    _run(s, f"select_topology id:{w.input.original_bundle.id} {shape}")
    n_before = len(w.input.candidates)
    _run(s, f"generate_topologies_for_bundle "
            f"id:{w.input.original_bundle.id} double_detour")
    out = _run(s, f"select_topology id:{w.input.original_bundle.id} {shape}")
    assert "[TopoSpec]" in out and "Pinned" in out
    idx = w.plan.selected_topology_index
    assert w.input.candidates[idx].type.split("@")[0] == shape


def test_type_spec_in_select_topologies_pairs():
    s = _sess()
    wa = next(w for w in s.bundles
              if w.input.original_bundle.get_net_names()[0].startswith("busA"))
    wb = next(w for w in s.bundles
              if w.input.original_bundle.get_net_names()[0].startswith("busB"))
    out = _run(s, f"select_topologies busA {_shapes(wa)[0]} "
                  f"busB {_shapes(wb)[0]}")
    assert out.count("Pinned") == 2
    assert wa.input.topology_pinned and wb.input.topology_pinned
