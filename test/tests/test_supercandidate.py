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

"""Super-candidate grouping + group-pin (select_topology group:<N>).

The default-on Hanan loci produce many near-identical candidates that differ
only in the nominal perp of their trunk (b44's TRUNK_H@y10830/y11330/y11830).
`_loci_groups` collapses those into families for INSPECTION (dump_topologies
--grouped) WITHOUT dropping any, and a group-pin restricts the planner to a
family's members so it refines WHICH member wins — byte-identical when unused,
and byte-identical to an unpinned plan when the pinned family is the natural
winner.
"""
import contextlib
import io
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

# A 3-block fan-out bus whose trunk has several Hanan-line loci → a candidate
# pool with genuine nominal-locus families (verified: ~36 cands / ~19 families).
_SETUP = [
    "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
    "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
    "add_block A 8000 10000 9000 11000",
    "add_block B 2000 3000 3000 4000",
    "add_block C 0 20000 500 20500",
    "add_bus dbus[52] A.p B.p,C.p",
    "run_bundler", "generate_topologies",
]


def _session(extra=()):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in list(_SETUP) + list(extra):
            s.do_command(c)
    return s


def _groups(s, w):
    return s._loci_groups(w, s._make_topo_fp_resolver()(w))


def test_loci_groups_collapse_without_dropping():
    s = _session()
    w = s.bundles[0]
    n = len(w.input.candidates)
    groups = _groups(s, w)
    # Fewer families than candidates, but every index is covered exactly once
    # (nothing dropped — this is presentation grouping, not dedup).
    assert 1 < len(groups) < n
    flat = sorted(i for g in groups for i in g)
    assert flat == list(range(n))
    # Representative-first: group[0] is the lowest index (== lowest WL, sorted).
    assert all(g == sorted(g) and g[0] == min(g) for g in groups)
    assert any(len(g) > 1 for g in groups)          # at least one real family


def test_pinned_group_defaults_empty():
    # The new field is empty by default, so planning is byte-identical unused.
    s = _session()
    assert list(s.bundles[0].input.pinned_group) == []


def test_group_pin_restricts_to_family_members():
    s = _session()
    w = s.bundles[0]
    fam = next(g for g in _groups(s, w) if len(g) > 1)
    # Pin via a NON-representative member — the whole family is pinned.
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"select_topology 1 group:{fam[1] + 1}")
    assert list(w.input.pinned_group) == fam
    assert w.input.topology_pinned is False          # group pin, not single pin
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_planner")
    # The planner refined to a member OF THE FAMILY (never outside it).
    assert w.plan.selected_topology_index in fam


def test_group_pin_actually_constrains_away_from_the_global_best():
    # The teeth of the restriction: pin a family that does NOT contain the
    # unpinned winner, and prove the planner is forced to a member of THAT
    # family (not the global optimum it would otherwise pick).
    s = _session(["run_planner"])
    w = s.bundles[0]
    sel0 = w.plan.selected_topology_index
    other = next((g for g in _groups(s, w) if sel0 not in g), None)
    assert other is not None, "need a family without the natural winner"
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"select_topology 1 group:{other[0] + 1}")
        s.do_command("run_planner")
    assert w.plan.selected_topology_index in other
    assert w.plan.selected_topology_index != sel0


def test_group_pin_of_natural_family_is_byte_identical():
    # The strongest guarantee: pinning the family that CONTAINS the unpinned
    # winner reproduces the exact same selection — the group-pin only restricts,
    # the planner still picks the same lowest-cost member.
    s = _session(["run_planner"])
    w = s.bundles[0]
    sel0 = w.plan.selected_topology_index
    fam = next(g for g in _groups(s, w) if sel0 in g)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"select_topology 1 group:{sel0 + 1}")
        s.do_command("run_planner")
    assert w.plan.selected_topology_index == sel0


def test_single_pin_clears_group_pin():
    s = _session()
    w = s.bundles[0]
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("select_topology 1 group:1")
    assert list(w.input.pinned_group) != []
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("select_topology 1 1")          # single pin
    assert list(w.input.pinned_group) == []          # group pin cleared
    assert w.input.topology_pinned is True


def test_unpin_star_clears_group_pin():
    # unpin_topology * must clear the new field too, or the "unpinned" bundle
    # stays constrained to the old family (Codex).
    s = _session()
    w = s.bundles[0]
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("select_topology 1 group:1")
    assert list(w.input.pinned_group) != []
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("unpin_topology *")
    assert list(w.input.pinned_group) == []
    assert w.input.topology_pinned is False


