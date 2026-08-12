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

"""Where BUDA's Python layer is — the ONE place that knows.

BUDA's import contract has always been a set of directories rather than a
package: `bin/activate` exports `PYTHONPATH=build:tools`, `pytest.ini` says
`pythonpath = build src`, and `bin/buda` adds `build` and `tools`.  So
`buda_cli`, `buda_cmds`, `buda_viz`, `bdb_serialize` and the compiled `buda`
are all TOP-LEVEL names, and every import in the repo is written that way.

A `pip install` has to reproduce that contract, and it cannot reproduce it by
copying the directory names: `build/` is a build product, `src/` and `tools/`
are names no distribution may claim in `site-packages`.  So the wheel folds
the whole layer into ONE directory — this one — and the only difference
between a checkout and an installed copy is which directories the layer is
spread across:

    checkout                       installed wheel
    --------                       ---------------
    <repo>/build   (.so)           <site-packages>/          (.so, at the root
    <repo>/src                     <site-packages>/buda_runtime/    so `import
    <repo>/tools                   <site-packages>/buda_runtime/tools/  buda`
    <repo>        (`tools` pkg)    <site-packages>/buda_runtime/  needs nothing)

`paths()` is that table, and `install()` puts it on `sys.path`.  Both entry
points bootstrap through here rather than each carrying its own idea of the
layout — two copies of a rule that only ever gets exercised in one layout at
a time is exactly how a packaged build comes apart six months later.

Nothing else lives here.  Importing this module has no side effect; you have
to ask for `install()`.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_UP = os.path.dirname(_HERE)


def in_checkout():
    """True when this file sits in a source checkout rather than a wheel.

    The discriminator is the CMakeLists that BUILDS us, which is next to a
    checkout's `buda_runtime/` and is never installed.  Probing for `build/`
    or `src/` instead would be a guess: `build` is a real distribution (the
    PEP 517 frontend) and installs as `site-packages/build/`, so a machine
    with it would read as a checkout.
    """
    return os.path.isfile(os.path.join(_UP, "CMakeLists.txt"))


def paths():
    """The directories holding the Python layer, in IMPORT ORDER.

    Only directories that exist are returned, so this is also the answer to
    "is the extension built yet" — a checkout with no `build/` gets a list
    without it and the `import buda` that follows fails saying so, rather
    than the path list quietly claiming a directory that is not there.
    """
    if in_checkout():
        # `build` FIRST and deliberately: a stale `.so` copied next to the
        # sources must never win over the fresh one (`bin/bb` deletes such
        # copies for the same reason).  `_UP` last — it is what makes
        # `from tools import bdb2buda` resolve to the repo's own `tools`
        # package (see tools/__init__.py for why that package exists).
        cand = (os.path.join(_UP, "build"), os.path.join(_UP, "src"),
                os.path.join(_UP, "tools"), _UP)
    else:
        # Installed: the extension modules are at the wheel root, which is
        # already on `sys.path`, so only the folded layer needs adding.
        # `_HERE` carries the `src/` modules AND the `tools` package;
        # `_HERE/tools` carries the same files as top-level modules, which
        # is how `open_bdb` reaches `bdb_serialize`.
        cand = (_HERE, os.path.join(_HERE, "tools"))
    return [d for d in cand if os.path.isdir(d)]


def install():
    """Put `paths()` on `sys.path`, in order, ahead of everything else.

    Idempotent: a directory already present is left where it is rather than
    moved to the front, so calling this twice cannot reorder a caller's own
    `sys.path`.  Returns what it worked from.
    """
    where = paths()
    for d in reversed(where):
        if d not in sys.path:
            sys.path.insert(0, d)
    return where
