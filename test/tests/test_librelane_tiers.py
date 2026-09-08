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
import io
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
def test_a_failing_dry_run_warns_and_still_writes_the_arm(tmp_path, monkeypatch, capsys):
    """`pdn_phase.py`'s verdict must not GATE the arm.

    It used to raise, and that abort is the proximate cause of the study's
    only PDN failure (#893): it refused to write a plan that works, the only
    way past it was to hand-edit PDN_HOFFSET, and the hand offset cut VGND's
    met5 into 85 pieces -- PSM-0069 at signoff on a design whose generated
    plan was fine.  So a failing dry run is REPORTED and the arm is written.
    """
    d = _emit(tmp_path, 2)
    sys.path.insert(0, str(_T1A))
    import harm
    import pdn_phase as pp

    real = pp.run_check
    monkeypatch.setattr(pp, "run_check", lambda *a, **k: {**real(*a, **k), "pass": False})
    out = tmp_path / "hout"
    r = harm.write_h(str(d), str(out), (pp.SKY130["FP_MACRO_HORIZONTAL_HALO"],
                                        pp.SKY130["FP_MACRO_VERTICAL_HALO"]))
    assert r["dry_run_failed"] is True
    assert (out / "top" / "config.json").is_file()          # written anyway
    err = capsys.readouterr().err
    assert "ADVISORY" in err and "Do NOT hand-edit" in err
    assert "PSM" in err


def test_the_generated_readme_no_longer_teaches_the_fix_that_broke_the_pdn(tmp_path):
    """The doc recorded the lesson while this template still instructed the
    action that caused it -- and the template is written fresh into every arm
    directory, so it is what the next person reads."""
    src = (_T1A / "harm.py").read_text()
    body = src[src.index("def render_readme"):src.index("def main(")]
    assert "fix the\noffsets in top/config.json" not in body
    assert "never the top blind" not in body
    assert "ADVISORY" in body and "Do not hand-edit" in body
    # and it points at the verdict that counts, with the way to localise it
    assert "PSM-0040" in body and "grid-errors.rpt" in body
    assert "pdn_connect.py" in body and "--self-cross" in body


def test_pdn_phase_says_it_is_advisory_wherever_it_is_read(tmp_path):
    """Anyone can run it directly, so the caveat has to travel with the tool
    and not only with the doc."""
    sys.path.insert(0, str(_T1A))
    import pdn_phase as pp
    assert "ADVISORY" in pp.__doc__ and "PSM-0069" in pp.__doc__
    assert "verdict" in pp.ADVISORY and "pdn_connect.py" in pp.ADVISORY


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


