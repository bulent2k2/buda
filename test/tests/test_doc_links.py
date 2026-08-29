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

"""Every documentation link resolves to a file that exists.

The docs here are load-bearing — the reasoning behind a decision usually
lives one link away from the code that implements it — and a link rots
SILENTLY: nothing runs it, so a moved file leaves a trail of references that
still read as if they point somewhere.  Moving the wishlist set into
`docs/internal/wishlist/` (2026-08-28) touched roughly 200 references across
docs, code comments, tests and flow scripts, which is more than a careful
reading can vouch for; this walks them instead.

Two kinds of reference, because the docs use both:

  * a **markdown link** `](path.md)`, resolved relative to the file it is
    written in — the one that breaks when a doc changes directory;
  * a **repo-root path** written in prose or a code comment
    (`docs/internal/wishlist/wishlist-topo.md`), which is how a C++ or
    Python source points at the doc explaining it.

A bare file NAME in prose (`wishlist-topo.md` with no path) is deliberately
not checked: it names a document, not a location, and stays true wherever
the file lives.
"""
import os
import re
import subprocess

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Generated/derived trees that are not part of the source of truth.
_SKIP_DIRS = ("build/", "log/")

_LINK = re.compile(r"\]\(([^)\s]+)\)")
# A repo-root reference to a DOC: `docs/internal/wishlist/wishlist-topo.md`
# written in prose or a code comment.  Scoped to `docs/` on purpose — a
# `test/…md` string is almost always a markdown link's DISPLAY text, which the
# link walk above already resolves properly (from the file's own directory),
# and matching it here would flag a correct relative link as a bad root path.
_ROOTPATH = re.compile(r"(?<![\w./-])(docs/[\w./*-]+\.(?:md|feature))")


def _tracked(suffixes=None):
    out = subprocess.run(["git", "ls-files"], cwd=_ROOT,
                         capture_output=True, text=True, check=True).stdout
    for f in out.split("\n"):
        if not f or f.startswith(_SKIP_DIRS):
            continue
        if suffixes and not f.endswith(suffixes):
            continue
        yield f


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_every_markdown_link_resolves():
    """`](path)` in any tracked `.md`, resolved from its own directory."""
    bad, checked = [], 0
    for rel in _tracked((".md",)):
        base = os.path.dirname(os.path.join(_ROOT, rel))
        for m in _LINK.finditer(_read(rel)):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#")[0]
            if not path:                      # a pure `#anchor`
                continue
            checked += 1
            if not os.path.exists(os.path.normpath(os.path.join(base, path))):
                bad.append(f"{rel} -> {target}")
    assert checked > 300, f"only {checked} links seen — the walk found nothing"
    assert not bad, "broken markdown links:\n  " + "\n  ".join(sorted(bad))


def test_every_repo_root_doc_path_resolves():
    """A `docs/…md` path written in prose or a code comment names a real file.

    This is the half a markdown-link check cannot see: a C++ source pointing
    at the doc that explains it writes the path, not a link.
    """
    import glob
    bad, checked = [], 0
    for rel in _tracked():
        try:
            text = _read(rel)
        except (IsADirectoryError, FileNotFoundError):
            continue
        for m in _ROOTPATH.finditer(text):
            path = m.group(1)
            checked += 1
            full = os.path.join(_ROOT, path)
            ok = bool(glob.glob(full)) if "*" in path else os.path.exists(full)
            if not ok:
                bad.append(f"{rel} -> {path}")
    assert checked > 200, f"only {checked} paths seen — the walk found nothing"
    assert not bad, "repo-root doc paths naming no file:\n  " + "\n  ".join(
        sorted(set(bad)))


@pytest.mark.parametrize("name", [
    "wishlist.md", "wishlist-bdb.md", "wishlist-bundler.md", "wishlist-healer.md",
    "wishlist-nuts.md", "wishlist-planner.md", "wishlist-topo.md",
    "wishlist-topoedit.md", "wishlist-ux.md",
])
def test_the_wishlist_set_lives_in_one_folder(name):
    """The wishlist docs are a SET, and a stray one outside the folder is how
    a set stops being one — the reader who finds it never learns the rest
    exist.  Pins both halves: the file is in `wishlist/`, and nothing is left
    at the old flat location."""
    assert os.path.isfile(os.path.join(_ROOT, "docs/internal/wishlist", name))
    assert not os.path.exists(os.path.join(_ROOT, "docs/internal", name))


def test_the_index_lists_every_wishlist_file():
    """A wishlist doc reachable from nowhere is a doc nobody reads."""
    folder = os.path.join(_ROOT, "docs/internal/wishlist")
    index = _read("docs/internal/wishlist/wishlist.md")
    missing = [f for f in sorted(os.listdir(folder))
               if f.endswith(".md") and f != "wishlist.md" and f not in index]
    assert not missing, f"not linked from the wishlist index: {missing}"
