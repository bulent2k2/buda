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

"""The tier-1a/1b tooling of the LibreLane study (flow/librelane/tier1*).

No LibreLane here, so this pins what the recipes hand to it: `gen.sh N`
emits a complete design directory with a config that names the files it
emitted, and `runtimes.py` reads a run directory the way LibreLane writes
one -- per-step `runtime.txt` in its `h:m:s:ms` format, `final/metrics.json`
-- and reports the stage totals and PPA metrics the benchmark tabulates.  A
wrong time parser would have made every runtime number in the write-up
wrong by a silent factor.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_T1A = _ROOT / "flow" / "librelane" / "tier1a"

pytestmark = pytest.mark.mid


def test_gen_sh_emits_a_complete_flat_design_at_n(tmp_path):
    r = subprocess.run(["bash", str(_T1A / "gen.sh"), "2"], env={**__import__("os").environ,
                       "T1A_DIR": str(tmp_path)}, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    d = tmp_path / "n2"
    for f in ("tpu_rtl.v", "tpu.v", "tpu.def", "tpu.lef", "config.json"):
        assert (d / f).exists(), f
    cfg = json.loads((d / "config.json").read_text())
    assert cfg["DESIGN_NAME"] == "tpu_top"
    assert cfg["VERILOG_FILES"] == ["dir::tpu_rtl.v"]
    assert cfg["CLOCK_PORT"] == "clk" and cfg["FP_SIZING"] == "relative"
    # N=2: two rows of two PEs.
    assert (d / "tpu_rtl.v").read_text().count("pe_cell pe_") == 2
    assert (d / "tpu_rtl.v").read_text().count("row_cell row_") == 2


def _fake_run(root):
    # What LibreLane 3.0.11 WRITES is HH:MM:SS.mmm (its formatter's docstring
    # says h:m:s:ms, and a parser written to the docstring refused every real
    # run -- measured 2026-09-05); one step keeps the documented form, which
    # is accepted too.
    steps = {"01-verilator-lint": "00:00:01.500", "02-yosys-synthesis": "0:1:2:250",
             "03-openroad-floorplan": "00:00:10.000", "04-openroad-globalplacement": "00:00:30.000",
             "05-openroad-cts": "00:00:20.000", "06-openroad-globalrouting": "00:00:40.000",
             "07-openroad-detailedrouting": "01:00:00.000", "08-magic-streamout": "00:00:05.000",
             "09-checker-lvs": "00:00:01.000"}
    for name, t in steps.items():
        (root / name).mkdir(parents=True)
        (root / name / "runtime.txt").write_text(t)
    (root / "final").mkdir()
    (root / "final" / "metrics.json").write_text(json.dumps({
        "design__instance__area": 273115.69, "design__die__area": 700000.0,
        "timing__setup__ws": 1.23, "power__total": 0.0042, "power__internal__total": 0.0030,
        "power__switching__total": 0.0011, "power__leakage__total": 0.0001,
        "route__wirelength": 1234567, "route__drc_errors": 0}))


def test_runtimes_accounts_a_hierarchical_arms_blocks(tmp_path):
    """An H arm's row carries its blocks: wire per PLACED instance (a cell
    hardened once and placed twice is twice the wire), block time as the
    longest one (parallel) and as the sum, and the top-plus-blocks totals.
    The block-internal wire stays its own column -- it is where the pin
    template's cost shows (+60 % on the phase-0 block)."""
    top, b1, b2 = tmp_path / "top", tmp_path / "b1", tmp_path / "b2"
    for d in (top, b1, b2):
        _fake_run(d)
    (b1 / "final" / "metrics.json").write_text(json.dumps({"route__wirelength": 1000, "route__drc_errors": 0}))
    (b2 / "final" / "metrics.json").write_text(json.dumps({"route__wirelength": 50, "route__drc_errors": 2}))
    (b2 / "07-openroad-detailedrouting" / "runtime.txt").write_text("00:30:00.000")   # b2: 1969.8 s, b1: 3769.8 s
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top),
                        "--block", f"{b1}:3", "--block", str(b2), "--json"],
                       check=True, capture_output=True, text=True)
    row = json.loads(r.stdout)
    assert row["total_s"] == 3769.8                                  # the top alone, unchanged
    assert [b["instances"] for b in row["blocks"]] == [3, 1]
    assert row["route__wirelength__blocks"] == 3 * 1000 + 50
    assert row["route__wirelength__arm"] == 1234567 + 3050
    assert row["route__drc_errors__blocks"] == 2
    assert row["blocks_wall_s"] == 3769.8 and row["blocks_cpu_s"] == 3769.8 + 1969.8
    assert row["arm_wall_s"] == 2 * 3769.8 and row["arm_cpu_s"] == round(3769.8 * 2 + 1969.8, 1)
    # A block that never finished routing has no wire to account, one that
    # never reached the DRC step has no violation count, and a top without
    # wire would make an arm total out of the blocks alone: all refused --
    # "not measured" must never read as zero (Codex #878).
    (b2 / "final" / "metrics.json").write_text(json.dumps({"route__drc_errors": 2}))
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top), "--block", str(b2)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "no route__wirelength" in r.stderr
    (b2 / "final" / "metrics.json").write_text(json.dumps({"route__wirelength": 50}))
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top), "--block", str(b2)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "no route__drc_errors" in r.stderr
    (top / "final" / "metrics.json").write_text(json.dumps({"route__drc_errors": 0}))
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top), "--block", f"{b1}:3"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "no route__wirelength" in r.stderr and str(top) in r.stderr
    _fake_run(top := tmp_path / "top2")                      # a top without --block still reports
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top), "--block", f"{b1}:two"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "must be an integer" in r.stderr