def test_pdn_phase_offers_the_smallest_verified_shift_and_searches_pairs(tmp_path, monkeypatch):
    """The remedy is ONE whole-placement shift, verified before it is
    offered: the smaller single-axis shift when either axis alone clears
    the prediction, else a searched (dx, dy) pair -- a design failing on
    both axes passes neither single search and used to get no remedy at
    all (Codex #885).  Here u0 at x=20 puts its VGND met4 pins under VPWR
    straps and an HOFFSET of 28.7 puts the VPWR met5 strap over the VGND
    met5 pin, which cuts it over both macros; a y-shift alone clears it
    (the whole met5 VPWR strap then vias u0's met4 VPWR pins), so that is
    what is offered, not the pair.  The pair search itself is exercised
    with the verdict stubbed: only a shift on BOTH axes passes, and one is
    found from the two axes' candidates within the budget."""
    _toy_lef(tmp_path / "reg32.lef")
    _toy_config(tmp_path / "both.json", 20, FP_PDN_HOFFSET=28.7, FP_PDN_HPITCH=153.18)
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "both.json"),
                        "--json", str(tmp_path / "both.out")], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    out = json.loads((tmp_path / "both.out").read_text())
    assert sorted({t["axis"] for t in out["trims"]}) == ["x", "y"]          # both axes trimmed
    assert out["global_dx"] is None and out["global_dy"] == -5.0
    assert out["global_shift"] == [0.0, -5.0] and out["global_clean_at_shift"] is True
    assert "shifting EVERY macro by dy=-5.000 (PDN_HOFFSET=33.7)" in r.stdout
    assert "both axes needed" not in r.stdout
    # the pair search: when no single axis clears it, the candidates of both
    # axes are tried as pairs, smallest total move first, within the budget
    import pdn_phase as pp
    top = pp.read_top_config(str(tmp_path / "both.json"))
    lefs = pp.read_lef(str(tmp_path / "reg32.lef"))
    real = pp.clean_at
    monkeypatch.setattr(pp, "clean_at", lambda *a: a[-2] != 0 and a[-1] != 0)
    res = pp.run_check(top, lefs)
    assert res["global_dx"] is None and res["global_dy"] is None
    dx, dy = res["global_shift"]
    assert dx != 0 and dy != 0 and abs(dx) <= 15 and abs(dy) <= 153.18 / 2      # half a pitch each
    assert res["global_clean_at_shift"] is True
    buf = io.StringIO()
    pp.report(top, lefs, res, buf)
    assert f"dx={dx:+.3f} (PDN_VOFFSET={res['voffset_for_shift']}) and dy={dy:+.3f}" in buf.getvalue()
    assert "both axes needed: neither alone does" in buf.getvalue()
    monkeypatch.setattr(pp, "clean_at", lambda *a: False)
    res = pp.run_check(top, lefs)
    assert res["global_shift"] is None
    buf = io.StringIO()
    pp.report(top, lefs, res, buf)
    assert "nor a pair within the trial budget" in buf.getvalue()
    monkeypatch.setattr(pp, "clean_at", real)


