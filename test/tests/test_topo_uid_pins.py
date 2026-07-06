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

"""Phase E1b — pins and sidecar selections survive by stable content identity
(docs/internal/topo_conn_unification.md §8/E1).

Before E1b, regenerating a bundle's candidates dropped its pin wholesale (a
list index is meaningless across lists) and a sidecar selection resolved by
type+WL with a warned index-hint fallback.  Now:

  * `_reset_plan_for_regen` re-attaches a pin when the regenerated list
    contains a candidate with the SAME `topo_uid` as the previously pinned one;
  * `_apply_selections` resolves sidecar entries uid-first (survives
    reordering and regeneration), falling back to type+WL, then the warned
    index hint — so older sidecars keep working.
"""
import contextlib
import io
import json

import buda
import buda_cli


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "def_layer 4 M4 H TOP 20",
           "def_layer 5 M5 V TOP 20",
           "add_block A 0 0 200 400",
           "add_block B 600 100 800 500",
           "add_bus d[8] A.p B.q",
           "run_bundler",
           "generate_topologies")
    return s


def test_pin_survives_regeneration_by_uid(capsys):
    s = _session()
    w = s.bundles[0]
    assert len(w.input.candidates) >= 3
    pin_idx = 2
    pinned_uid = buda.topo_uid(w.input.candidates[pin_idx])
    bid = w.input.original_bundle.id
    _quiet(s, f"select_topology {bid} {pin_idx + 1}")
    assert w.input.topology_pinned

    s.do_command("generate_topologies_for_bundle d")
    out = capsys.readouterr().out
    assert "Pin re-attached by topo_uid" in out, out

    sel = w.plan.selected_topology_index
    assert w.input.topology_pinned, "pin was dropped despite an identical candidate"
    assert buda.topo_uid(w.input.candidates[sel]) == pinned_uid, \
        "pin re-attached to a different topology"


def test_sidecar_resolves_by_uid_over_stale_type_and_index(tmp_path, capsys):
    """A sidecar whose type/WL/index fields are all stale must still resolve to
    the uid'd candidate — proving uid outranks the legacy chain."""
    s = _session()
    w = s.bundles[0]
    target_idx = 2
    target = w.input.candidates[target_idx]
    sidecar = {
        "selections": [{
            "bundle_hint": "d",
            "bundle_id": w.input.original_bundle.id,
            "topo_type": "NO_SUCH_TYPE",        # stale on purpose
            "topo_wl": -12345,                   # stale on purpose
            "topo_uid": buda.topo_uid(target),
            "topo_index_hint": 0,                # points at the WRONG candidate
            "note": "",
            "selected_at": "2026-01-01T00:00:00",
        }]
    }
    script = tmp_path / "uidtest.buda"
    script.write_text("# anchor for the sidecar path\n")
    (tmp_path / "uidtest.json").write_text(json.dumps(sidecar))
    s.script_path = str(script)

    s._apply_selections()
    out = capsys.readouterr().out
    assert "matched by index hint" not in out, out   # uid resolved it, not the hint
    assert w.input.topology_pinned
    sel = w.plan.selected_topology_index
    assert sel == target_idx, \
        f"sidecar uid should pin candidate {target_idx}, pinned {sel}"


def test_sidecar_without_uid_still_resolves_by_type_wl(tmp_path):
    """Back-compat: an older sidecar (no topo_uid field) keeps resolving
    through the type+WL chain."""
    s = _session()
    w = s.bundles[0]
    target_idx = 1
    target = w.input.candidates[target_idx]
    sidecar = {
        "selections": [{
            "bundle_hint": "d",
            "bundle_id": w.input.original_bundle.id,
            "topo_type": target.type,
            "topo_wl": target.estimated_wirelength,
            "topo_index_hint": target_idx,
            "note": "",
            "selected_at": "2026-01-01T00:00:00",
        }]
    }
    script = tmp_path / "uidtest.buda"
    script.write_text("# anchor for the sidecar path\n")
    (tmp_path / "uidtest.json").write_text(json.dumps(sidecar))
    s.script_path = str(script)

    with contextlib.redirect_stdout(io.StringIO()):
        s._apply_selections()
    assert w.input.topology_pinned
    assert w.plan.selected_topology_index == target_idx