def test_runtimes_reads_librelane_time_format_and_groups_stages(tmp_path):
    _fake_run(tmp_path)
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path), "--json"],
                       check=True, capture_output=True, text=True)
    row = json.loads(r.stdout)
    assert row["steps"] == 9
    assert "N" not in row and os.path.isabs(row["run"])          # outside the repo: absolute
    # A row says which point it is on its own (Codex #881): `--set` puts the
    # benchmark coordinates in, integers as integers, and a malformed one
    # is refused rather than recorded as a key with no value.
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path), "--json",
                        "--set", "N=8", "--set", "arm=F"], check=True, capture_output=True, text=True)
    row = json.loads(r.stdout)
    assert row["N"] == 8 and row["arm"] == "F" and list(row)[:2] == ["N", "arm"]
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path), "--set", "N"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "expected KEY=VALUE" in r.stderr
    assert row["synth_s"] == 63.8                  # 0:0:1:500 + 0:1:2:250, to 0.1 s
    assert row["floorplan+place_s"] == 40.0
    assert row["cts_s"] == 20.0
    assert row["route_s"] == 3640.0                # 40 s + 1 h
    assert row["signoff_s"] == 6.0
    assert row["other_s"] == 0.0
    assert row["total_s"] == 3769.8
    assert row["design__instance__area"] == 273115.69 and row["route__drc_errors"] == 0
    # The power BREAKDOWN, not just the total: the plan's tables need it.
    assert (row["power__internal__total"], row["power__switching__total"],
            row["power__leakage__total"]) == (0.0030, 0.0011, 0.0001)
    # A runtime.txt in neither shape is an error, not a zero.
    (tmp_path / "03-openroad-floorplan" / "runtime.txt").write_text("10s")
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "not HH:MM:SS.mmm" in r.stderr


# ── arm H: harm.sh, pdn_phase.py, runtimes --blocks-from ─────────────────
import os
import re
import shutil

_HAS_TCLSH = shutil.which("tclsh") is not None


