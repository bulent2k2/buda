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

# mid tier: qor_corpus --vs baseline machinery over real git worktrees — a
# heavy xdist contender, so out of the fast inner loop.  Still validated on
# Windows (windows-validate.yml runs `-m "not slow"`), which is where the
# worktree path handling most needs to run.
pytestmark = pytest.mark.mid

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "qor_corpus", _ROOT / "tools" / "qor_corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree_state():
    """Everything a stash-based baseline would disturb: the porcelain status,
    HEAD, and the stash depth.

    UNTRACKED entries are excluded, and that is the whole point of the filter:
    this is a snapshot of the WHOLE repo, but other xdist workers write
    temporary files INSIDE it — `test_bit_antenna_audit` does
    `mkstemp(dir=flow.parent, prefix="_no_trim_")` so a generated flow's
    relative `source` paths resolve beside its siblings.  `--dist loadfile`
    keeps one FILE on one worker, not one repo on one worker, so such a file
    can appear or vanish between the two snapshots and fail a test that has
    nothing to do with it (measured on CI: `?? flow/rnr/_no_trim_yx7f4yi3.buda`
    present in `before`, gone by the final assert).

    Nothing is lost by excluding them: what this guard is for is the baseline
    operation moving OUR tree — a stray checkout, a stash, a modified tracked
    file — and the worktree itself is asserted to live OUTSIDE the repo, so
    "untracked junk left in the repo" was never the signal it carried.
    """
    def git(*a):
        return subprocess.run(["git", *a], cwd=_ROOT, capture_output=True,
                              text=True).stdout
    tracked = "\n".join(l for l in git("status", "--porcelain").splitlines()
                        if not l.startswith("??"))
    return (tracked, git("rev-parse", "HEAD"), git("stash", "list"))


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


def _git(cwd, *a, **kw):
    r = subprocess.run(["git", *a], cwd=str(cwd), capture_output=True,
                       text=True, **kw)
    assert r.returncode == 0, f"git {' '.join(a)}:\n{r.stdout}{r.stderr}"
    return r.stdout.strip()


@pytest.fixture
def stale_repo(tmp_path):
    """A work repo whose local `main` is one commit behind `origin/main`, with
    HEAD on a branch that forked BEFORE that commit.

    Both halves of the trap in one fixture, because they are one situation: a
    topic-branch workflow never checks `main` out, so the local ref goes stale
    exactly while the branch falls behind the real baseline."""
    origin, work, other = (tmp_path / n for n in ("origin.git", "work", "other"))
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-b", "main", str(work))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(work, "config", k, v)
    (work / "f").write_text("1")
    _git(work, "add", "f")
    _git(work, "commit", "-m", "c1")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    c1 = _git(work, "rev-parse", "HEAD")

    # Somebody else advances main.
    _git(tmp_path, "clone", str(origin), str(other))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(other, "config", k, v)
    (other / "f").write_text("2")
    _git(other, "commit", "-am", "c2")
    _git(other, "push", "origin", "main")

    _git(work, "fetch", "origin")          # origin/main moves, local main does not
    _git(work, "checkout", "-b", "feat", c1)
    (work / "g").write_text("x")
    _git(work, "add", "g")
    _git(work, "commit", "-m", "mine")
    return work


def test_a_stale_local_ref_is_reported(stale_repo, monkeypatch):
    """`--vs main` resolves the LOCAL ref.  In a topic-branch workflow that ref
    is whatever it was when you last touched it, and nothing about the run
    looks wrong — both sweeps succeed and the table prints."""
    qc = _load()
    monkeypatch.setattr(qc, "_ROOT", stale_repo)
    commit = _git(stale_repo, "rev-parse", "main^{commit}")
    lines = qc.baseline_advisories("main", commit)
    assert any("behind its upstream" in l and "origin/main" in l for l in lines), lines


def test_a_baseline_the_tree_does_not_contain_is_reported(stale_repo,
                                                          monkeypatch):
    """The shape that actually misattributes a delta — and a FRESH ref does not
    prevent it.  Here the baseline is `origin/main`, perfectly up to date, yet
    it holds a commit the branch forked before, so that commit's effects would
    be reported as the branch's."""
    qc = _load()
    monkeypatch.setattr(qc, "_ROOT", stale_repo)
    commit = _git(stale_repo, "rev-parse", "origin/main^{commit}")
    lines = qc.baseline_advisories("origin/main", commit)
    assert not any("behind its upstream" in l for l in lines), \
        "origin/main is not stale; only the ancestry advisory applies"
    assert any("your working tree does not" in l for l in lines), lines


def test_an_ancestor_baseline_says_nothing(stale_repo, monkeypatch):
    """The normal case must stay quiet, or the advisory becomes noise people
    learn to skip past."""
    qc = _load()
    monkeypatch.setattr(qc, "_ROOT", stale_repo)
    head = _git(stale_repo, "rev-parse", "HEAD")
    assert qc.baseline_advisories("HEAD", head) == []
    # A raw SHA has no upstream to be stale against, and is an ancestor here.
    assert qc.baseline_advisories(head, head) == []


