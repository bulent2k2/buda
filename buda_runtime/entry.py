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

"""Console-script entry points — the installed twins of `bin/buda` etc.

`bin/buda`, `bin/fp` and `bin/viz` set `PYTHONPATH` and exec a script by
path, which only works from a checkout.  These do the same job through
`buda_runtime.install()`, so they work in both layouts and there is one
statement of where the layer is.

The wrappers keep their extra behaviour (the macOS `.app` relaunch, `bin/buda`'s
argument anchoring) and remain the developer path; nothing here replaces them.

Every import below is TOP-LEVEL on purpose — `import buda_cli`, never
`from . import buda_cli`.  In the installed layout the same file is reachable
under both names, and importing it under both would give the process two
distinct module objects with two copies of the session state.
"""
import sys

from . import install


def _main(module, func="main"):
    install()
    __import__(module)
    return getattr(sys.modules[module], func)()


def buda():
    """`buda <script.buda>` — the routing CLI (`src/buda_cli.py`)."""
    return _main("buda_cli")


def floorplanner():
    """`buda-fp [file.bdb]` — the interactive Floorplanner GUI."""
    return _main("bdb_floorplanner")


def viz():
    """`buda-viz [file.def|file.bdb]` — the DEF/LEF/BDB cluster visualizer."""
    return _main("def_viz_o3")


# There is deliberately NO `buda-server` script.  `tools/buda.tcl` resolves
# its engine child as a FILE beside itself (`[file dirname [info script]]`),
# which is why the wheel keeps `buda.tcl` and `buda_server.py` adjacent in
# `buda_runtime/tools/`; a console script cannot stand in for a path Tcl
# computes.  And the server distinguishes being run from being imported
# (`__name__ == "__main__"` decides whether it claims the protocol channel),
# so calling its `main()` through an entry point would leave it talking the
# protocol on a channel it never took.

