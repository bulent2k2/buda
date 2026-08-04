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

"""`from tools import X` must resolve to THIS repo's tools/, never a
third-party distribution that happens to be called `tools`.

`tools` is a plausible PyPI name and the repo does not own it.  One appeared
transitively in CI and broke every such import at once — 17 failures and 6
collection errors on main.  The fix is `tools/__init__.py`; these tests pin it
by constructing the shadowing situation directly, rather than waiting for a
dependency to reintroduce it.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# A representative sample of the modules the suite imports as `from tools import X`.
TOOLS_MODULES = ("bdb2buda", "buda2bdb", "bdb_serialize", "build_hier_demo",
                 "def_cluster", "def_viz_shared", "floorplanner_commands",
                 "gds_build", "qor_corpus", "render", "viz_ipc")


def test_tools_is_a_regular_package():
    """A namespace package loses to ANY regular package of the same name, at any
    sys.path position — so this file is load-bearing, not decoration."""
    init = _ROOT / "tools" / "__init__.py"
    assert init.is_file(), (
        "tools/__init__.py is missing — `tools` reverts to a namespace package "
        "and a third-party `tools` distribution will shadow it")


@pytest.mark.parametrize("mod", TOOLS_MODULES)
def test_repo_tools_modules_are_importable(mod):
    assert (_ROOT / "tools" / f"{mod}.py").is_file(), f"tools/{mod}.py vanished"


def _run(code, extra_path):
    """Run `code` in a fresh interpreter with `extra_path` appended to sys.path."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True, text=True,
        env={"PYTHONPATH": f"{_ROOT}:{extra_path}", "PATH": "/usr/bin:/bin"})


def test_repo_tools_wins_over_a_shadowing_distribution(tmp_path):
    """THE regression.  Build a fake site-packages holding a REGULAR `tools`
    package, put it after the repo root, and confirm the repo still wins.

    Before tools/__init__.py existed this failed with exactly the CI error:
        ImportError: cannot import name 'qor_corpus' from 'tools'
                     (.../site-packages/tools/__init__.py)
    """
    sp = tmp_path / "site-packages"
    (sp / "tools").mkdir(parents=True)
    (sp / "tools" / "__init__.py").write_text("IMPOSTOR = True\n")
    r = _run(f"""
        import tools
        assert not getattr(tools, "IMPOSTOR", False), (
            "the impostor won: " + str(tools.__file__))
        from tools import qor_corpus            # noqa: F401
        print("OK", tools.__file__)
        """, str(sp))
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert str(_ROOT) in r.stdout, r.stdout


def test_the_shadowing_really_would_break_a_namespace_package(tmp_path):
    """The control: the same fake distribution DOES win against a namespace
    package, which is why precedence alone could not have saved us.  Guards the
    explanation in tools/__init__.py against being 'simplified' away."""
    fake_repo = tmp_path / "repo"
    (fake_repo / "tools").mkdir(parents=True)          # namespace: no __init__
    (fake_repo / "tools" / "thing.py").write_text("VALUE = 1\n")
    sp = tmp_path / "site-packages"
    (sp / "tools").mkdir(parents=True)
    (sp / "tools" / "__init__.py").write_text("IMPOSTOR = True\n")
    r = subprocess.run(
        [sys.executable, "-c", "from tools import thing"],
        capture_output=True, text=True,
        env={"PYTHONPATH": f"{fake_repo}:{sp}", "PATH": "/usr/bin:/bin"})
    assert r.returncode != 0, "expected the namespace package to lose"
    assert "cannot import name 'thing' from 'tools'" in r.stderr, r.stderr