def test_pdn_phase_finds_the_toys_trim_and_the_shift_that_clears_it(tmp_path):
    """The phase-0 toy's failure, recomputed through pdngen's own steps: with
    u0 at x = 20 its VGND met4 pin (33.22-35.22) meets the top's VPWR strap
    (34.72-36.32, offset 0 pitch 30 from the 5.52 core origin) -- the doc's
    own numbers -- so pdngen CUTS that strap over the macro (a TRIM, spacing
    across and halo along), and with all three cut the fragments left over
    u0 are via-less stubs that TRIM removes, so u0's VPWR terminal sits on
    no grid: STRANDED, the PSM-0069 shape.  The smallest clearing shift is
    1.1 um west -- the pin's spacing plus the strap's, both 0.3 on met4,
    which is how `Shape::cut` grows the violation.  At x = 10 (10 = 160 mod 30, the toy's measured fix) the
    same check passes -- and it passes WITH the two shapes the old model
    called failures (#895): the met4 VPWR strap on the core's left edge,
    whose 0.8 um crossings hold no via and which trim removes, and a VGND
    met4 pin rectangle off the grid whose TERMINAL is fed by its met5
    rectangle.  A macro whose pins no strap crosses is STRANDED on both
    nets with no trim at all, and a top without a fixed die is refused
    (exit 2)."""
    _toy_lef(tmp_path / "reg32.lef")
    _toy_config(tmp_path / "bad.json", 20)
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "bad.json"),
                        "--json", str(tmp_path / "bad.out")], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "TRIM u0 VGND pin VGND on met4 cuts VPWR strap k=1 [34.720,36.320] over y [20.580,99.420]" in r.stdout
    assert "STRANDED u0 VPWR pin VPWR: its component (4 shapes" in r.stdout
    assert "every strap fragment on it is via-less and trimmed away" in r.stdout
    assert ("FAIL: 2 instances, 3 trims in 1 instances, 1 stranded terminal(s) in 1 instance-net(s), "
            "0 floating fragment(s); shifting EVERY macro by dx=-1.100 (PDN_VOFFSET=1.1)") in r.stdout
    out = json.loads((tmp_path / "bad.out").read_text())
    assert out["global_dx"] == -1.1 and out["global_shift"] == [-1.1, 0.0]
    assert out["voffset_for_shift"] == 1.1 and out["global_clean_at_shift"] is True
    assert out["min_connections"] == 1                       # pin layers: one via keeps a fragment
    assert out["unconnected"] == [{"instance": "u0", "net": "VPWR"}]
    u0 = next(p for p in out["per_instance"] if p["instance"] == "u0")
    assert u0["trims"] == 3 and u0["connected"] == {"VPWR": False, "VGND": True}
    assert all(p["trims"] == 0 for p in out["per_instance"] if p["instance"] == "u1")
    _toy_config(tmp_path / "good.json", 10)                     # the toy's fix
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "good.json"),
                        "--json", str(tmp_path / "good.out")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS: 2 instances, 16 power-pin rects, 0 trims in 0 instances, every terminal" in r.stdout
    assert "(1 via-less trimmed away)" in r.stdout
    out = json.loads((tmp_path / "good.out").read_text())
    assert not out["failures"] and len(out["trimmed_away"]) == 1
    edge = out["trimmed_away"][0]
    assert edge["net"] == "VPWR" and edge["layer"] == "met4" and edge["k"] == 0 and edge["vias"] == 0
    assert edge["rect"][0] == 4.72 and edge["rect"][2] == 6.32               # the core-edge strap
    assert {p["net"] for p in out["off_grid_pins"]} == {"VGND"}
    assert sorted(t for p in out["off_grid_pins"] for t in p["terminals"]) == ["u0.VGND", "u1.VGND"]
    assert out["network"]["VGND"]["terminals_on_grid"] == 2 and out["network"]["VPWR"]["terminals_on_grid"] == 2
    assert "ASSUMED" in r.stdout and "PDN_HPITCH=153.18" in r.stdout   # what it took from the defaults
    # the LEF passed explicitly wins over the config's path
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "good.json"),
                        str(tmp_path / "reg32.lef")], capture_output=True, text=True)
    assert r.returncode == 0
    # stranded on both nets: met5 pins too short for any vertical strap to
    # cross them, met4 pins clear of every strap -- no trim, no supply either
    _toy_lef(tmp_path / "short.lef", met5_x_hi=8.0)
    _toy_config(tmp_path / "unc.json", 10, **{"MACROS": {"reg32": {
        "instances": {"u0": {"location": [10, 20], "orientation": "N"}}, "lef": ["dir::short.lef"]}}})
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "unc.json")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "STRANDED u0 VPWR pin VPWR" in r.stdout and "STRANDED u0 VGND pin VGND" in r.stdout
    assert "FAIL: 1 instances, 0 trims in 0 instances, 2 stranded terminal(s) in 2 instance-net(s)" in r.stdout
    _toy_config(tmp_path / "rel.json", 10, FP_SIZING="relative")
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "rel.json")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "FP_SIZING absolute + DIE_AREA required" in r.stderr
    r = subprocess.run([sys.executable, str(_T1A / "pdn_phase.py"), str(tmp_path / "good.json"),
                        str(tmp_path / "missing.lef")], capture_output=True, text=True)
    assert r.returncode == 2 and "no such LEF" in r.stderr