def _emit(tmp_path, n):
    r = subprocess.run(["bash", str(_T1A / "gen.sh"), str(n)], env={**os.environ, "T1A_DIR": str(tmp_path)},
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / f"n{n}"


def _def_components(path):
    txt = path.read_text()
    dbu = int(re.search(r"UNITS DISTANCE MICRONS (\d+)", txt).group(1))
    die = [int(v) / dbu for v in re.search(r"DIEAREA \( (\d+) (\d+) \) \( (\d+) (\d+) \)", txt).groups()]
    comps = re.findall(r"^- (\S+) (\S+) \+ PLACED \( (-?\d+) (-?\d+) \) (\S+) ;", txt, re.M)
    n = int(re.search(r"COMPONENTS (\d+) ;", txt).group(1))
    assert len(comps) == n
    return die, [(name, cell, int(x) / dbu, int(y) / dbu, o) for name, cell, x, y, o in comps]


def _lef_sizes(path):
    return {m: (float(w), float(h)) for m, w, h in
            re.findall(r"MACRO (\S+)\n(?:.*\n)*?\s*SIZE (\S+) BY (\S+) ;", path.read_text())}


@pytest.mark.skipif(not _HAS_TCLSH, reason="gen.sh emits the set through tclsh")
@pytest.mark.parametrize("n", [2, 4])
def test_harm_sh_writes_the_h_arm_from_the_emitted_set(tmp_path, n):
    """harm.sh N: one block directory per leaf cell on a die exactly its LEF
    SIZE, a top whose MACROS map EVERY DEF component -- `row_0/pe_0` as the
    flattened `row_0.pe_0` -- at the DEF location plus one die-fit shift,
    DIE_AREA the DEF's, the leaf bodies gone from tpu_top_h.v but still
    instantiated, a PDN pitch that divides the array pitch with every macro
    of a cell at one phase, and a predicted-pin dry run of pdn_phase.py that
    passes."""
    d = _emit(tmp_path, n)
    r = subprocess.run(["bash", str(_T1A / "harm.sh"), str(n)], env={**os.environ, "T1A_DIR": str(tmp_path)},
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    h = d / "h"
    die, comps = _def_components(d / "tpu.def")
    sizes = _lef_sizes(d / "tpu.lef")
    cells = ["pe_cell", "feed_cell", "wbuf_cell", "acc_cell"]
    assert set(sizes) == set(cells)
    rtl = (d / "tpu_rtl.v").read_text()
    # (a) the blocks
    for cell in cells:
        cfg = json.loads((h / cell / "config.json").read_text())
        assert cfg["DESIGN_NAME"] == cell and cfg["CLOCK_PORT"] == "clk"
        assert cfg["FP_SIZING"] == "absolute" and cfg["DIE_AREA"] == [0, 0, *sizes[cell]]
        assert cfg["VERILOG_FILES"] == [f"dir::src/{cell}.v"]
        for k, v in {"RT_MAX_LAYER": "met4", "PDN_VPITCH": 30, "PDN_VOFFSET": 5, "PDN_VWIDTH": 2,
                     "PDN_SKIPTRIM": True, "PL_TARGET_DENSITY_PCT": 50}.items():   # reg32/config.json's
            assert cfg[k] == v, k
        assert "FP_DEF_TEMPLATE" not in cfg                                         # LibreLane's own placer
        src = (h / cell / "src" / f"{cell}.v").read_text()
        assert re.findall(r"^module (\w+)", src, re.M) == [cell]
        body = re.search(rf"^module {cell} .*?^endmodule\n", rtl, re.M | re.S).group(0)
        assert body in src                                                          # verbatim, not rewritten
    # (b) the top
    cfg = json.loads((h / "top" / "config.json").read_text())
    assert cfg["DESIGN_NAME"] == "tpu_top" and cfg["FP_SIZING"] == "absolute"
    assert cfg["DIE_AREA"] == die                                                   # the DEF's DIEAREA
    assert cfg["VERILOG_FILES"] == ["dir::src/tpu_top_h.v"]
    pl = json.loads((h / "top" / "placement.json").read_text())
    halo = cfg["FP_MACRO_HORIZONTAL_HALO"], cfg["FP_MACRO_VERTICAL_HALO"]
    # the shift rule, recomputed here from the DEF: the emitter puts feed_* at
    # x = -140, outside its own die, so the whole placement moves right by
    # halo - min x and not at all in y
    shift = [max(0.0, die[0] + halo[0] - min(c[2] for c in comps)),
             max(0.0, die[1] + halo[1] - min(c[3] for c in comps))]
    assert pl["shift_um"] == shift and shift[0] > 0 and shift[1] == 0
    placed = {}
    for cell, m in cfg["MACROS"].items():
        for name, inst in m["instances"].items():
            placed[name] = (cell, inst["location"], inst["orientation"])
        assert m["lef"] == [f"dir::../{cell}/runs/h/final/lef/{cell}.lef"]
        assert m["gds"] == [f"dir::../{cell}/runs/h/final/gds/{cell}.gds"]
        assert m["nl"] == [f"dir::../{cell}/runs/h/final/nl/{cell}.nl.v"]
        assert m["spef"] == {f"{c}_*": f"dir::../{cell}/runs/h/final/spef/{c}/{cell}.{c}.spef"
                             for c in ("nom", "min", "max")}
    assert len(placed) == len(comps)
    for name, cell, x, y, o in comps:
        top_name = name.replace("/", ".")                    # the instance-name rule, pinned
        assert top_name in placed, name
        assert placed[top_name] == (cell, [x + shift[0], y + shift[1]], o)
    assert sum(1 for c in comps if "/" in c[0]) == n * n          # every PE is inside a row
    v = (h / "top" / "src" / "tpu_top_h.v").read_text()
    assert not any(re.search(rf"^module {c}\b", v, re.M) for c in cells)
    assert re.search(r"^module row_cell\b", v, re.M) and re.search(r"^module tpu_top\b", v, re.M)
    assert v.count("pe_cell pe_") == n and v.count("feed_cell feed_") == n
    assert v.count("wbuf_cell wbuf_") == n and v.count("acc_cell ") == 3 * n   # acc + 2 pipe stages
    assert "`default_nettype none" in v
    # (c) the PDN phase: the pitch divides the PE column pitch, so every PE
    # sits at one phase; the prediction passes the checker it was written for
    xs = sorted({x for name, cell, x, y, o in comps if cell == "pe_cell"})
    ppx = xs[1] - xs[0]
    assert ppx == 200 and abs(ppx / cfg["PDN_VPITCH"] - round(ppx / cfg["PDN_VPITCH"])) < 1e-9
    assert len({round((x + shift[0]) % cfg["PDN_VPITCH"], 3) for x in xs}) == 1
    ys = sorted({y for name, cell, x, y, o in comps if cell == "pe_cell"})
    assert (ys[1] - ys[0]) % cfg["PDN_HPITCH"] == 0
    lefs = sorted(str(p) for p in (h / "predicted_lef").glob("*.lef"))
    assert len(lefs) == 4
    sys.path.insert(0, str(_T1A))
    import pdn_phase as pp
    for lef in lefs:
        m = list(pp.read_lef(lef).values())[0]
        pin_rects = {r for pin in m["pins"].values() for r in pin["rects"]}
        assert {l for (l, *_r) in pin_rects} == {"met4", "met5"}
        # the predicted OBS on the PDN layers is the block's OWN power metal --
        # the very rectangles its pins are, which is why the phase search clears
        # the obstruction by clearing the pins
        assert m["obs"] and set(m["obs"]) == pin_rects
    plan = json.loads((h / "top" / "pdn_plan.json").read_text())
    # the horizontal offset is ordered by distance from the macro BOXES, so the
    # straps land mid-channel rather than on the first offset that ties at inf
    assert plan["horizontal"]["gap_from_macro_boxes"] > 5.0
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(h / "top" / "config.json"), *lefs],
                       capture_output=True, text=True)
    assert r.returncode == 0 and f"PASS: {len(comps)} instances" in r.stdout, r.stdout + r.stderr
    readme = (h / "README.md").read_text()
    assert f"runtimes.py top/runs/h --set N={n} --set arm=H --blocks-from top/config.json" in readme
    assert f"--block pe_cell/runs/h:{n * n}" in readme
    assert "--block ../pe_cell" not in readme          # ../pe_cell is n<N>/pe_cell, which does not exist
    assert (h / "pe_cell").is_dir() and not (h.parent / "pe_cell").exists()


