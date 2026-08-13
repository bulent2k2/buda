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

"""A post-expansion hier pin must persist the PRE-expansion view.

After `run_planner hier`, `session.bundles` is the EXPANDED per-instance
list with synthetic wrapper ids.  `select_topology` at that point (the
interactive prompt of flow/tcl/hdesign.tcl — pin AFTER looking at the route)
triggers a clear-and-rewrite persist that used to walk the expanded list:
the template/replica bundle rows were replaced by per-instance rows carrying
absolute geometry and no expansion marker, and the next session's
`load_pipeline` failed to restore them ("block(s) ... are not in the current
Floorplan").  Three coupled fixes, exercised together here:

* `_persist_wrappers`: bundle/topology persists source the pre-expansion
  originals in a post-expansion hier session;
* `run_planner hier` snapshots `_hier_bundles_orig` on a RESUMED session
  (only the bundler and the bottom-up restore set it before) — also closing
  the C6-09-style re-expansion of the prior run's instance wrappers;
* the expansion-map pin/unpin branches mirror onto the pre-expansion
  original, so an in-session replan honors the pin without the sidecar.
"""
import contextlib
import io

import pytest

pytestmark = pytest.mark.mid

import buda_cli


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _out(session, *cmds):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            session.do_command(c)
    return buf.getvalue()


_SETUP = (
    "def_layer 4 M4 H TOP 30",
    "def_layer 5 M5 V TOP 30",
)

_DESIGN = (
    "add_cell chip_cell 400 500",
    "add_cell blk_cell 180 230",
    "add_inst_to_cell chip_cell top blk_cell 10 260",
    "add_inst_to_cell chip_cell bot blk_cell 10 10",
    "add_inst left chip_cell - 0 0",
    "add_inst right chip_cell - 450 0",
    "derive_busterms 1",
    "add_blocks_from_bdb 0",
    "add_blocks_from_bdb 1 skip",
    "bdb_net_mode on",
    # One cell-local template (4-bit, in every chip instance) + one D0 bus.
    "add_bus b_loc_l[4] left/top.out left/bot.in",
    "add_bus b_loc_r[4] right/top.out right/bot.in",
    "add_bus x[4] left/top.out right/bot.in",
)

_PIPE = ("run_hier_bundler depth 1", "generate_hier_topologies",
         "run_planner hier 3", "run_nuts")


def _bundle_rows(bdb_path):
    import sqlite3
    c = sqlite3.connect(bdb_path)
    try:
        return {r[0]: r[1] for r in
                c.execute("select id, is_expanded from bundle")}
    finally:
        c.close()


def test_post_expansion_pin_keeps_the_checkpoint_restorable(tmp_path):
    bdb = str(tmp_path / "hier.bdb")

    # Session 1: build + route (post-expansion state), then pin a template
    # by hint — the interactive-prompt order.
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"open_bdb {bdb}", *_SETUP, *_DESIGN, *_PIPE)
    pre = {b for b, exp in _bundle_rows(bdb).items() if exp == 0}
    out = _out(s, "select_topology b_loc 2")
    assert "Pinned" in out, out

    # The persist must not have clobbered the pre-expansion rows: every
    # original bundle id is still there, unexpanded.
    post = {b for b, exp in _bundle_rows(bdb).items() if exp == 0}
    assert pre <= post, f"pre-expansion rows lost: {pre - post}"

    # An in-session replan honors the pin (the mirror onto the original),
    # with no reliance on the sidecar restore.
    out = _out(s, "run_planner hier 3")
    assert "[pinned]" in out, out
    del s

    # Session 2: re-declare setup, reopen, load_pipeline — the restore that
    # failed outright before the fix — and the pin is still in force.
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    out = _out(s2, f"open_bdb {bdb}", *_SETUP,
               "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
               "load_pipeline")
    assert "Error" not in out, out
    out = _out(s2, "run_planner hier 3")
    assert "[pinned]" in out, out


def _resume(bdb):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"open_bdb {bdb}", *_SETUP,
           "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
           "load_pipeline")
    return s


def test_post_expansion_pin_of_a_pass_through_bundle_sticks(tmp_path):
    """Expansion rebuilds EVERY wrapper as a fresh object — pass-through
    (cross-block / cross-level) bundles too, not just template instances —
    so a pin found by the direct self.bundles scan must mirror onto the
    pre-expansion original exactly like the expansion-map branch does
    (Codex #723: `pin xl_c2l 3` silently absent from checkpoint + replan)."""
    bdb = str(tmp_path / "hier.bdb")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"open_bdb {bdb}", *_SETUP, *_DESIGN, *_PIPE)

    out = _out(s, "select_topology x 2")           # the D0 pass-through bus
    assert "Pinned" in out, out
    out = _out(s, "run_planner hier 3")            # replan from the originals
    assert "[pinned]" in out, out
    del s

    out = _out(_resume(bdb), "run_planner hier 3")
    assert "[pinned]" in out, out


def test_wildcard_unpin_clears_the_originals_too(tmp_path):
    """`unpin_topology *` in a post-expansion session must clear the
    pre-expansion originals as well — they are what the next replan resets
    from and what the persist serializes, so a template pin cleared only on
    the routed wrappers came back on the next replan or resume."""
    bdb = str(tmp_path / "hier.bdb")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"open_bdb {bdb}", *_SETUP, *_DESIGN, *_PIPE)
    _quiet(s, "select_topology b_loc 2")
    out = _out(s, "unpin_topology *")
    assert "Unpinned all bundles" in out
    out = _out(s, "run_planner hier 3")
    assert "[pinned]" not in out, out              # cleared for THIS replan
    del s
    out = _out(_resume(bdb), "run_planner hier 3")
    assert "[pinned]" not in out, out              # ...and for the next session


def test_persist_bundles_is_batched_and_the_getter_is_not():
    """Inserting _persist_wrappers under _persist_bundles' @_batched line
    silently transferred the transaction wrapper to the read-only getter:
    stage-1 persists ran one autocommit (fsync) per row, and an exception
    midway left a partially rewritten checkpoint instead of rolling back
    (Codex #723).  functools.wraps marks the decorated one."""
    assert hasattr(buda_cli.BudaSession._persist_bundles, "__wrapped__"), \
        "_persist_bundles lost its @_batched transaction wrapper"
    assert not hasattr(buda_cli.BudaSession._persist_wrappers, "__wrapped__"), \
        "_persist_wrappers must stay an undecorated read-only helper"