def test_pdn_phase_models_trim_in_both_directions(tmp_path):
    """#895's two halves.  A same-layer meeting is a TRIM, which is never a
    failure by itself: the old model's "N clips" FAIL on a plan that works
    (144 clips on the H+B set, PSM clean) came from counting them.  And a
    cut CAN strand: the hand HOFFSET that fragmented VGND's met5 into 85
    pieces failed PSM-0069, and this toy's HOFFSET 28.7 reproduces the shape
    -- fragments that survive trim on a component off the grid (FLOATING,
    the "unconnected shapes" PSM counts) and a terminal whose every
    rectangle is off it (STRANDED).  Which fragments survive is pdngen's
    trim rule, read from the config: with PDN_SKIPTRIM every fragment
    survives (the via-less ones included), with PDN_ENABLE_PINS off a
    fragment needs two vias, and the counts move monotonically."""
    import pdn_phase as pp
    _toy_lef(tmp_path / "reg32.lef")
    lefs = pp.read_lef(str(tmp_path / "reg32.lef"))
    runs = {}
    for name, extra in (("default", {}), ("skiptrim", {"PDN_SKIPTRIM": True}),
                        ("nopins", {"PDN_ENABLE_PINS": False}), ("fp_skip", {"FP_PDN_SKIPTRIM": "true"})):
        _toy_config(tmp_path / f"{name}.json", 20, FP_PDN_HOFFSET=28.7, FP_PDN_HPITCH=153.18, **extra)
        runs[name] = pp.run_check(pp.read_top_config(str(tmp_path / f"{name}.json")), lefs)
    d, sk, npn = runs["default"], runs["skiptrim"], runs["nopins"]
    assert (d["min_connections"], sk["min_connections"], npn["min_connections"]) == (1, None, 2)
    assert runs["fp_skip"]["min_connections"] is None              # the deprecated spelling, as a string
    # the same cuts whatever trim does ...
    for r in (sk, npn):
        assert r["trims"] == d["trims"]
    for r in (d, sk):
        assert [(s["instance"], s["net"]) for s in r["stranded"]] == [("u0", "VPWR"), ("u1", "VGND")]
    # ... but what survives them differs, and it is what PSM would count
    assert len(npn["floating"]) == 0 < len(d["floating"]) == 8 < len(sk["floating"]) == 19
    assert len(sk["trimmed_away"]) == 0 and len(d["trimmed_away"]) == 11 and len(npn["trimmed_away"]) == 23
    assert all(f["vias"] == 1 for f in d["floating"])              # one via keeps it on a pin layer
    assert all(f["vias"] == 0 for f in d["trimmed_away"])
    # trim runs BEFORE the partition (Codex #900): with two vias required,
    # the one-via straps that fed u1's VPWR and u0's VGND are removed, and
    # the terminals they bridged into the grid are stranded -- partitioning
    # first would have kept them on the main component and passed them
    assert [(s["instance"], s["net"]) for s in npn["stranded"]] == [
        ("u0", "VPWR"), ("u1", "VPWR"), ("u0", "VGND"), ("u1", "VGND")]
    buf = io.StringIO()
    pp.report(pp.read_top_config(str(tmp_path / "default.json")), lefs, d, buf)
    text = buf.getvalue()
    assert "FLOATING VGND met4 fragment k=4 [128.020,10.880,129.620,109.120] with 1 via(s)" in text
    assert "STRANDED u1 VGND pin VGND: its component (5 shapes" in text and "2 of its fragments survive trim" in text
    assert "trim: a strap fragment with fewer than 1 via is removed" in text
    buf = io.StringIO()
    pp.report(pp.read_top_config(str(tmp_path / "skiptrim.json")), lefs, sk, buf)
    assert "trim: SKIPPED (PDN_SKIPTRIM)" in buf.getvalue()
    # with trim skipped the core-edge strap is a permanent floating shape no
    # shift removes, and the check says so rather than offering one
    assert sk["global_shift"] is None and "nor a pair within the trial budget" in buf.getvalue()
    # the prediction and the post-mortem share one network code: what
    # pdn_connect reads off a DEF is what this predicts from the LEFs
    from pdn_connect import net_components
    assert pp.predicted_network.__doc__ and "pdn_connect.net_components" in pp.predicted_network.__doc__
    assert net_components.__defaults__[-1] is False                # members only on request


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
    assert r.returncode == 1 and "STRANDED u0 VPWR pin VPWR" in r.stdout and "STRANDED u0 VGND" in r.stdout
    assert "TRIM u0 OBS on met4 cuts VPWR strap k=1 [34.720,36.320] over y [9.700,110.300]" in r.stdout
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


