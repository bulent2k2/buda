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

"""Documentation freshness guards (risk_reduction_plan.md R5).

Doc staleness used to be found by readers, not tooling.  These guards
fail CI on the two mechanically checkable classes:

- a repo-relative markdown link whose target no longer exists (docs
  renamed/moved without updating referrers), and
- a CLI command that exists in the registry but appears in none of the
  command-documentation surfaces (a new command landed undocumented).
"""

import pathlib
import re

from buda_cmds import COMMANDS

_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Markdown files whose links must resolve.  debug/history/log scratch
# areas are deliberately out of scope.
_LINK_SCOPE = ["README.md", "CLAUDE.md", "GEMINI.md"]

# Where a command counts as documented: the authoritative CLAUDE.md
# tables, the script-reference index, and the per-stage reference pages.
_CMD_DOC_SOURCES = ["CLAUDE.md", "docs/BUDA_SCRIPT_REFERENCE.md"]

_LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)\s]*)?\)")


def _md_files():
    files = [_ROOT / f for f in _LINK_SCOPE if (_ROOT / f).exists()]
    files += sorted((_ROOT / "docs").rglob("*.md"))
    return files


def test_repo_relative_markdown_links_resolve():
    broken = []
    for f in _md_files():
        text = f.read_text(encoding="utf-8")
        for m in _LINK_RE.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            p = (f.parent / target).resolve()
            if not p.exists():
                broken.append(f"{f.relative_to(_ROOT)} -> {target}")
    assert not broken, (
        "Broken repo-relative markdown link(s) — the target moved or was "
        "renamed without updating the referrer:\n  " + "\n  ".join(broken))


def test_every_cli_command_is_documented():
    doc_text = ""
    for f in _CMD_DOC_SOURCES:
        doc_text += (_ROOT / f).read_text(encoding="utf-8")
    for f in sorted((_ROOT / "docs" / "script_reference").glob("*.md")):
        doc_text += f.read_text(encoding="utf-8")
    missing = [c for c in sorted(COMMANDS)
               if not re.search(r"(?<![a-z_])" + re.escape(c) + r"(?![a-z_])",
                                doc_text)]
    assert not missing, (
        "CLI command(s) present in the registry but absent from every "
        "command-doc surface (CLAUDE.md, BUDA_SCRIPT_REFERENCE.md, "
        "docs/script_reference/): " + ", ".join(missing) +
        " — document them (a one-line table row is enough).")
