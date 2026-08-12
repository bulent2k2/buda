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
import textwrap

from . import install


def _main(module, func="main"):
    install()
    __import__(module)
    return getattr(sys.modules[module], func)()


# ── help ──────────────────────────────────────────────────────────────────
# `-h`/`--help` is answered HERE, before the module is imported, for the two
# GUI commands — which is exactly why `bin/fp` and `bin/viz` answer it in bash
# before they exec anything.  A GUI entry point imports tkinter and picks a
# matplotlib backend at module scope, so "tell me what this command does" would
# otherwise need a display to answer: measured on the installed copy,
# `buda-fp --help` reported `cannot open BDB — file not found: --help` and
# `buda-viz --help` died in `import tkinter`.
#
# `buda` is NOT in this table.  Its help comes from argparse inside `buda_cli`,
# which knows the real option list; intercepting it here would replace a
# generated answer with a copy that goes stale.
#
# These texts describe the INSTALLED commands and are deliberately not copies
# of the wrappers' — `bin/fp` can point at `bin/bfp` and a `.buda` flow, which
# a pip install does not carry, and the command is spelled differently.
_HELP = {
    "buda-fp": """
        Usage: buda-fp [file.bdb]

        Open the interactive BUDA Floorplanner (Tk/matplotlib GUI) to edit
        block placement in a BDB and, optionally, launch the hier routing flow.

        Arguments:
          file.bdb    BDB to open; omit for a blank canvas

        Needs a display and a working tkinter.  See the Floorplanner User Guide
        in the BUDA docs.
    """,
    "buda-viz": """
        Usage: buda-viz [file.def [file.lef]] [--ipc <name>]
               buda-viz [file.bdb] [--ipc <name>]

        Open the interactive DEF/LEF/BDB net-cluster visualizer (Tk GUI).

        Arguments:
          file.def [file.lef]   DEF to load (a sibling .lef is auto-found if
                                omitted)
          file.bdb              a BDB to load instead of DEF/LEF
          --ipc <name>          selection-sync IPC channel to pair with the
                                BUDA viewer (defaults to the input file's stem)

        Needs a display and a working tkinter.
    """,
}


def _helped(command):
    """Print `command`'s help and return True, if help is what was asked for."""
    if not any(a in ("-h", "--help") for a in sys.argv[1:]):
        return False
    print(textwrap.dedent(_HELP[command]).strip())
    return True


def buda():
    """`buda <script.buda>` — the routing CLI (`src/buda_cli.py`)."""
    return _main("buda_cli")


def floorplanner():
    """`buda-fp [file.bdb]` — the interactive Floorplanner GUI."""
    if _helped("buda-fp"):
        return 0
    return _main("bdb_floorplanner")


def viz():
    """`buda-viz [file.def|file.bdb]` — the DEF/LEF/BDB cluster visualizer."""
    if _helped("buda-viz"):
        return 0
    return _main("def_viz_o3")


# There is deliberately NO `buda-server` script.  `tools/buda.tcl` resolves
# its engine child as a FILE beside itself (`[file dirname [info script]]`),
# which is why the wheel keeps `buda.tcl` and `buda_server.py` adjacent in
# `buda_runtime/tools/`; a console script cannot stand in for a path Tcl
# computes.  And the server distinguishes being run from being imported
# (`__name__ == "__main__"` decides whether it claims the protocol channel),
# so calling its `main()` through an entry point would leave it talking the
# protocol on a channel it never took.

