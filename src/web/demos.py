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

"""Built-in demo catalog for the web client.

Each demo is `{key, label, setup, stages}`:
  - `setup`   — the `.buda` commands the client drops into the command textarea
                (floorplan / netlist / BDB hierarchy), run once via /api/command;
  - `stages`  — the command each stage button runs, so the same "setup + click
                stages" UX drives BOTH the flat and the hierarchy-aware flows
                (the hier flow needs `run_hier_bundler` / `generate_hier_topologies`
                / `run_planner hier`).

The hier demo's setup is EXTRACTED from flow/hbundles/06_multipin_stress.buda
(not duplicated): everything up to the first pipeline stage, with the
file-relative `source ../tracks/...` rewritten repo-root-relative so it resolves
from the server's CWD.  Paths assume the server runs from the repo root, same as
the flat b44 demo already does (`source flow/tracks/tracks4top.buda`).
"""
import os

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HIER_FLOW = os.path.join(_REPO, "flow", "hbundles", "06_multipin_stress.buda")

# Commands that begin the routing PIPELINE — the hier setup is everything BEFORE
# the first of these (the stages are driven by the buttons instead).
_PIPELINE_CMDS = frozenset({
    "run_bundler", "run_hier_bundler", "generate_topologies",
    "generate_hier_topologies", "run_planner", "run_nuts", "run_detailed_nuts",
    "check_design", "visualize", "ripup_reroute", "negotiate_congestion",
})

_FLAT_SETUP = "\n".join([
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
])

_FLAT_STAGES = {
    "bundler": "run_bundler",
    "topologies": "generate_topologies",
    "planner": "run_planner",
    "nuts": "run_nuts",
    "dnuts": "run_detailed_nuts",
}

_HIER_STAGES = {
    "bundler": "run_hier_bundler depth 2",
    "topologies": "generate_hier_topologies",
    "planner": "run_planner hier 5",
    "nuts": "run_nuts",
    "dnuts": "run_detailed_nuts",
}


def _hier_setup():
    """Read the 06 hier flow and return its SETUP portion (up to the first
    pipeline stage), comment/blank lines stripped, `source` path repo-rooted."""
    out = []
    with open(_HIER_FLOW, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()        # drop comments
            if not line:
                continue
            if line.split()[0] in _PIPELINE_CMDS:
                break                                  # setup ends here
            if line.startswith("source ../tracks/"):
                line = line.replace("source ../tracks/", "source flow/tracks/")
            out.append(line)
    return "\n".join(out)


def catalog():
    """The demo list the client renders in its picker.  Built fresh each call so
    an edit to the hier flow is reflected without a server restart."""
    return [
        {"key": "flat", "label": "Flat — b44 bus (3 blocks, 52 bits)",
         "setup": _FLAT_SETUP, "stages": _FLAT_STAGES},
        {"key": "hier", "label": "Hierarchical — multi-pin stress (35 buses, depth 2)",
         "setup": _hier_setup(), "stages": _HIER_STAGES},
    ]