def test_measuring_your_own_tip_is_never_called_stale(stale_repo, monkeypatch):
    """`--vs HEAD` names ONE commit unambiguously, so remote drift cannot make
    it the wrong baseline — the delta is your uncommitted work either way.

    The case that forced this: a REBASE leaves the remote copy of your branch
    holding commits your HEAD does not, so a naive upstream check fires on
    every rebase-then-measure and tells you to `git fetch`, which is the wrong
    action.  An advisory that cries wolf on a routine workflow is one people
    learn to skip past."""
    qc = _load()
    monkeypatch.setattr(qc, "_ROOT", stale_repo)
    _git(stale_repo, "push", "-u", "origin", "feat")
    _git(stale_repo, "commit", "--amend", "-m", "mine, reworded")  # a rebase-alike
    head = _git(stale_repo, "rev-parse", "HEAD")
    behind = _git(stale_repo, "rev-list", "--count", "HEAD..origin/feat")
    assert behind == "1", "the fixture must actually leave the remote ahead"
    assert qc.baseline_advisories("HEAD", head) == []


def test_the_advisory_is_printed_before_the_builds(tmp_path, monkeypatch,
                                                   capsys):
    """Ordering is the point: the two sweeps cost tens of minutes, so an
    advisory printed after them has already let you act on a wrong number."""
    qc = _load()
    seen = []
    monkeypatch.setattr(qc, "baseline_worktree", lambda rev: (tmp_path, "c" * 40))
    monkeypatch.setattr(qc, "baseline_advisories",
                        lambda rev, commit: ["the sky is falling"])
    def fake_build(tree, label):
        seen.append("build")
        print(f"[--vs] building {label}...")     # as the real one does
    monkeypatch.setattr(qc, "_build", fake_build)
    monkeypatch.setattr(qc, "_sh",
                        lambda *a, **kw: seen.append("baseline sweep"))
    monkeypatch.setattr(qc, "cmd_run",
                        lambda *a, **kw: seen.append("branch sweep"))
    monkeypatch.setattr(qc, "cmd_compare", lambda base, mine: 0)
    qc.cmd_vs("main", str(tmp_path / "o.json"), jobs=1, flows=["x.buda"])

    out = capsys.readouterr().out
    assert "WARNING: the sky is falling" in out, out
    assert out.index("WARNING") < out.index("building"), \
        "the advisory came after the build it should have preceded"
    assert seen[0] == "build", seen


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


def _clean_tracked_probe():
    """A tracked file with no uncommitted modification, for the probe above."""
    import subprocess
    dirty = set()
    out = subprocess.run(["git", "status", "--porcelain"], cwd=_ROOT,
                         capture_output=True, text=True).stdout
    for ln in out.splitlines():
        if len(ln) > 3:
            dirty.add(ln[3:].strip())
    for cand in ("tools/qor_corpus.py", "tools/qor_table.py",
                 "tools/qor_corpus.py", "pytest.ini", "CMakeLists.txt"):
        if cand not in dirty and (_ROOT / cand).is_file():
            return _ROOT / cand
    import pytest as _pt
    _pt.skip("no clean tracked file available to probe with")


def test_the_tree_snapshot_ignores_another_workers_temp_file():
    """A parallel worker's untracked file must not read as "the tree moved".

    Reproduces the CI failure directly rather than by running the suite twice:
    `test_bit_antenna_audit` writes `flow/rnr/_no_trim_*.buda` inside the repo,
    and this file's snapshot covers the whole repo, so under xdist the two were
    racing.  Same name, same directory — if the filter regresses, this fails
    deterministically instead of one run in N.
    """
    import os
    before = _tree_state()
    stray = _ROOT / "flow" / "rnr" / "_no_trim_snapshot_probe.buda"
    stray.write_text("# another worker's scratch flow\n")
    try:
        assert _tree_state() == before, (
            "an unrelated untracked file changed the snapshot — the whole-repo "
            "porcelain is shared mutable state across xdist workers")
        # ...and the guard still SEES what it exists to catch: a tracked file
        # modified under it.
        #
        # The probe must be a file that is CURRENTLY CLEAN.  A hardcoded one
        # (this was `tools/qor_corpus.py`) makes the test fail for anyone with
        # uncommitted edits to it: porcelain already lists the path, so
        # appending does not move the snapshot and the assertion below reads as
        # a regression in the filter.  Measured while editing that very file --
        # a spurious red that costs a diagnosis, which is the same
        # cries-wolf failure docs/internal/measurement_hazards.md is about.
        tracked = _clean_tracked_probe()
        # BYTES, not text: `read_text`/`write_text` translate newlines, so on
        # Windows the restore writes CRLF where the checkout had LF and the
        # probe file stays genuinely MODIFIED -- the final assert then fails
        # having itself dirtied the tree.  It bit only the lane that forces an
        # LF checkout (measured, windows-validate run 36: mingw alone, msbuild
        # and ninja clean because their CRLF worktree round-tripped).  A
        # save/restore must be byte-exact or it is not a restore.
        orig = tracked.read_bytes()
        tracked.write_bytes(orig + b"\n# probe\n")
        try:
            assert _tree_state() != before, \
                "a modified TRACKED file no longer moves the snapshot"
        finally:
            tracked.write_bytes(orig)
    finally:
        os.unlink(stray)
    assert _tree_state() == before
