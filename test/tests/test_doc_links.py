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

Three kinds of reference, because the docs use all three:

  * an **inline markdown link** `](path.md)`, resolved relative to the file
    it is written in — the one that breaks when a doc changes directory;
  * a **reference-style definition** `[label]: path.md`, whose destination
    lives on its own line far from the `[text][label]` that uses it — so it
    is exactly the kind a reader is least likely to re-check by hand
    (Codex #867 P2).  `docs/origin/paper.md` writes its figures this way;
    every one of its ten definitions is a `data:` URI today, so this catches
    nothing yet — which is the point of adding it before it has to;
  * a **reference-style USAGE** `[text][label]` whose definition is missing
    entirely.  That one is not a dead link but a non-link: markdown renders
    it as literal text, brackets and all, so it fails in the direction a
    reader notices least — the sentence still reads, it just stopped being a
    pointer;
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
# A reference-style destination: `[label]: path.md` (optionally `<bracketed>`,
# optionally followed by a title).  Up to three leading spaces, per CommonMark.
_REFDEF = re.compile(r"(?m)^ {0,3}\[[^\]]+\]:[ \t]*<?([^>\s]+)>?")
# The label a definition BINDS, for checking usages against.
_REFLABEL = re.compile(r"(?m)^ {0,3}\[([^\]]+)\]:")
# A reference-style USAGE: the FULL form `[text][label]` and the COLLAPSED
# form `[label][]`.  The SHORTCUT form (a bare `[label]`) is deliberately not
# matched — in this repo a bare bracket pair is almost never a link (`[0]`,
# `[TOP]`, `[SIGNAL 1 0.5]`, a checkbox), so matching it would bury a real
# finding under noise it can never distinguish.
_REFUSE = re.compile(r"\[([^\]\n]*)\]\[([^\]\n]*)\]")
# Fenced blocks and inline code spans, removed before any of the above is
# applied to a USAGE.  This is not tidiness: the three `\w[1][0]` mentions in
# CLAUDE.md, BDB_REFERENCE.md and opens_interchange.md are a 2-D Verilog
# array element written in backticks, and one of them is a doc EXPLAINING
# that string.  Scanning raw text reports all three as broken links.
_FENCE = re.compile(r"(?ms)^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$")
_CODESPAN = re.compile(r"(`+)(?:.|\n)*?\1")
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


def _strip_code(text):
    """`text` with fenced blocks and inline code spans blanked out.

    Blanked rather than deleted so nothing downstream needs to remap offsets;
    newlines are kept so any line-based reading still lines up.
    """
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return _CODESPAN.sub(blank, _FENCE.sub(blank, text))


def _local_targets(text):
    """Every link destination in `text` that names a file rather than a URL —
    inline `](…)` and reference-style `[label]: …` alike."""
    for m in _LINK.finditer(text):
        yield m.group(1)
    for m in _REFDEF.finditer(text):
        yield m.group(1)


def test_every_markdown_link_resolves():
    """Every link destination in any tracked `.md`, resolved from its own
    directory — inline and reference-style both."""
    bad, checked = [], 0
    for rel in _tracked((".md",)):
        base = os.path.dirname(os.path.join(_ROOT, rel))
        for target in _local_targets(_read(rel)):
            if target.startswith(("http://", "https://", "mailto:",
                                  "data:", "#")):
                continue
            path = target.split("#")[0]
            if not path:                      # a pure `#anchor`
                continue
            checked += 1
            if not os.path.exists(os.path.normpath(os.path.join(base, path))):
                bad.append(f"{rel} -> {target}")
    assert checked > 300, f"only {checked} links seen — the walk found nothing"
    assert not bad, "broken markdown links:\n  " + "\n  ".join(sorted(bad))


def test_every_reference_style_usage_has_a_definition():
    """`[text][label]` with no `[label]:` anywhere in the file.

    Unlike a dead link this does not 404 — markdown renders it as literal
    text, brackets and all — so it degrades in the direction a reader is
    least likely to report.  Labels are matched case-insensitively, per
    CommonMark.
    """
    bad, checked = [], 0
    for rel in _tracked((".md",)):
        text = _read(rel)
        defined = {m.group(1).strip().lower()
                   for m in _REFLABEL.finditer(text)}
        for m in _REFUSE.finditer(_strip_code(text)):
            first, second = m.group(1), m.group(2)
            # `[label][]` is the collapsed form: the FIRST group is the label.
            label = (second or first).strip()
            if not label:
                continue
            checked += 1
            if label.lower() not in defined:
                bad.append(f"{rel}: [{first}][{second}] -> no [{label}]: "
                           f"definition")
    assert checked, "the usage walk found nothing at all"
    assert not bad, ("reference-style links with no definition:\n  "
                     + "\n  ".join(sorted(bad)))


def test_a_bracket_pair_in_CODE_is_not_read_as_a_link():
    """The regression this guard would otherwise BE.

    `\\w[1][0]` — a 2-D Verilog array element — appears in three checked-in
    docs, in backticks, and one of them is a doc explaining that exact
    string.  A usage scan over raw text reports all three as broken links,
    which is worse than not scanning: the finding is always wrong and always
    there.  So code is stripped first, and this pins that it stays stripped.
    """
    raw_hits = [rel for rel in _tracked((".md",))
                if _REFUSE.search(_read(rel))]
    assert raw_hits, "expected the repo to contain reference-shaped text"
    stripped = [rel for rel in raw_hits
                if _REFUSE.search(_strip_code(_read(rel)))]
    # The `\w[1][0]` docs must drop out; paper.md's real image refs stay.
    for rel in ("CLAUDE.md", "docs/BDB_REFERENCE.md",
                "docs/internal/opens_interchange.md"):
        assert rel in raw_hits, f"{rel} no longer carries the code mention"
        assert rel not in stripped, f"{rel} leaked a code span into the scan"
    assert "docs/origin/paper.md" in stripped, "real refs were stripped too"


def test_reference_style_definitions_are_walked(tmp_path):
    """The reference-style half, pinned on a fixture — because the repo's own
    reference links all point at `data:` URIs, so the real corpus cannot yet
    tell a guard that reads them from one that does not."""
    good = tmp_path / "good.md"
    good.write_text("see [the guide][g]\n\n[g]: good.md\n")
    bad = tmp_path / "bad.md"
    bad.write_text("see [the guide][g]\n\n[g]: moved.md\n")
    assert [t for t in _local_targets(good.read_text())] == ["good.md"]
    assert [t for t in _local_targets(bad.read_text())] == ["moved.md"]
    assert (tmp_path / "good.md").exists()
    assert not (tmp_path / "moved.md").exists()


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
