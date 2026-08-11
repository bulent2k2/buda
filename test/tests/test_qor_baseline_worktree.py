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

"""`qor_corpus.py --vs` must measure a baseline WITHOUT touching the tree.

That is the whole reason the mode exists, so it is the thing to pin.  The
alternative it replaces — stash, build, measure, pop — makes the measurement a
mutation of the working tree, and each of its failure modes yields a WRONG
NUMBER rather than an error: a concurrent edit corrupts the run, a commit
mid-run leaves nothing to stash so the "baseline" silently measures the branch
and reports a vacuous no-difference, and an interrupt leaves the tree stashed.

These tests are deliberately cheap: they exercise the worktree/cache
machinery and the argument contract, not a corpus sweep (that is measured by
hand, and a 40-minute test is not a test).
"""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "qor_corpus", _ROOT / "tools" / "qor_corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree_state():
    """Everything a stash-based baseline would disturb: the porcelain status,
    HEAD, and the stash depth."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=_ROOT, capture_output=True,
                              text=True).stdout
    return (git("status", "--porcelain"), git("rev-parse", "HEAD"),
            git("stash", "list"))


@pytest.mark.mid
def test_materializing_a_baseline_leaves_the_working_tree_untouched():
    qc = _load()
    before = _tree_state()
    wt, commit = qc.baseline_worktree("HEAD")
    try:
        assert (wt / "CMakeLists.txt").exists(), "the worktree has no sources"
        # Outside the repo: a baseline built INSIDE it would collide with the
        # working tree's own build/ and flow/log/.
        assert _ROOT not in wt.parents and wt != _ROOT
        assert commit.startswith(
            subprocess.run(["git", "rev-parse", "HEAD"], cwd=_ROOT,
                           capture_output=True, text=True).stdout.strip()[:12])
        assert _tree_state() == before, "materializing the baseline moved the tree"

        # Cached by COMMIT: a corpus baseline carries a full build, so
        # rebuilding it for every `--vs main` would dominate the measurement.
        # Checked HERE rather than in its own test because a second test
        # would key on the same commit and race this one for the directory
        # under xdist.
        (wt / ".reuse-probe").write_text("x")
        wt2, c2 = qc.baseline_worktree("HEAD")
        assert (wt2, c2) == (wt, commit)
        assert (wt2 / ".reuse-probe").exists(), "the worktree was re-created"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=_ROOT, capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=_ROOT,
                       capture_output=True)
    assert _tree_state() == before, "cleaning up the baseline moved the tree"


def test_the_baseline_is_a_commit_not_a_branch_checkout():
    """`--vs` resolves its rev to a commit and checks it out DETACHED.

    Both matter: git allows one checkout per branch, so a non-detached
    worktree would refuse whenever the ref is already checked out, and a
    baseline tied to a moving ref is not a baseline."""
    src = (_ROOT / "tools" / "qor_corpus.py").read_text()
    assert '"--detach"' in src, "the baseline worktree must be detached"
    assert 'rev + "^{commit}"' in src, "the rev must resolve to a commit"


def test_vs_output_paths_are_absolute(tmp_path, monkeypatch):
    """The two sweeps run in DIFFERENT directories — the baseline subprocess
    in the worktree, the branch sweep after chdir'ing to the repo root — so a
    relative `--out` names two different files and `--compare` dies with
    FileNotFoundError AFTER both sweeps, the most expensive moment to fail."""
    qc = _load()
    monkeypatch.chdir(tmp_path)
    mine, base = qc.vs_paths("results.json")
    assert os.path.isabs(mine) and os.path.isabs(base), (mine, base)
    # …and resolved against where the USER is, not where the tool chdirs to.
    assert Path(mine).parent == tmp_path
    assert base.startswith(mine), (mine, base)
    assert mine != base

    # No --out: a private temp dir, absolute by construction and distinct.
    m2, b2 = qc.vs_paths(None)
    assert os.path.isabs(m2) and os.path.isabs(b2) and m2 != b2
    assert Path(m2).parent == Path(b2).parent


def test_a_relative_out_lands_where_the_user_is(tmp_path, monkeypatch):
    """`cmd_run` chdirs to the repo root, so a relative --out used to land
    there rather than in the caller's directory.  A no-op from the repo root
    (the documented usage), and the difference between one file and two under
    --vs."""
    qc = _load()
    stray = _ROOT / "here.json"
    stray.unlink(missing_ok=True)     # a previous failure's artifact is not evidence
    monkeypatch.chdir(tmp_path)
    qc.cmd_run([], "here.json", jobs=1)
    assert (tmp_path / "here.json").exists(), sorted(
        p.name for p in tmp_path.iterdir())
    assert not stray.exists(), "the output landed in the repo root, not the cwd"


def test_no_build_is_rejected_without_vs():
    r = subprocess.run(["python3", str(_ROOT / "tools" / "qor_corpus.py"),
                        "--no-build"], cwd=_ROOT, capture_output=True,
                       text=True)
    assert r.returncode != 0
    assert "only meaningful with --vs" in r.stderr, r.stderr


def test_clean_baselines_reports_when_there_is_nothing_cached(tmp_path,
                                                              monkeypatch):
    qc = _load()
    monkeypatch.setattr(qc.tempfile, "gettempdir", lambda: str(tmp_path))
    assert qc.cached_baselines() == []
    qc.cmd_clean_baselines()          # must not raise on an empty cache


def test_clean_baselines_keeps_the_requested_number(tmp_path, monkeypatch):
    qc = _load()
    monkeypatch.setattr(qc.tempfile, "gettempdir", lambda: str(tmp_path))
    for name in ("buda-qor-aaaaaaaaaaaa", "buda-qor-bbbbbbbbbbbb"):
        d = tmp_path / name
        d.mkdir()
        (d / "f").write_text("x" * 10)
    assert len(qc.cached_baselines()) == 2
    qc.cmd_clean_baselines(keep=1)
    assert len(qc.cached_baselines()) == 1
    qc.cmd_clean_baselines()
    assert qc.cached_baselines() == []
