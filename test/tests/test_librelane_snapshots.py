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

"""`flow/librelane/snapshots.py` -- renders and BDBs from what a finished
LibreLane run already wrote (#906, follow-ups in #908).

Everything pinned here is a PURE FUNCTION of a `resolved.json` and a
directory layout, so none of it needs Docker, a PDK or a run: `by_corner`
(a LEF view is either a list or a dict keyed by corner PATTERN, and reading
the wrong shape hands the renderer `nom_*` as a filename), `lefs` (the key
is `TECH_LEFS`, PLURAL -- the first cut read a singular `TECH_LEF`, found
nothing and passed NO technology LEF at all), `tech_layers` (the stage BDB's
layer table, from the run's own stack rather than a hard-coded sky130 one),
`stages` (the ordinal/name parse and the flow order) and `mounts` (what the
container can SEE -- a run tree outside $HOME used to fail as a truncated
path error from inside KLayout).  `render` and `write_bdb` are the only two
that need the image, and they are stubbed for a pass over the index writer.
"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "flow" / "librelane"))
import snapshots as sn   # noqa: E402

pytestmark = pytest.mark.mid

_CORNER = "nom_tt_025C_1v80"


def _resolved(run_dir, **over):
    """A minimal `resolved.json` of the shape LibreLane writes."""
    cfg = {"DESIGN_NAME": "tpu_top", "PDK": "sky130A", "DEFAULT_CORNER": _CORNER,
           "TECH_LEFS": {"nom_*": str(run_dir / "nom.tlef"),
                         "min_*": str(run_dir / "min.tlef"),
                         "max_*": str(run_dir / "max.tlef")},
           "CELL_LEFS": [str(run_dir / "cells.lef")],
           "EXTRA_LEFS": None}
    cfg.update(over)
    (run_dir / "resolved.json").write_text(json.dumps(cfg))
    return cfg


def test_by_corner_resolves_both_shapes_a_lef_view_comes_in(tmp_path):
    """`CELL_LEFS` is a list and `TECH_LEFS` a dict keyed by corner PATTERN,
    and confusing them is not a near miss: `list(dict)` yields the KEYS, so
    the renderer is handed the string `nom_*` as a filename."""
    assert sn.by_corner(["a.lef", "b.lef"], _CORNER) == ["a.lef", "b.lef"]
    assert sn.by_corner("a.lef", _CORNER) == ["a.lef"]
    assert sn.by_corner(None, _CORNER) == []
    assert sn.by_corner({}, _CORNER) == []
    # the pattern is matched with fnmatch...
    assert sn.by_corner({"min_*": "min.lef", "nom_*": "nom.lef"}, _CORNER) == ["nom.lef"]
    # ...and an EXACT key wins over a pattern that would also match
    assert sn.by_corner({"nom_*": "glob.lef", _CORNER: "exact.lef"}, _CORNER) == ["exact.lef"]
    # a corner nothing matches still yields a LEF rather than nothing at all
    assert sn.by_corner({"min_*": "min.lef"}, _CORNER) == ["min.lef"]


def test_lefs_reads_the_plural_tech_key_and_reports_a_macro_lef_that_is_not_there(tmp_path):
    """The technology view's key is `TECH_LEFS`, plural.  Reading a singular
    `TECH_LEF` found nothing and passed the renderer NO technology LEF at
    all (Codex #906) -- the renders still came out, because `-T`/`-M` carry
    the layer map, so the loss was invisible.

    A macro LEF that is not on disk is REPORTED, not silently dropped: a
    macro rendered without its LEF is an empty box, which looks like a
    placement bug."""
    run = tmp_path / "runs" / "h"
    run.mkdir(parents=True)
    for n in ("nom.tlef", "cells.lef"):
        (run / n).write_text("")
    (tmp_path / "pe_cell" / "runs" / "h" / "final" / "lef").mkdir(parents=True)
    (tmp_path / "pe_cell" / "runs" / "h" / "final" / "lef" / "pe_cell.notch.lef").write_text("")
    cfg = _resolved(run, MACROS={
        "pe_cell": {"lef": ["dir::../pe_cell/runs/h/final/lef/pe_cell.notch.lef"]},
        "acc_cell": {"lef": ["dir::../acc_cell/runs/h/final/lef/acc_cell.notch.lef"]}})
    # `dir::` is LibreLane's own prefix and reaches this file resolved; the
    # fixture uses the resolved spelling the run's resolved.json carries
    cfg["MACROS"] = {k: {"lef": [p.replace("dir::", "")] } for k, v in cfg["MACROS"].items()
                     for p in v["lef"]}
    out, missing = sn.lefs(cfg, str(run))
    assert out[0] == str(run / "nom.tlef"), "the technology LEF must come FIRST"
    assert str(run / "cells.lef") in out
    assert any(p.endswith("pe_cell.notch.lef") for p in out)
    assert len(missing) == 1 and missing[0].endswith("acc_cell.notch.lef")
    assert not any(p.endswith("acc_cell.notch.lef") for p in out)
    assert sn.tech_lefs(cfg) == [str(run / "nom.tlef")]


def test_tech_layers_reads_the_runs_own_routing_stack(tmp_path):
    """The stage BDB's layer table comes from the run's technology LEF, not
    from a hard-coded sky130 one (#908).

    Two shapes have to survive.  A CUT or MASTERSLICE layer has no
    `DIRECTION` and is not a `def_layer`.  And sky130's tech LEF declares a
    PROPERTY named `LAYER` (`LAYER LEF58_TYPE STRING ;`, inside
    PROPERTYDEFINITIONS) -- taking that as an open layer block left every
    real layer inside a block that never ends, and the whole file read as
    ZERO layers."""
    p = tmp_path / "t.tlef"
    p.write_text("""VERSION 5.7 ;
PROPERTYDEFINITIONS
  LAYER LEF58_TYPE STRING ;
END PROPERTYDEFINITIONS
LAYER nwell
  TYPE MASTERSLICE ;
END nwell
LAYER li1
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.46 ;
END li1
LAYER mcon
  TYPE CUT ;
END mcon
LAYER met1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
END met1
END LIBRARY
""")
    assert sn.tech_layers(str(p)) == [("li1", "V"), ("met1", "H")]
    # a file that is not there is empty, not an exception: main falls back and says so
    assert sn.tech_layers(str(tmp_path / "nope.tlef")) == []


def test_stages_parses_the_ordinal_and_returns_them_in_flow_order(tmp_path):
    """A stage is a numbered step directory holding a DEF.  The ordinal
    orders them (string order puts step 10 before step 2), and a directory
    that is not a numbered step -- `final/`, `tmp/` -- is not a stage."""
    for step, has_def in (("03-openroad-floorplan", True),
                          ("17-odb-manualmacroplacement", True),
                          ("21-openroad-generatepdn", True),
                          ("35-openroad-cts", False),
                          ("final", True), ("tmp", True)):
        d = tmp_path / step
        d.mkdir()
        if has_def:
            (d / "tpu_top.def").write_text("")
    got = sn.stages(str(tmp_path))
    assert [(n, name) for n, _step, name, _d in got] == [
        (3, "floorplan"), (17, "manualmacroplacement"), (21, "generatepdn")]
    assert all(os.path.isfile(d) for *_r, d in got)


def test_mounts_makes_a_run_tree_outside_home_visible(tmp_path):
    """The container sees only what is bind-mounted.  `render` mounted $HOME
    and nothing else, so a run directory, LEF or tech file anywhere else was
    invisible and the failure was `render.py`'s own one-line path error
    (#908).  Each such path now gets its own mount, deduplicated against a
    mount that already contains it -- and a path at the filesystem root is
    REPORTED rather than answered by bind-mounting `/`."""
    home = os.path.realpath(os.path.expanduser("~"))
    args, bad = sn.mounts([os.path.join(home, "src", "buda", "x.def")])
    assert args == ["-v", f"{home}:{home}"] and bad == []
    outside = os.path.realpath(str(tmp_path))          # macOS tmp is not under $HOME
    if not outside.startswith(home + os.sep):
        args, bad = sn.mounts([os.path.join(outside, "runs", "h", "x.def")])
        assert bad == []
        assert f"{outside}/runs/h:{outside}/runs/h" in args, args
        # a second path under the first adds no second mount
        args2, _ = sn.mounts([os.path.join(outside, "runs", "h", "x.def"),
                              os.path.join(outside, "runs", "h", "y.def")])
        assert args2 == args
    args, bad = sn.mounts(["/x.def"])
    assert bad == ["/x.def"] and args == ["-v", f"{home}:{home}"]


def test_every_path_render_passes_is_inside_a_mount_even_through_a_symlink(tmp_path, monkeypatch):
    """The mount set and the arguments must be ONE spelling of each path.

    `mounts` resolves, and `render` used to pass the caller's path
    unresolved, so an input reached through a symlink was mounted at its
    TARGET and named by its LINK: mount `/mnt/pdk`, argument
    `/scratch/pdk-link/cells.lef`, whose parent is mounted nowhere, and
    KLayout fails on a file that is plainly there on the host (Codex #909).
    Not exotic -- on macOS `/var` is a link to `/private/var`, so every path
    under a system temp directory has two spellings.

    So the property is checked over the WHOLE argv rather than on the one
    call that was reported: every absolute path `render` hands the container
    must lie inside one of the `-v` roots it asks for."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "cells.lef").write_text("")
    (real_dir / "tpu_top.def").write_text("")
    (tmp_path / "link").symlink_to(real_dir)

    seen = {}
    monkeypatch.setattr(sn.subprocess, "run",
                        lambda argv, **k: seen.setdefault("argv", argv) and None
                        or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    cfg = {"KLAYOUT_TECH": str(tmp_path / "link" / "sky130A.lyt")}
    sn.render(str(tmp_path / "link" / "tpu_top.def"), str(tmp_path / "link" / "out.png"),
              cfg, str(real_dir), [str(tmp_path / "link" / "cells.lef")])
    argv = seen["argv"]
    roots = [argv[i + 1].split(":")[0] for i, a in enumerate(argv) if a == "-v"]
    paths = [a for a in argv if a.startswith("/") and (
        a.endswith((".lef", ".def", ".png", ".lyt")) or a in roots)]
    assert paths, argv
    for a in paths:
        assert any(a == r or a.startswith(r.rstrip("/") + os.sep) for r in roots), \
            f"{a} is passed to the container but is inside none of {roots}"
    # ...and the working directory is resolved the same way
    w = argv[argv.index("-w") + 1]
    assert any(w == r or w.startswith(r.rstrip("/") + os.sep) for r in roots), (w, roots)


def test_render_keeps_each_klayout_file_on_its_own_flag(tmp_path, monkeypatch):
    """`-T`, `-P` and `-M` are three different files, and a run need not
    declare all three.  Pairing a FILTERED list of the present ones with a
    positional `zip` over the flags hands the layer map to `-P` the moment
    `KLAYOUT_PROPERTIES` is absent, which is every tier-1a run."""
    (tmp_path / "t.lyt").write_text("")
    (tmp_path / "m.map").write_text("")
    (tmp_path / "x.def").write_text("")
    seen = {}
    monkeypatch.setattr(sn.subprocess, "run",
                        lambda argv, **k: seen.setdefault("argv", argv) and None
                        or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    sn.render(str(tmp_path / "x.def"), str(tmp_path / "x.png"),
              {"KLAYOUT_TECH": str(tmp_path / "t.lyt"),
               "KLAYOUT_DEF_LAYER_MAP": str(tmp_path / "m.map")}, str(tmp_path), [])
    argv = seen["argv"]
    assert argv[argv.index("-T") + 1].endswith("t.lyt")
    assert argv[argv.index("-M") + 1].endswith("m.map")
    assert "-P" not in argv


def test_main_renders_the_curated_stages_and_writes_the_index(tmp_path, monkeypatch, capsys):
    """The end-to-end pass over `main`'s index writer, with the only two
    functions that need the image stubbed.

    What it pins beyond the plumbing: a BDB is written for a PLACEMENT stage
    and refused for a routed one, because `import_def_lef` does not read
    routed geometry -- a BDB of a routed stage would look like an unrouted
    design and quietly mislead."""
    run = tmp_path / "runs" / "h"
    run.mkdir(parents=True)
    (run / "nom.tlef").write_text("LAYER met1\n  TYPE ROUTING ;\n  DIRECTION HORIZONTAL ;\nEND met1\n")
    (run / "cells.lef").write_text("")
    _resolved(run)
    for step in ("03-openroad-floorplan", "17-odb-manualmacroplacement",
                 "49-openroad-detailedrouting"):
        (run / step).mkdir()
        (run / step / "tpu_top.def").write_text("")

    seen = {}
    monkeypatch.setattr(sn, "render", lambda dp, png, *a, **k: (
        seen.setdefault("render", []).append(os.path.basename(png)),
        open(png, "w").write("x"), True)[-1])
    monkeypatch.setattr(sn, "write_bdb", lambda dp, bdb, *a, **k: (
        seen.setdefault("bdb", []).append(os.path.basename(bdb)), (True, ""))[-1])
    out = tmp_path / "snap"
    assert sn.main([str(run), "--all", "--bdb", "--out", str(out)]) == 0
    assert seen["render"] == ["03-floorplan.png", "17-manualmacroplacement.png",
                              "49-detailedrouting.png"]
    # only the placement stages get a BDB
    assert seen["bdb"] == ["03-floorplan.bdb", "17-manualmacroplacement.bdb"]
    idx = (out / "index.md").read_text()
    assert "| 3 | `floorplan` |" in idx and "| 49 | `detailedrouting` |" in idx
    assert "a routed DEF's geometry is not read into a BDB" in idx
    assert "layers from nom.tlef: met1(H)" in capsys.readouterr().out