@pytest.mark.skipif(not _HAS_TCLSH, reason="gen.sh emits the set through tclsh")
def test_harm_sh_fails_loudly_on_the_shape_it_did_not_expect(tmp_path):
    env = {**os.environ, "T1A_DIR": str(tmp_path)}
    r = subprocess.run(["bash", str(_T1A / "harm.sh"), "2"], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "run gen.sh 2 first" in r.stderr
    d = _emit(tmp_path, 2)
    defp = d / "tpu.def"
    keep = defp.read_text()
    defp.write_text(keep.replace("acc_0 acc_cell", "acc_0 sum_cell", 1))          # a cell the LEF lacks
    r = subprocess.run(["bash", str(_T1A / "harm.sh"), "2"], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "tpu.lef does not declare" in r.stderr, r.stderr
    defp.write_text(keep.replace("+ PLACED ( 72000 132000 ) N", "+ PLACED ( 72000 132000 ) FN", 1))
    r = subprocess.run(["bash", str(_T1A / "harm.sh"), "2"], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "orientation FN" in r.stderr, r.stderr
    defp.write_text(keep)
    lef = d / "tpu.lef"
    lef.write_text(lef.read_text().replace("MACRO acc_cell", "MACRO sum_cell").replace("END acc_cell", "END sum_cell"))
    r = subprocess.run(["bash", str(_T1A / "harm.sh"), "2"], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "defines no module sum_cell" in r.stderr, r.stderr


def _toy_lef(path, met5_x_hi=74.06, obs=()):
    """The phase-0 block as its hardened LEF would show it: 80 x 80, met4
    straps at 5.52 + 5 + 30k (VGND 3.7 after, width 2), met5 straps at
    10.88 + 5 + 30k over the row extent -- the toy's measured 10.52 / 14.22
    / 74.06.  `obs` writes the OBS block LibreLane's `-bloat_occupied_layers`
    abstract LEF carries: a whole-block cover rectangle per occupied layer."""
    L = ["VERSION 5.8 ;", "MACRO reg32", "  CLASS BLOCK ;", "  ORIGIN 0 0 ;", "  SIZE 80 BY 80 ;"]
    for net, off, use in (("VPWR", 0.0, "POWER"), ("VGND", 3.7, "GROUND")):
        L += [f"  PIN {net}", "    DIRECTION INOUT ;", f"    USE {use} ;", "    PORT"]
        for k in range(3):
            c = 5.52 + 5 + 30 * k + off
            if c < 80 - 5.52:
                L += ["      LAYER met4 ;", f"      RECT {c - 1:.3f} 10.88 {c + 1:.3f} 69.12 ;"]
        c = 10.88 + 5 + off
        L += ["      LAYER met5 ;", f"      RECT 5.52 {c - 1:.3f} {met5_x_hi} {c + 1:.3f} ;"]
        L += ["    END", f"  END {net}"]
    if obs == "pins":                       # the block's own power metal, as Magic writes it
        L += ["  OBS"]
        for net, off in (("VPWR", 0.0), ("VGND", 3.7)):
            for k in range(3):
                c = 5.52 + 5 + 30 * k + off
                if c < 80 - 5.52:
                    L += ["    LAYER met4 ;", f"      RECT {c - 1:.3f} 10.88 {c + 1:.3f} 69.12 ;"]
            c = 10.88 + 5 + off
            L += ["    LAYER met5 ;", f"      RECT 5.52 {c - 1:.3f} {met5_x_hi} {c + 1:.3f} ;"]
        L += ["  END"]
    elif obs:
        L += ["  OBS"]
        for layer in obs:
            L += [f"    LAYER {layer} ;", "      RECT 0.000 0.000 80.000 80.000 ;"]
        L += ["  END"]
    L += ["END reg32", "END LIBRARY"]
    path.write_text("\n".join(L) + "\n")


def _toy_config(path, x0, x1=160, **extra):
    cfg = {"meta": {"version": 2}, "DESIGN_NAME": "two_reg32", "FP_SIZING": "absolute",
           "DIE_AREA": [0, 0, 250, 120], "FP_PDN_VOFFSET": 0, "FP_PDN_VPITCH": 30,
           "MACROS": {"reg32": {"instances": {"u0": {"location": [x0, 20], "orientation": "N"},
                                              "u1": {"location": [x1, 20], "orientation": "N"}},
                                "lef": ["dir::reg32.lef"]}}}
    cfg.update(extra)
    path.write_text(json.dumps(cfg))


def test_pdn_phase_finds_the_toys_clip_and_the_shift_that_clears_it(tmp_path):
    """The phase-0 toy's failure, recomputed: with u0 at x = 20 its VGND met4
    pin (33.22-35.22) meets the top's VPWR strap (34.72-36.32, offset 0 pitch
    30 from the 5.52 core origin) -- the doc's own numbers -- so pdngen CUTS
    that strap over the macro, and with all three cut u0's VPWR has nothing
    left to feed it.  The smallest clearing shift is 0.8 um west; at x = 10
    (10 = 160 mod 30) the same check passes.  A macro whose pins no strap
    crosses is UNCONNECTED, and a top without a fixed die is refused
    (exit 2)."""
    _toy_lef(tmp_path / "reg32.lef")
    _toy_config(tmp_path / "bad.json", 20)
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "bad.json"),
                        "--json", str(tmp_path / "bad.out")], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "CLIP u0 VGND pin VGND on met4 [33.220,35.220]" in r.stdout
    assert "cuts VPWR strap k=1 [34.720,36.320]" in r.stdout
    assert "FAIL: 2 instances, 3 clips in 1 instances, 1 unconnected" in r.stdout
    assert "UNCONNECTED u0 VPWR" in r.stdout          # the clips are why
    out = json.loads((tmp_path / "bad.out").read_text())
    assert out["global_dx"] == -0.8 and out["voffset_for_dx"] == 0.8
    u0 = next(p for p in out["per_instance"] if p["instance"] == "u0")
    assert u0["dx"] == -0.8 and u0["clips"] == 3
    assert all(p["clips"] == 0 for p in out["per_instance"] if p["instance"] == "u1")
    _toy_config(tmp_path / "good.json", 10)                     # the toy's fix
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "good.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "PASS: 2 instances, 16 power-pin rects, 0 clips" in r.stdout, r.stdout
    assert "ASSUMED" in r.stdout and "PDN_HPITCH=153.18" in r.stdout   # what it took from the defaults
    # the LEF passed explicitly wins over the config's path
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "good.json"),
                        str(tmp_path / "reg32.lef")], capture_output=True, text=True)
    assert r.returncode == 0
    # unconnected: met5 pins too short for any vertical strap to cross them,
    # met4 pins clear of every strap -- no short, no supply either
    _toy_lef(tmp_path / "short.lef", met5_x_hi=8.0)
    _toy_config(tmp_path / "unc.json", 10, **{"MACROS": {"reg32": {
        "instances": {"u0": {"location": [10, 20], "orientation": "N"}}, "lef": ["dir::short.lef"]}}})
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "unc.json")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "UNCONNECTED u0 VPWR" in r.stdout and "UNCONNECTED u0 VGND" in r.stdout
    assert "0 clips in 0 instances, 2 unconnected" in r.stdout
    _toy_config(tmp_path / "rel.json", 10, FP_SIZING="relative")
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "rel.json")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "FP_SIZING absolute + DIE_AREA required" in r.stderr
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "good.json"),
                        str(tmp_path / "missing.lef")], capture_output=True, text=True)
    assert r.returncode == 2 and "no such LEF" in r.stderr