# ── arm H+B: pins.sh, harm.sh --pins ──────────────────────────────────────
@pytest.mark.skipif(not _HAS_TCLSH, reason="gen.sh emits the set through tclsh")
def test_pins_sh_writes_one_template_per_leaf_cell_and_harm_consumes_it(tmp_path):
    """`pins.sh N` routes the emitted array and writes one FP_DEF_TEMPLATE
    per leaf CELL TYPE -- a template, not a per-instance file, because the
    arm hardens each cell once and places it N^2 times -- and
    `harm.sh N --pins pins` puts each one into its block's config.

    Three properties earn their assertions: the templates are per CELL (so
    the count is the LEF's macro count, whatever N is), no two pins in one
    template share metal (two nets on one rectangle is a short, and a
    template can produce one where no single instance does -- each instance
    contributes the pins it routes), and every block is capped at
    `RT_MAX_LAYER met3`, which is the pin template's own risk: the extra
    internal wire a template costs is what pushes a block up onto met4, and
    a block with a met4 OBS made pdngen drop the straps that would have fed
    it (§8 step 5b, PSM-0069)."""
    d = _emit(tmp_path, 2)
    env = {**os.environ, "T1A_DIR": str(tmp_path)}
    r = subprocess.run(["bash", str(_T1A / "pins.sh"), "2"], env=env,
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    cells = sorted(_lef_sizes(d / "tpu.lef"))
    assert cells, "the emitted LEF declares no macro"
    for c in cells:
        text = (d / "pins" / f"{c}.def").read_text()
        assert f"DESIGN {c} ;" in text
        n = int(re.search(r"^PINS (\d+) ;", text, re.M).group(1))
        assert n > 0
        # one pin per piece of metal
        boxes = []
        for m in re.finditer(r"- (\S+) \+ NET \S+.*?LAYER (\S+) "
                             r"\( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)"
                             r".*?PLACED \( (-?\d+) (-?\d+) \)", text):
            nm, lay = m.group(1), m.group(2)
            x1, y1, x2, y2, px, py = (int(v) for v in m.group(3, 4, 5, 6, 7, 8))
            boxes.append((nm, lay, px + x1, py + y1, px + x2, py + y2))
        assert len(boxes) == n
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert not (a[1] == b[1] and a[2] < b[4] and b[2] < a[4]
                            and a[3] < b[5] and b[3] < a[5]), \
                    f"{c}: pins {a[0]} and {b[0]} share metal"

    r = subprocess.run(["bash", str(_T1A / "harm.sh"), "2", "--pins", "pins"],
                       env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "arm=H+B" in r.stdout
    for c in cells:
        cfg = json.loads((d / "h" / c / "config.json").read_text())
        # `permissive`, not `strict`: BUDA plans against the emitter's
        # structural view and a block run synthesizes the twin, whose
        # `clk`/`rst` no bus reaches -- `strict` requires the two pin sets
        # to be identical and exits 1 on exactly that.  What strict was
        # protecting is checked afterwards by tools/pin_def_verify.py,
        # which the generated README's step 1 runs.
        assert cfg["FP_TEMPLATE_MATCH_MODE"] == "permissive"
        assert cfg["RT_MAX_LAYER"] == "met3"
        t = cfg["FP_DEF_TEMPLATE"]
        assert t.startswith("dir::")
        assert (d / "h" / c / t[len("dir::"):]).resolve() == \
            (d / "pins" / f"{c}.def").resolve()
    readme = (d / "h" / "README.md").read_text()
    assert readme.startswith("# Arm H+B ")
    assert "--set arm=H+B" in readme
    plan = json.loads((d / "h" / "top" / "pdn_plan.json").read_text())
    assert sorted(plan["pin_templates"]) == cells


@pytest.mark.skipif(not _HAS_TCLSH, reason="gen.sh emits the set through tclsh")
def test_without_pins_the_h_arm_is_byte_identical_and_a_gap_is_refused(tmp_path):
    """The H+B option must not move arm H: the same command without
    `--pins` writes exactly what it wrote before the option existed, key
    for key (a `pin_templates: {}` in every arm-H plan file would be a diff
    for no information).  And a templates directory missing ONE cell is
    refused BEFORE anything is written -- otherwise it would surface at
    that block's ApplyDEFTemplate, after the other three had hardened."""
    d = _emit(tmp_path, 2)
    env = {**os.environ, "T1A_DIR": str(tmp_path)}
    assert subprocess.run(["bash", str(_T1A / "pins.sh"), "2"], env=env,
                          capture_output=True, text=True, timeout=900).returncode == 0
    plain, pinned = tmp_path / "h_plain", tmp_path / "h_pinned"
    for out, extra in ((plain, []), (pinned, ["--pins", "pins"])):
        r = subprocess.run([sys.executable, str(_T1A / "harm.py"), str(d),
                            "--out", str(out)] + extra,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stdout + r.stderr
    for c in sorted(_lef_sizes(d / "tpu.lef")):
        a = json.loads((plain / c / "config.json").read_text())
        b = json.loads((pinned / c / "config.json").read_text())
        assert "FP_DEF_TEMPLATE" not in a and "FP_DEF_TEMPLATE" in b
        assert a["RT_MAX_LAYER"] == "met4", "arm H keeps met4"
        assert {k: v for k, v in b.items()
                if k not in ("FP_DEF_TEMPLATE", "FP_TEMPLATE_MATCH_MODE",
                             "RT_MAX_LAYER")} == \
            {k: v for k, v in a.items() if k != "RT_MAX_LAYER"}
    assert "pin_templates" not in json.loads(
        (plain / "top" / "pdn_plan.json").read_text())

    (d / "pins" / "pe_cell.def").unlink()
    r = subprocess.run([sys.executable, str(_T1A / "harm.py"), str(d),
                        "--out", str(tmp_path / "h_gap"), "--pins", "pins"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 1
    assert "no template for pe_cell" in r.stderr
    assert "pins.sh" in r.stderr
    assert not (tmp_path / "h_gap").exists(), "refused after writing"


def _acc_lef(path):
    """A 96 x 60 block with met2 OBS as Magic draws it -- ACTUAL shapes, not a
    cover: two met2 rects, and a met4 VGND pin at local x 69.52-71.52 (the
    #896 neighbour).  No met1 OBS at all."""
    L = ["VERSION 5.8 ;", "MACRO acc_cell", "  CLASS BLOCK ;", "  ORIGIN 0 0 ;", "  SIZE 96 BY 60 ;",
         "  PIN VGND", "    DIRECTION INOUT ;", "    USE GROUND ;", "    PORT",
         "      LAYER met4 ;", "      RECT 69.52 5.0 71.52 55.0 ;", "    END", "  END VGND",
         "  OBS", "    LAYER met2 ;", "      RECT 69.30 0.20 69.50 0.40 ;", "      RECT 10.0 10.0 50.0 50.0 ;",
         "  END", "END acc_cell", "END LIBRARY"]
    path.write_text("\n".join(L) + "\n")


def _lyrdb(path, items):
    """A KLayout report database with one <item> per (category, value)."""
    body = "".join(f"<item><category>'{c}'</category><cell>top</cell><visited>false</visited>"
                   f"<multiplicity>1</multiplicity><values><value>{v}</value></values></item>"
                   for c, v in items)
    path.write_text("<?xml version=\"1.0\"?><report-database><description>DRC</description>"
                    "<categories><category><name>m2.2</name></category></categories>"
                    f"<cells><cell><name>top</name></cell></cells><items>{body}</items></report-database>")


def test_drc_locate_maps_markers_to_cells_and_says_whose_metal(tmp_path):
    """#896's five `m2.2` markers are one defect: the same local spot of one
    cell.  `drc_locate.py` reads the lyrdb and the top DEF, inverts each
    instance's placement (orientation included) to give the CELL-LOCAL spot,
    groups equal spots, and -- with the LEF -- says per offending edge
    whether it sits on metal the macro's abstract claims, in a HOLE of the
    abstract (inside the box on no claimed shape: the router reads the spot
    as free, so the edge is the top's wire or macro metal the LEF omits --
    the nearest claimed shape is named, which is how a notch beside a pin
    reads), or outside it.  The N=8 ground truth: both edges of every
    marker inside the box, one on the macro's real met2 in a notch the LEF
    leaves beside pin `in[22]`, the other the TOP's wire overhanging into
    that notch -- so "obstructed layer, therefore no router wire" was a
    wrong inference, and a claimed-against-hole pair is reported as the
    notch shape whose fix is in the abstract."""
    _acc_lef(tmp_path / "acc_cell.lef")
    (tmp_path / "top.def").write_text("\n".join([
        "VERSION 5.8 ;", "DESIGN top ;", "UNITS DISTANCE MICRONS 1000 ;",
        "COMPONENTS 3 ;",
        "- pipe_1_3 acc_cell + PLACED ( 694000 2160000 ) N ;",
        "- pipe_1_4 acc_cell + PLACED ( 870000 2160000 ) N ;",
        "- acc_5 acc_cell + PLACED ( 400000 1000000 ) FS ;",       # mirrored: local y counts from the top
        "END COMPONENTS", "END DESIGN"]) + "\n")
    # the issue's own marker (edge A on the LEF's met2 OBS, edge B 0.13 um
    # above the cell edge on no LEF shape), the same spot in the neighbour,
    # the same spot in the MIRRORED instance (top coords differ, local do
    # not), a met1 marker over a macro (met1 unobstructed), and one in the
    # channel between macros
    _lyrdb(tmp_path / "drc.lyrdb", [
        ("m2.2", "edge-pair: (763.492,2160.27;763.37,2160.27)/(763.37,2160.14;763.44,2160.14)"),
        ("m2.2", "edge-pair: (939.492,2160.27;939.37,2160.27)/(939.37,2160.14;939.44,2160.14)"),
        ("m2.2", "edge-pair: (469.492,1059.73;469.37,1059.73)/(469.37,1059.86;469.44,1059.86)"),
        ("m1.1", "polygon: (720,2180;721,2180;721,2181;720,2181)"),
        ("m2.2", "edge-pair: (800,2158;801,2158)/(800,2158.1;801,2158.1)"),
        # straddling the boundary, the OUTER edge farther from it than the
        # inner one: the marker's centre is outside the macro, the marker is
        # still the macro's (Codex #900)
        ("m2.2", "edge-pair: (763.492,2160.27;763.37,2160.27)/(763.37,2159.6;763.44,2159.6)"),
    ])
    r = subprocess.run([sys.executable, str(_T1A / "drc_locate.py"), str(tmp_path / "drc.lyrdb"),
                        str(tmp_path / "top.def"), str(tmp_path / "acc_cell.lef"),
                        "--json", str(tmp_path / "loc.json")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads((tmp_path / "loc.json").read_text())
    m = out["markers"]
    assert m[0]["instance"] == "pipe_1_3" and m[0]["local"] == [69.37, 0.14, 69.492, 0.27]
    assert [ev["verdict"] for ev in m[0]["edge_verdicts"]] == ["macro-lef", "hole"]
    assert m[0]["edge_verdicts"][0]["on"] == ["OBS"] and m[0]["shape"] == "notch"
    assert m[0]["edge_verdicts"][1]["nearest_claimed"] == ["OBS", 0.06]     # the notch's width
    assert m[2]["instance"] == "acc_5" and m[2]["orient"] == "FS" and m[2]["local"] == [69.37, 0.14, 69.492, 0.27]
    assert [ev["verdict"] for ev in m[2]["edge_verdicts"]] == ["macro-lef", "hole"]
    assert m[3]["layer"] == "met1" and m[3]["edge_verdicts"][0]["verdict"] == "hole"
    assert m[3]["edge_verdicts"][0]["nearest_claimed"] is None and m[3]["shape"] == "hole"
    assert m[4]["instance"] is None and m[4]["nearest"]["instance"] == "pipe_1_3"
    assert m[5]["instance"] == "pipe_1_3" and m[5]["local"] == [69.37, -0.4, 69.492, 0.27]
    assert [ev["verdict"] for ev in m[5]["edge_verdicts"]] == ["macro-lef", "outside"]
    assert m[5]["shape"] == "boundary"
    assert out["groups"][0] == {"cell": "acc_cell", "layer": "met2", "local": [69.4, 0.1],
                                "instances": ["pipe_1_3", "pipe_1_4", "acc_5"]}
    assert "drc_locate: 6 marker(s), 2 categories (m1.1, m2.2), 5 inside a placed macro (1 cell type(s): acc_cell), 1 elsewhere" in r.stdout
    assert "edge B: (763.370,2159.600)-(763.440,2159.600) outside the macro box -- the top's routing against its edge" in r.stdout
    assert "m2.2 (763.370,2160.140)-(763.492,2160.270) -> pipe_1_3 [acc_cell N] local (69.370,0.140)-(69.492,0.270)" in r.stdout
    assert "edge A: (763.492,2160.270)-(763.370,2160.270) on metal the macro's LEF claims (OBS)" in r.stdout
    assert ("edge B: (763.370,2160.140)-(763.440,2160.140) inside the macro box on NO LEF shape"
            in r.stdout)
    assert "nearest claimed shape OBS at 0.060 um" in r.stdout
    assert "=> claimed metal against an abstract HOLE" in r.stdout and "openroad.lef" in r.stdout
    assert "GROUP acc_cell met2 local ~(69.4,0.1): 3 marker(s) in pipe_1_3, pipe_1_4, acc_5 -- one defect, repeated per instance" in r.stdout
    assert "-> no macro holds it; nearest pipe_1_3 [acc_cell] 10.500 um away" in r.stdout
    # without a LEF the location half still runs; the size then comes from nowhere, so nothing is located
    r = subprocess.run([sys.executable, str(_T1A / "drc_locate.py"), str(tmp_path / "drc.lyrdb"),
                        str(tmp_path / "top.def")], capture_output=True, text=True)
    assert r.returncode == 0 and "no LEF given, so no edge is classified" in r.stdout
    assert "0 inside a placed macro" in r.stdout
    # a file that is not a report database is refused, not read as empty
    (tmp_path / "junk.lyrdb").write_text("not xml")
    r = subprocess.run([sys.executable, str(_T1A / "drc_locate.py"), str(tmp_path / "junk.lyrdb"),
                        str(tmp_path / "top.def")], capture_output=True, text=True)
    assert r.returncode == 2 and "not a KLayout report database" in r.stderr


def test_the_documented_relative_paths_resolve_from_the_directory_they_say(tmp_path):
    """A recipe in the study's own docs that cannot be pasted is worse than
    no recipe, and `../..`-counting is exactly where one rots: the
    `check_grid.tcl` snippet said `cd <arm>/top` and then reached for
    `../../../phase0/...`, which from `n<N>/h/top` lands on
    `tier1a/phase0` -- a directory that does not exist (Codex #901).

    So each fenced block is WALKED the way pasting it would walk: a `cd`
    moves the working directory, and every `../`-prefixed script path after
    it is resolved against wherever the block has got to.  Only `.sh`/`.py`/
    `.tcl` targets are asserted -- a run directory is made by the recipe
    itself and is not checked in."""
    doc = (_ROOT / "docs" / "internal" / "librelane_hier_flow.md").read_text()
    t1a = _ROOT / "flow" / "librelane" / "tier1a"
    checked = 0
    for block in re.findall(r"```(?:bash)?\n(.*?)```", doc, re.S):
        cwd = None
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            for part in line.split("&&"):
                part = part.strip()
                if not part.startswith("cd "):
                    continue
                d = part[3:].strip().split()[0]
                d = re.sub(r"<[^>]*>", "8", d)
                if d.startswith("~/src/buda"):
                    cwd = _ROOT / d[len("~/src/buda"):].lstrip("/")
                elif d.startswith(("~", "/", "$")):
                    cwd = None                    # someone else's machine
                elif cwd is not None:
                    cwd = cwd / d
                else:
                    cwd = t1a / d                 # blocks that open in tier1a
            if cwd is None:
                continue
            for tok in re.findall(r"(?<![\w/.])\.\./[\w./-]+", line):
                if any(c in tok for c in "<>$*"):
                    continue
                target = (cwd / tok).resolve()
                if target.suffix in (".sh", ".py", ".tcl"):
                    assert target.exists(), (
                        f"in a block at {cwd.relative_to(_ROOT)}: '{tok}' resolves "
                        f"to {target}, which does not exist -- the recipe cannot "
                        f"be pasted")
                    checked += 1
    assert checked, "no relative script path was checked; has the doc changed shape?"
