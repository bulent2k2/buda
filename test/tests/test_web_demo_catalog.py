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

"""The web demo catalog is DERIVED from real flows — these pin that.

`demo/web/demos.json` names a `.buda` flow per demo and `src/web/demos.py`
reads it: setup above the first pipeline command, the flow's own spelling of
each stage, and the pipeline tail for the "Run flow" button.  Nothing about a
demo is written twice, so what these tests guard is the DERIVATION: a manifest
naming a flow that moved, a setup that leaked a pipeline command, a tail that
would open a viewer or write into the checkout, and a relative path that would
resolve against the wrong root when replayed command-by-command.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
from web import demos as demos_mod            # noqa: E402


_ROOT = Path(__file__).parents[2]
_MANIFEST = _ROOT / "demo" / "web" / "demos.json"


def _catalog():
    return demos_mod.catalog()


def test_every_manifest_entry_names_an_existing_flow():
    # The one way this catalog can rot: a flow is renamed or moved and the
    # manifest still points at the old path.  The demo would then be listed
    # (with an `unavailable` note) but never runnable.
    entries = json.loads(_MANIFEST.read_text())["demos"]
    assert entries, "manifest is empty"
    for e in entries:
        assert (_ROOT / e["flow"]).is_file(), f"{e['key']}: {e['flow']} missing"
        assert e["label"] and e["key"]
    keys = [e["key"] for e in entries]
    assert len(set(keys)) == len(keys), f"duplicate demo keys: {keys}"


def test_every_demo_is_available_with_checked_in_inputs():
    # A curated demo must run from a fresh clone.  A flow whose `require_file`
    # inputs are fetched/generated is legal in the repo but not a demo — the
    # catalog marks it unavailable, and this says none of ours is.
    bad = {d["key"]: d["unavailable"] for d in _catalog() if d.get("unavailable")}
    assert not bad, bad


def test_setup_stops_at_the_pipeline_and_the_tail_starts_there():
    for d in _catalog():
        setup = [ln for ln in d["setup"].splitlines() if ln.strip()]
        assert setup, f"{d['key']}: empty setup"
        for ln in setup:
            verb = ln.split()[0].lower()
            assert verb not in demos_mod._PIPELINE_CMDS, \
                f"{d['key']}: pipeline command {verb!r} leaked into the setup"
        assert d["flow"], f"{d['key']}: empty flow tail"
        assert d["flow"][0].split()[0].lower() in demos_mod._PIPELINE_CMDS


def test_the_flow_tail_opens_no_viewer_and_writes_no_artifact():
    # What "skip inapplicable commands" means concretely: a viewer would block
    # the server on a window nobody can see, `exit` would end the session, and
    # an artifact writer would scribble into the user's checkout.
    for d in _catalog():
        for ln in d["flow"]:
            assert ln.split()[0].lower() not in demos_mod._SKIP_IN_BROWSER, \
                f"{d['key']}: {ln!r} does not belong in a browser run"


def test_stages_use_the_flows_own_spelling():
    demos = {d["key"]: d for d in _catalog()}
    for d in demos.values():
        assert set(d["stages"]) == {"bundler", "topologies", "planner",
                                    "nuts", "dnuts"}
    # A hier flow drives the hier commands WITHOUT the manifest saying so —
    # the whole point of deriving the stage map from the flow.
    assert demos["hier"]["stages"]["bundler"].startswith("run_hier_bundler")
    assert demos["hier"]["stages"]["topologies"] == "generate_hier_topologies"
    assert demos["rv"]["stages"]["planner"].startswith("run_planner hier")
    # ...and a flat one stays flat, with the flow's own arguments carried.
    assert demos["flat"]["stages"]["bundler"] == "run_bundler"
    assert demos["comprehensive"]["stages"]["bundler"] == "run_bundler strict"


def test_relative_paths_are_rerooted_for_command_by_command_replay():
    # A flow resolves `source ../../flow/tracks/x.buda` against its OWN
    # directory; replayed one line at a time through /api/command there is no
    # enclosing script, so the engine falls back to the CWD.  Every path the
    # setup names must therefore resolve from the repo root.
    # Keyed on a FILE EXTENSION, not on "contains a slash": a hierarchical pin
    # is `u1/s1/r.out`, which is a NAME, not a path — the loose test read those
    # as broken paths.
    exts = (".buda", ".def", ".lef", ".v", ".sql", ".bdb", ".json", ".gds",
            ".tcl", ".csv")
    checked = 0
    for d in _catalog():
        for ln in d["setup"].splitlines():
            for tok in ln.split()[1:]:
                if tok.lower().endswith(exts):
                    assert (_ROOT / tok).exists(), \
                        f"{d['key']}: {tok!r} does not resolve from the repo root"
                    checked += 1
    assert checked, "no path arguments were checked — the test proved nothing"
    flat = {d["key"]: d for d in _catalog()}["flat"]
    assert "source flow/tracks/tracks4top.buda" in flat["setup"]


@pytest.mark.mid
@pytest.mark.parametrize("key", [d["key"] for d in demos_mod.catalog()])
def test_each_demo_setup_runs_clean(key):
    """Every catalog setup executes — the check a listing cannot make.  Driven
    through the SERVER, because that is the door the client uses: the lines are
    replayed one at a time with no enclosing script, which is exactly the case
    the path rerooting exists for (running them as a .buda file would resolve
    against the temp file's directory instead and prove nothing about it).

    Setup only: the pipeline tail is the stage buttons' job, and the slow part.
    """
    from fastapi.testclient import TestClient
    from web.server import app

    d = {x["key"]: x for x in demos_mod.catalog()}[key]
    with TestClient(app) as client:
        client.post("/api/reset", json={})
        cmds = [ln for ln in d["setup"].splitlines() if ln.strip()]
        res = client.post("/api/command", json={"cmds": cmds}).json()["results"]
    bad = [r for r in res if not r["ok"]]
    assert not bad, (key, bad[:3])


# ---------------------------------------------------------------- quoted paths
# Codex #863 P2.  This module is a `.buda` READER, and reparsing the syntax
# here got all three of its rules wrong at once on a path the engine reads
# fine.  The fix is to go through `buda_script`; these pin the behaviour that
# proves it, on a flow written the way the engine documents.

def _quoted_flow(tmp_path):
    """A flow whose input directory carries a `#` and a space in its name —
    legal on every filesystem, and exactly the spelling `require_file`'s
    quoting exists for."""
    d = tmp_path / "rev #2 inputs"
    d.mkdir()
    (d / "top.v").write_text("module top(); endmodule\n")
    (d / "inc.buda").write_text("# included\n")
    flow = tmp_path / "f.buda"
    flow.write_text('require_file "rev #2 inputs/top.v" hint run fetch.py\n'
                    'source "rev #2 inputs/inc.buda"\n'
                    'run_bundler STRICT\n')
    return flow


def test_a_quoted_path_is_not_cut_at_its_hash(tmp_path):
    """The `#` inside a quoted run is part of the filename, not a comment."""
    setup, _stages, _tail, _missing, _hint = demos_mod._parse_flow(
        str(_quoted_flow(tmp_path)))
    assert "rev #2 inputs/top.v" in setup
    assert "rev #2 inputs/inc.buda" in setup


def test_a_quoted_path_that_exists_is_not_reported_missing(tmp_path):
    """The availability check reads the same tokens the engine does, so a
    flow whose inputs are all present is not marked unavailable."""
    *_, missing, _hint = demos_mod._parse_flow(str(_quoted_flow(tmp_path)))
    assert missing == []


def test_a_rerooted_spaced_path_is_requoted(tmp_path):
    """Reassembly is the tokenizer's inverse: a rerooted path carrying a
    space (or a `#`) comes back QUOTED, so replaying the line through
    /api/command reads it as ONE argument again."""
    from buda_script import split_quoted_args
    setup, *_ = demos_mod._parse_flow(str(_quoted_flow(tmp_path)))
    for line in setup.splitlines():
        toks = split_quoted_args(line, skip=0)
        # `source <path>` and `require_file <path> hint …`: the path is one
        # token whichever rule reads it, and it still names a real file.
        assert any("rev #2 inputs/" in t for t in toks), line
        assert not any(t.startswith('"') for t in toks), line


def test_the_checked_in_catalog_is_unchanged_by_the_quote_rule():
    """No checked-in flow quotes anything, so the quote-aware read is the
    identity on all of them — the property that makes the fix safe."""
    for d in _catalog():
        for line in [l for l in d["setup"].splitlines()] + d["flow"]:
            assert '"' not in line and "'" not in line, (d["key"], line)