def test_pdn_phase_reads_the_obstruction_that_removes_the_straps(tmp_path):
    """The block's OBS decides what can feed it, so the check reads it -- and
    what matters is WHOSE metal it is.  `final/lef/<cell>.lef` is MAGIC's LEF,
    so its obstruction is the block's actual metal, and on the PDN layers that
    metal IS the block's own grid: the same rectangles as its power pins, which
    the phase search clears by clearing them.  Obstruction that is NOT the
    block's own power metal is the dangerous kind -- signal routing pushed onto
    a PDN layer, wherever the router put it -- and it is what made pdngen drop
    straps and fail IR-drop signoff on the phase-0 toy."""
    # (a) the block's OWN power metal as OBS -- what Magic's LEF actually
    # carries on the PDN layers -- says nothing the pins did not, so it must
    # neither cut nor be reported as an obstruction
    _toy_lef(tmp_path / "own.lef", obs="pins")
    _toy_config(tmp_path / "own.json", 10, **{"MACROS": {"reg32": {
        "instances": {"u0": {"location": [10, 20], "orientation": "N"}}, "lef": ["dir::own.lef"]}}})
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "own.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OBSTRUCTED" not in r.stdout and "own-power OBS" in r.stdout
    assert "PASS: 1 instances" in r.stdout
    # (b) FOREIGN metal on a PDN layer -- signal routing pushed onto met4 --
    # is the dangerous one: no phase search cleared it
    _toy_lef(tmp_path / "met4_obs.lef", obs=("met1", "met2", "met3", "met4"))
    _toy_config(tmp_path / "m4.json", 10, **{"MACROS": {"reg32": {
        "instances": {"u0": {"location": [10, 20], "orientation": "N"}}, "lef": ["dir::met4_obs.lef"]}}})
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "m4.json")],
                       capture_output=True, text=True)
    assert "OBSTRUCTED reg32 (e.g. u0) on met4" in r.stdout, r.stdout
    assert "RT_MAX_LAYER" in r.stdout          # the remedy, named
    # met4 is gone over the macro, so the met4 pins can only be fed across
    # layers -- and here the default met5 grid misses them
    assert r.returncode == 1 and "UNCONNECTED u0" in r.stdout
    _toy_lef(tmp_path / "sealed.lef", obs=("met4", "met5"))
    _toy_config(tmp_path / "sealed.json", 10, **{"MACROS": {"reg32": {
        "instances": {"u0": {"location": [10, 20], "orientation": "N"}}, "lef": ["dir::sealed.lef"]}}})
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "sealed.json")],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "carries foreign metal on BOTH met4 and met5: nothing can reach it" in r.stdout, r.stdout
    assert "RT_MAX_LAYER" in r.stdout
    # an OBS drawn as a polygon is refused rather than read as nothing
    (tmp_path / "poly.lef").write_text((tmp_path / "sealed.lef").read_text().replace(
        "      RECT 0.000 0.000 80.000 80.000 ;", "      POLYGON 0 0 80 0 80 80 ;", 1))
    _toy_config(tmp_path / "poly.json", 10, **{"MACROS": {"reg32": {
        "instances": {"u0": {"location": [10, 20], "orientation": "N"}}, "lef": ["dir::poly.lef"]}}})
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "poly.json")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "OBS as a POLYGON" in r.stderr, r.stderr