def test_group_pin_clears_stale_seg_overrides():
    # A family the planner may re-choose within must not carry a prior
    # candidate's per-segment overrides (Codex).
    s = _session()
    w = s.bundles[0]
    w.plan.seg_slide_lo = [1, 2, 3]
    w.plan.seg_slide_hi = [4, 5, 6]
    w.plan.seg_net_pull = [7]
    w.plan.seg_perp = [8]
    fam = next(g for g in _groups(s, w) if len(g) > 1)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"select_topology 1 group:{fam[0] + 1}")
    assert list(w.plan.seg_slide_lo) == [] and list(w.plan.seg_slide_hi) == []
    assert list(w.plan.seg_net_pull) == [] and list(w.plan.seg_perp) == []


def test_group_pin_survives_pool_resort():
    # After generate_more_topologies resorts the pool, the raw group indices
    # must be remapped by uid so they still name the SAME family (Codex).
    import buda
    s = _session()
    w = s.bundles[0]
    fam = next(g for g in _groups(s, w) if len(g) > 1)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"select_topology 1 group:{fam[0] + 1}")
    before = {buda.topo_uid(w.input.candidates[i]) for i in w.input.pinned_group}
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("generate_more_topologies dbus")     # resorts the pool
    after = {buda.topo_uid(w.input.candidates[i]) for i in w.input.pinned_group}
    assert before == after and len(w.input.pinned_group) == len(fam)


def test_dump_grouped_reduces_rows(capsys):
    s = _session()
    n = len(s.bundles[0].input.candidates)
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        s.do_command("dump_topologies --grouped")
    out = buf.getvalue()
    assert "famil" in out                            # header shows family count
    assert "family:+" in out                         # at least one collapsed row
    # The grouped view prints strictly fewer candidate rows than N.
    n_rows = sum(1 for ln in out.splitlines()
                 if ln.strip()[:1].isdigit() and "TRUNK" in ln)
    assert 0 < n_rows < n


def test_dump_grouped_emits_pinnable_token():
    """Every --grouped row prints the exact `group:<N>` token it takes.

    The `idx` column is 0-based and the ordinal position among families is NOT
    the pin id, so users must copy the representative's 1-based candidate id.
    The dump prints it verbatim as `group:<N>`; feeding THAT TOKEN back —
    unedited — to `select_topology <bundle> <token>` must pin the SAME family
    (the row's representative candidate becomes the selection). No `pin=` label
    or reconstruction: what the dump shows is what the command accepts.
    """
    s = _session()
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        s.do_command("dump_topologies --grouped")
    out = buf.getvalue()
    assert "group:" in out
    # Pull the first row's (idx, group:N mark) pair and feed the mark BACK
    # verbatim — it must pin the family whose representative is that idx cand.
    for ln in out.splitlines():
        toks = ln.strip().split()
        if toks and toks[0].isdigit() and "group:" in ln:
            idx = int(toks[0])
            token = next(m for m in ln.split()[-1].split(",")
                         if m.startswith("group:"))
            assert token == f"group:{idx + 1}"   # 1-based candidate id, verbatim
            with contextlib.redirect_stdout(io.StringIO()):
                s.do_command(f"select_topology 1 {token}")  # token unedited
            w = s.bundles[0]
            assert idx in w.input.pinned_group   # the rep is in the pinned family
            assert w.plan.selected_topology_index == w.input.pinned_group[0]
            break
    else:
        raise AssertionError("no `group:` row found in --grouped output")


def test_incoherent_single_pin_does_not_crash_planner():
    """topology_pinned=True with an out-of-range selected_topology_index must
    NOT segfault the planner (issue #454).

    The supported CLI never produces this state (select_topology sets the flag
    AND a valid index together), but the pybind fields are independently
    writable, so Python can set the pin flag while the index is still at its
    default -1 (or leave a stale index after the pool shrank). The planner must
    treat an incoherent pin as "not usefully pinned" and plan from the full
    pool rather than index candidates[] out of bounds.
    """
    n = len(_session().bundles[0].input.candidates)
    assert n > 1

    for bad_index, label in [(-1, "default -1"), (n + 5, "stale over-range")]:
        s = _session()
        w = s.bundles[0]
        w.input.topology_pinned = True
        w.plan.selected_topology_index = bad_index
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command("run_planner")          # must not SIGSEGV
        # Fell back to the full sweep and chose a valid candidate.
        assert 0 <= w.plan.selected_topology_index < n, label
