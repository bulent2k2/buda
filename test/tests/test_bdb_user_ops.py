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

"""Op-log provenance in the BDB (TopoEdit follow-on #1).

A committed USER candidate persists geometrically (topology tables, v15
source='user') — restore never needs more.  The op-log (base candidate uid
+ the applied edit_* command lines) is the human-readable design INTENT,
now stored beside the geometry as BDB meta (`user_ops:<bundle_id>:<uid>`,
key-value — no schema bump):

- the CLI session records each APPLIED edit_* command (GUI parity) and
  edit_commit stores the log;
- a live GUI commit stores it too, through the explorer's user_ops_sink
  (the session's _record_user_ops) — not just the sidecar;
- load_pipeline prints a pointer for each restored USER candidate that
  has a log, and `dump_user_ops <bundle_id>` prints the replayable ops.
"""
import contextlib
import io
import json
import os
import sys
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")   # headless; no window

import pytest

import buda
import buda_cli

pytestmark = pytest.mark.mid

LAYERS = ("def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10")

FLAT_SETUP = (*LAYERS,
              "add_block lo 0 0 400 400", "add_block up 0 1000 400 1400",
              "add_bus w[4] lo.o up.i", "run_bundler", "generate_topologies")

EDIT_OPS = ("edit_add_trunk V 500 200 1200",
            "edit_add_stub up 0",
            "edit_add_stub lo 0")