def test_strap_enumeration_is_pdngens_own_loop():
    """`Straps::makeStraps` is not "every k the pitch allows": the period loop
    runs while `pos <= pos_end` so a centre ON the core edge still counts, each
    NET's strap is dropped once its own centre passes the end (the last period
    can be a lone VPWR), and a strap whose rectangle leaves the die is skipped.
    Latent on the emitted sets -- their last straps are far from the edge -- so
    it is pinned here instead."""
    sys.path.insert(0, str(_T1A))
    import pdn_phase as pp
    straps = pp.straps_along(5.52, 244.48, 8.48, 46, 1.6, 1.7, "VPWR", "VGND", 0.0, 250.0)
    last = [(s["net"], round(s["lo"], 3), round(s["hi"], 3)) for s in straps[-2:]]
    # the VPWR of the last period is kept (its centre 244.0 <= 244.48); its
    # VGND partner would centre at 247.3, past the end, so pdngen drops it
    assert last[-1] == ("VPWR", 243.2, 244.8), straps[-3:]
    assert len(straps) % 2 == 1 and straps[-1]["net"] == "VPWR"
    assert not any(s["net"] == "VGND" and s["lo"] > 245 for s in straps)
    # a centre landing exactly on the core edge is still generated
    on_edge = pp.straps_along(0.0, 100.0, 0.0, 50.0, 1.6, 1.7, "VPWR", "VGND", -10.0, 110.0)
    assert [round(s["c"], 3) for s in on_edge if s["net"] == "VPWR"] == [0.0, 50.0, 100.0]
    # ... and a strap whose rect leaves the DIE is skipped without stopping
    clipped = pp.straps_along(0.0, 100.0, 0.0, 50.0, 1.6, 1.7, "VPWR", "VGND", 0.0, 110.0)
    assert [round(s["c"], 3) for s in clipped if s["net"] == "VPWR"] == [50.0, 100.0]


