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

"""The environment a test needs to run BUDA in a SUBPROCESS.

`pytest.ini`'s `pythonpath = build src` puts the extension on the path of the
pytest process only.  A test that runs `src/buda_cli.py` as a child has to say
so itself, and about fifteen of them each said it their own way -- six
spellings of one idea, five of which are wrong on Windows for one or both of
these reasons:

**The separator is `os.pathsep`, not `:`.**  It is `;` on Windows, and a
Windows path contains a colon of its own (`D:\\a\\buda\\build`), so a
`:`-joined value is not merely split wrongly -- it is one nonsense entry, and
the child dies with `ModuleNotFoundError: No module named 'buda'`.

**PREPEND to the inherited value, never replace it.**  Under the Visual
Studio multi-config layout the extension lands in `build\\Release`, which
only the caller's environment knows about; replacing orphans the child even
with the separator right.  That is not deduced -- `test_audit3.py` and
`test_floorplanner_headless_backend.py` each learned it from a real run
(doc-validation run 8, windows-validate run 19) and wrote it down, and this
module is where those two comments now live so the next site inherits the
lesson instead of rediscovering it.

Directories are resolved against `root`, so a caller cannot accidentally
depend on the current directory being the repo root either.
"""
import os
from pathlib import Path

__all__ = ["buda_env", "DEFAULT_DIRS"]

# `build` holds the compiled extension; `tools` is a package flows import.
DEFAULT_DIRS = ("build", "tools")


def buda_env(root, *dirs, **overrides):
    """`os.environ` with `dirs` prepended to PYTHONPATH, portably.

    `dirs` are names relative to `root` (default `build`, `tools`); pass
    explicit ones where a test needs `src` too.  `overrides` are applied last,
    so `buda_env(root, MPLBACKEND="Agg")` reads naturally.
    """
    env = dict(os.environ)
    parts = [str(Path(root) / d) for d in (dirs or DEFAULT_DIRS)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.update({k: str(v) for k, v in overrides.items()})
    return env
