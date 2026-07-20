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

"""Per-bundle floorplans in the web render payload.

`serialize_bundle` now emits each bundle's OWN `floorplan`/`hanan`, computed in
the frame its candidates were generated in (`bundle_floorplan`).  For the FLAT
flow every bundle shares `session.fp`, so the per-bundle frame duplicates the
top-level one and nothing changes.  For the HIERARCHY-AWARE flow a pre-expansion
HBundle lives in a cell-local / endpoint frame that differs from a top-level
bundle's die frame — one shared top-level floorplan cannot represent an
unfocused multi-bundle render, so each bundle carries its own.
"""
import io
import contextlib
import json

import buda
import buda_cli
from web import runner, serialize


def _run(s, cmds):
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in cmds:
            runner.run_one(s, c)


# ── flat flow: per-bundle floorplan duplicates the top-level one ──────────────
_B44_SETUP = [
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
]


def test_flat_bundle_floorplan_equals_top_level():
    """The flat b44 flow shares one floorplan, so every bundle's per-bundle
    `floorplan`/`hanan` must be byte-identical to the top-level payload — the
    additive fields do not perturb the flat case (and the golden top-level is
    unchanged, asserted by test_web_serialize.test_generation_golden_snapshot)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, _B44_SETUP + ["run_bundler", "generate_topologies"])
    payload = json.loads(json.dumps(serialize.serialize_generation(s)))
    assert len(payload["bundles"]) == 1
    for b in payload["bundles"]:
        assert b["floorplan"] == payload["floorplan"]
        assert b["hanan"] == payload["hanan"]


# ── hier flow: two bundles in different frames get different floorplans ────────
def _hier_session(bdb_path):
    """The hier_mixed fixture: two proc cells instantiated under a top with
    src/snk blocks.  `run_hier_bundler depth 1` yields cell-local (level-1) and
    top-level (level-0) bundles that resolve to DIFFERENT floorplans."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, [
        "source flow/rnr/mix_tracks.buda",
        f"open_bdb {bdb_path}",
        "derive_busterms 1",
        "run_hier_bundler depth 1",
        "generate_hier_topologies",
    ])
    return s


def test_hier_bundles_have_distinct_floorplans(bdb_input):
    """An unfocused hier render carries a per-bundle frame: at least two bundles
    resolve to DIFFERENT block sets (cell-local `pa_i/pb_i/pc_i` vs. top-level
    `proc_a/…`), which one shared top-level floorplan could not represent."""
    s = _hier_session(bdb_input("hier_mixed"))
    payload = json.loads(json.dumps(serialize.serialize_generation(s)))
    bundles = payload["bundles"]
    assert len(bundles) >= 2

    # Each bundle's per-bundle floorplan matches what bundle_floorplan resolves
    # (the frame its candidates were generated in), NOT necessarily the top one.
    def block_set(fp):
        return frozenset(b["name"] for b in fp["blocks"])

    frames = {block_set(b["floorplan"]) for b in bundles}
    assert len(frames) >= 2, (
        "expected >=2 distinct per-bundle floorplans in the hier render; "
        f"got frames {frames}")

    # A concrete cell-local vs. top-level pair differs in block names AND coords.
    cell_local = next(b for b in bundles
                      if any(n["name"] == "pa_i" for n in b["floorplan"]["blocks"]))
    top_level = next(b for b in bundles
                     if any(n["name"].startswith("proc_a/")
                            for n in b["floorplan"]["blocks"]))
    assert block_set(cell_local["floorplan"]) != block_set(top_level["floorplan"])
    assert cell_local["hanan"] != top_level["hanan"]

    # The per-bundle floorplan is the SAME frame bundle_floorplan resolves.
    for w, sb in zip(s.bundles, bundles):
        fp = serialize.bundle_floorplan(s, w)
        assert block_set(sb["floorplan"]) == frozenset(
            n for n, _ in fp.get_all_blocks())
