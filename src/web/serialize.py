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

"""Struct -> JSON serializers for the web backend.

PURE and PRINT-FREE: every function reads the pybind C++ result structs
(`session.bundles[i].{input,plan}`, `session.nuts_result`,
`session.detailed_result`, `buda.ConnTopology.segs()`, Floorplan accessors) and
returns plain dicts/lists of JSON scalars.  The topology traversal mirrors the
golden-test walker in `tools/topo_snapshot.py`, emitting dicts instead of text.

Nothing here parses the human-facing print output of any command — all routed
data is available structured on the objects above.
"""

# ConnTopology marks an unbounded slide window / undefined pull with an INT
# sentinel (~1e9 magnitude); mirror the CLI/viz threshold so the client can tell
# "unconstrained" (JSON null) from a real bound.  (viz_common.py uses 1e9;
# nutsflow/reports use 1e8 — 1e9 is the safe upper discriminator.)
_SENTINEL = 10 ** 9


def _net_names(original_bundle):
    """Bundle net names, tolerant of the flat vs hier bundle interface."""
    try:
        return list(original_bundle.get_net_names())
    except Exception:
        return list(getattr(original_bundle, "net_names", []) or [])


def serialize_state(session):
    """Lightweight `StateSummary` — which stages have run + a per-bundle digest.

    stages_run is inferred from observable session state (no bespoke flags):
      bundler    -> any bundles exist
      topologies -> any bundle has candidates
      planner    -> any bundle's plan carries assigned seg_layers
      nuts/dnuts -> the corresponding result object exists
    """
    bundles = list(getattr(session, "bundles", []) or [])

    def _planned(w):
        try:
            return len(list(w.plan.seg_layers)) > 0
        except Exception:
            return False

    stages_run = {
        "bundler": len(bundles) > 0,
        "topologies": any(len(w.input.candidates) > 0 for w in bundles),
        "planner": any(_planned(w) for w in bundles),
        "nuts": getattr(session, "nuts_result", None) is not None,
        "dnuts": getattr(session, "detailed_result", None) is not None,
    }

    bundle_digests = []
    for w in bundles:
        ob = w.input.original_bundle
        bundle_digests.append({
            "id": ob.id,
            "net_count": len(_net_names(ob)),
            "num_candidates": len(w.input.candidates),
            "selected_index": w.plan.selected_topology_index,
            "pinned": bool(getattr(w.input, "topology_pinned", False)),
            "width": w.input.width,
        })

    return {
        "stages_run": stages_run,
        "bundles": bundle_digests,
        "has_bdb": getattr(session, "bdb", None) is not None,
    }