@pytest.mark.skipif(not _HAS_TCLSH, reason="gen.sh emits the set through gen.sh/tclsh")
def test_harm_sh_measures_the_die_fit_shift_from_the_dies_own_origin(tmp_path):
    """The shift is the smallest translation putting every macro halo inside
    the die, and "inside" is measured from the DIE's origin, not from zero: a
    DEF whose DIEAREA starts at (10, 10) needs 10 um more, not 10 um less.
    The macro BODY is what must fit -- a halo reaching past the die edge only
    means no other cell fits beside it there."""
    d = _emit(tmp_path, 2)
    defp = d / "tpu.def"
    txt = defp.read_text()
    m = re.search(r"DIEAREA \( (\d+) (\d+) \) \( (\d+) (\d+) \) ;", txt)
    x0, y0, x1, y1 = (int(v) for v in m.groups())
    off = 10000                                             # 10 um at 1000 dbu
    defp.write_text(txt.replace(m.group(0),
                    f"DIEAREA ( {x0 + off} {y0 + off} ) ( {x1 + off} {y1 + off} ) ;"))
    r = subprocess.run(["bash", str(_T1A / "harm.sh"), "2"], env={**os.environ, "T1A_DIR": str(tmp_path)},
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    pl = json.loads((d / "h" / "top" / "placement.json").read_text())
    die, comps = pl["die_um"], pl["instances"]
    halo = pl["halo_um"]
    assert die[0] == 10.0 and die[1] == 10.0
    assert pl["shift_um"] == [max(0.0, die[0] + halo[0] - min(c["def_location"][0] for c in comps)),
                              max(0.0, die[1] + halo[1] - min(c["def_location"][1] for c in comps))]
    assert min(c["x"] for c in comps) == die[0] + halo[0]   # the leftmost halo touches the die edge
    assert min(c["y"] for c in comps) >= die[1]
    for c in comps:                                        # every BODY inside the die
        assert die[0] <= c["x"] and c["x"] + c["size"][0] <= die[2]
        assert die[1] <= c["y"] and c["y"] + c["size"][1] <= die[3]


def test_utilization_advice_names_the_bar_and_the_measured_pepad():
    """The estimate is what tells a user to regenerate the set, so it has to be
    right about the number AND about which bar the placer trips on.  The first
    real run measured the PE at 5,964 um^2 against a ~3,900 Yosys-derived guess
    -- section 7.1's ~1.7x ratio, which the estimate now applies to the cells
    no run has measured -- and refused at GPL-0301, not at the density bar."""
    sys.path.insert(0, str(_T1A))
    import harm
    area, how = harm.cell_area_estimate("pe_cell")
    assert area == 5964.0 and "MEASURED" in how
    sparse, how2 = harm.cell_area_estimate("feed_cell")
    assert sparse == pytest.approx(250.0 * 1.7) and "1.7" in how2
    line = harm.utilization_advice("pe_cell", 152, 56)          # the emitter's default
    assert "GPL-0301" in line and "-PEPAD 100" in line
    assert "PL_TARGET_DENSITY_PCT" not in line                  # the FIRST bar it hits
    ok = harm.utilization_advice("pe_cell", 228, 132)           # at PEPAD 100
    assert "PEPAD" not in ok and "23605" in ok.replace(",", "")
    # a cell that clears 100 % but not the density bar names that one instead
    mid = harm.utilization_advice("pe_cell", 184, 88)
    assert "PL_TARGET_DENSITY_PCT" in mid and "GPL-0302" in mid


def test_runtimes_blocks_from_the_top_config(tmp_path):
    """`--blocks-from top/config.json` yields the row the explicit --block
    form yields: run dir three levels above each cell's lef view, instance
    count from MACROS.<cell>.instances; a cell whose run is missing is named."""
    top = tmp_path / "top"
    _fake_run(top / "runs" / "h")
    for cell, wl in (("pe_cell", 1000), ("acc_cell", 50)):
        _fake_run(tmp_path / cell / "runs" / "h")
        (tmp_path / cell / "runs" / "h" / "final" / "metrics.json").write_text(
            json.dumps({"route__wirelength": wl, "route__drc_errors": 0}))
    cfg = {"MACROS": {
        "pe_cell": {"instances": {f"row_0.pe_{i}": {} for i in range(3)},
                    "lef": ["dir::../pe_cell/runs/h/final/lef/pe_cell.lef"]},
        "acc_cell": {"instances": {"acc_0": {}}, "lef": ["dir::../acc_cell/runs/h/final/lef/acc_cell.lef"]}}}
    (top / "config.json").write_text(json.dumps(cfg))
    derived = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top / "runs" / "h"),
                              "--blocks-from", str(top / "config.json"), "--json"],
                             check=True, capture_output=True, text=True).stdout
    explicit = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top / "runs" / "h"),
                               "--block", f"{tmp_path / 'pe_cell' / 'runs' / 'h'}:3",
                               "--block", f"{tmp_path / 'acc_cell' / 'runs' / 'h'}:1", "--json"],
                              check=True, capture_output=True, text=True).stdout
    row, row2 = json.loads(derived), json.loads(explicit)
    assert row["route__wirelength__blocks"] == 3 * 1000 + 50
    assert [b["instances"] for b in row["blocks"]] == [3, 1]
    assert {k: v for k, v in row.items() if k != "blocks"} == {k: v for k, v in row2.items() if k != "blocks"}
    shutil.rmtree(tmp_path / "acc_cell")
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top / "runs" / "h"),
                        "--blocks-from", str(top / "config.json")], capture_output=True, text=True)
    assert r.returncode != 0 and "acc_cell" in r.stderr and "does not exist" in r.stderr