def _session(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = []
    for c in cmds:
        with contextlib.redirect_stdout(io.StringIO()) as b:
            s.do_command(c)
        out.append(b.getvalue())
    return s, "".join(out)


def _selected_uid(w):
    return buda.topo_uid(w.input.candidates[w.plan.selected_topology_index])


def test_cli_commit_stores_user_ops_meta(tmp_path):
    """edit_commit stores the session's applied ops as BDB meta: base 'new'
    for an E-style session, ops = the exact command lines, replayable."""
    db = str(tmp_path / "flat.bdb")
    s, out = _session(f"open_bdb {db}", *FLAT_SETUP,
                      "edit_topology 1 new", *EDIT_OPS, "edit_commit pin")
    assert "op-log provenance stored (3 op(s))" in out
    uid = _selected_uid(s.bundles[0])
    entry = json.loads(s.bdb.meta_get(f"user_ops:1:{uid}", ""))
    assert entry["base"] == "new"
    assert entry["ops"] == list(EDIT_OPS)


def test_inline_comment_stripped_from_oplog(tmp_path):
    """A `# comment` on an edit_* command line (handlers get the RAW cmd_line)
    must NOT leak into the persisted op-log — otherwise the note is baked in
    and shown by dump_user_ops.  The recorded op is the clean command."""
    db = str(tmp_path / "flat.bdb")
    s, _ = _session(f"open_bdb {db}", *FLAT_SETUP,
                    "edit_topology 1 new",
                    "edit_add_trunk V 500 200 1200   # OOB column trunk",
                    "edit_add_stub up 0",
                    "edit_add_stub lo 0",
                    "edit_set_span 0 240 1160  # trim to the stub taps",
                    "edit_commit pin")
    uid = _selected_uid(s.bundles[0])
    entry = json.loads(s.bdb.meta_get(f"user_ops:1:{uid}", ""))
    assert entry["ops"] == [
        "edit_add_trunk V 500 200 1200",
        "edit_add_stub up 0",
        "edit_add_stub lo 0",
        "edit_set_span 0 240 1160",
    ], entry["ops"]
    assert not any("#" in op for op in entry["ops"])


def test_rejected_ops_are_not_recorded(tmp_path):
    """Only APPLIED ops enter the log — a rejected op (out-of-range stub
    target) must not appear (it would break the replay)."""
    db = str(tmp_path / "flat.bdb")
    s, _ = _session(f"open_bdb {db}", *FLAT_SETUP,
                    "edit_topology 1 new",
                    "edit_add_trunk V 500 200 1200",
                    "edit_add_stub up 9",          # rejected: no segment 9
                    "edit_add_stub up 0",
                    "edit_add_stub lo 0",
                    "edit_commit pin")
    uid = _selected_uid(s.bundles[0])
    entry = json.loads(s.bdb.meta_get(f"user_ops:1:{uid}", ""))
    assert entry["ops"] == list(EDIT_OPS)


def test_edit_from_candidate_records_base_uid_and_raw_refs(tmp_path):
    """A session opened ON a candidate records that candidate's content uid
    as the base, and block/face coordinate references are stored VERBATIM —
    the ops can be replayed against a CHANGED floorplan (geometry can't)."""
    db = str(tmp_path / "flat.bdb")
    s, _ = _session(f"open_bdb {db}", *FLAT_SETUP)
    w = s.bundles[0]
    base_uid = buda.topo_uid(w.input.candidates[0])
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("edit_topology 1 1")
        s.do_command("edit_set_span 0 lo.top+50 up.bottom-50")
        s.do_command("edit_commit pin")
    uid = _selected_uid(w)
    entry = json.loads(s.bdb.meta_get(f"user_ops:1:{uid}", ""))
    assert entry["base"] == base_uid
    assert entry["ops"] == ["edit_set_span 0 lo.top+50 up.bottom-50"]
    # dump_user_ops resolves the base uid to its CURRENT pool index so the
    # printed sequence is paste-replayable (Codex #357 — no placeholder).
    with contextlib.redirect_stdout(io.StringIO()) as b:
        s.do_command("dump_user_ops 1")
    dump = b.getvalue()
    base_ci = next(k for k, c in enumerate(w.input.candidates)
                   if buda.topo_uid(c) == base_uid)
    assert f"edit_topology 1 {base_ci + 1}" in dump
    assert "<base" not in dump


def test_abort_discards_ops(tmp_path):
    """An aborted session's ops must not leak into a later commit's log."""
    db = str(tmp_path / "flat.bdb")
    s, _ = _session(f"open_bdb {db}", *FLAT_SETUP,
                    "edit_topology 1 new",
                    "edit_add_trunk V 700 200 1200",   # discarded with abort
                    "edit_abort",
                    "edit_topology 1 new", *EDIT_OPS, "edit_commit pin")
    uid = _selected_uid(s.bundles[0])
    entry = json.loads(s.bdb.meta_get(f"user_ops:1:{uid}", ""))
    assert entry["ops"] == list(EDIT_OPS)


def test_load_pipeline_points_at_user_ops_and_dump_replays(tmp_path):
    """Round trip: reopen the BDB, load_pipeline prints the provenance
    pointer for the restored USER candidate, and dump_user_ops prints the
    replayable command sequence."""
    db = str(tmp_path / "flat.bdb")
    s1, _ = _session(f"open_bdb {db}", *FLAT_SETUP,
                     "edit_topology 1 new", *EDIT_OPS, "edit_commit pin",
                     "save_bdb")
    uid = _selected_uid(s1.bundles[0])
    del s1

    s2, out = _session(f"open_bdb {db}", *LAYERS,
                       "add_block lo 0 0 400 400", "add_block up 0 1000 400 1400",
                       "load_pipeline")
    assert "is USER, built from 3 op(s)" in out
    assert "dump_user_ops 1" in out
    w2 = s2.bundles[0]
    assert _selected_uid(w2) == uid

    with contextlib.redirect_stdout(io.StringIO()) as b:
        s2.do_command("dump_user_ops 1")
    dump = b.getvalue()
    assert "built from an empty topology with 3 op(s)" in dump
    for op in EDIT_OPS:
        assert op in dump
    assert "edit_commit pin" in dump


def test_gui_commit_stores_user_ops_via_sink(tmp_path):
    """A LIVE explorer commit stores the op-log through the user_ops_sink —
    the GUI's provenance no longer waits for a sidecar re-run to land in
    the BDB."""
    import matplotlib
    matplotlib.use("Agg")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                    '..', '..', 'src'))
    import buda_viz

    db = str(tmp_path / "flat.bdb")
    s, _ = _session(f"open_bdb {db}", *FLAT_SETUP)
    exp = buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(tmp_path / "sc.json"),
        layer_stack=s.layers, user_ops_sink=s._record_user_ops)
    try:
        def key(k, x=None, y=None):
            exp._on_key(SimpleNamespace(key=k, xdata=x, ydata=y))

        with contextlib.redirect_stdout(io.StringIO()):
            key('E')                       # empty session
            key('Y', 200, 700); key('Y', 200, 700)   # arm + place V trunk
            key('j')                       # select it
            key('W', x=100, y=700)         # slide refine, bound 1
            key('W', x=300, y=700)         # bound 2 — logs edit_set_slide
            key('enter')                   # commit + pin + sink
        w = s.bundles[0]
        uid = _selected_uid(w)
        entry = json.loads(s.bdb.meta_get(f"user_ops:1:{uid}", ""))
        assert entry["base"] == "new"
        ops = entry["ops"]
        assert any(op.startswith("edit_add_trunk V") for op in ops)
        assert any(op.startswith("edit_set_slide 0") for op in ops)
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')
