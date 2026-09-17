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

"""E1's loop driver (`flow/tcl/converge.tcl`, convergence ladder item 5) and
the vehicle hooks it drives (`flow/tcl/converge_lib.tcl`, through `soc.tcl`).

What is worth pinning is the CONSTRUCTION, not the numbers — the tables in
docs/internal/convergence_e1.md are the measurement, and an engine change
that moved them is a result, not a regression:

  * a vehicle session takes the hooks and leaves a report the driver can
    read: both verdicts, marks, the reserve in force, the derived share
    lines with their kept/nsig, and one demand row per instance and
    patterned layer;
  * `-shares` really governs the next session (the log says every share
    was declared and the template solved under the thinned view);
  * the driver runs the three arms as loops — blind rounds stepping
    `reserve_top_layers`, the top-down-derived and the blind-derived
    informed rounds — writes one table, and the blind arm's round 1 is the
    blind-derived arm's measurement (one run, shared);
  * the reservation efficiency is computed the way the write-up says.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SOC = _ROOT / "flow" / "tcl" / "soc.tcl"
_DRIVER = _ROOT / "flow" / "tcl" / "converge.tcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]


def _tclsh(*args, cwd):
    return subprocess.run(["tclsh", *map(str, args)], capture_output=True,
                          encoding="utf-8", errors="replace", cwd=cwd,
                          timeout=900)


def _report(path):
    d = {"share": [], "tracks": [], "cap": [], "demand": []}
    for ln in path.read_text().splitlines():
        toks = ln.split()
        if not toks:
            continue
        # Tcl-list shaped: a word with a `/` is brace-quoted (`{io/p_0}`)
        toks = [t.strip("{}") for t in toks]
        if toks[0] in ("share", "tracks", "cap", "demand"):
            d[toks[0]].append(toks[1:])
        else:
            d[toks[0]] = toks[1:]
    return d


def test_a_vehicle_session_leaves_the_report_the_driver_reads(tmp_path):
    rep = tmp_path / "td.rep"
    shares = tmp_path / "td.buda"
    r = _tclsh(_SOC, 2, "-noheal", "-derive", shares, "-report", rep,
               cwd=tmp_path)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    d = _report(rep)
    assert d["verdict_first"] == d["verdict"] == ["0", "0", "0"]
    assert int(d["bundles"][0]) > 0 and d["marks"] == ["0"]
    assert d["healed"] == ["0"] and d["reserve"] == ["0"]
    assert int(d["wl_detailed"][0]) > 0
    assert d["cap"] == []                          # top-down: no band declared
    # the derived lines, with the kept/nsig pair a driver prices from
    assert d["share"], d
    for cell, layer, pct, kept, nsig, coll in d["share"]:
        assert 0 < int(pct) <= 100 and 0 < int(kept) <= int(nsig)
        assert f"set_cell_layer_share {cell} {layer} {pct}" in shares.read_text()
    # ... and the collision count is the TABLE's, not the own% column that
    # sits between worst% and collide (Codex P2 on #935: an unanchored
    # match captured own%'s digits as `coll` in every report)
    table = dict()
    for ln in (r.stdout + r.stderr).splitlines():
        m = re.match(r"^\s*(\S+)\s+(M\d)\s+\d+%\s+\d+/\d+\s+\d+\s+\S+\s+"
                     r"[\d.]+%\s+(\d+)%F?\s+(\d+)", ln)
        if m:
            table[(m[1], m[2])] = (int(m[3]), int(m[4]))
    assert table
    for cell, layer, pct, kept, nsig, coll in d["share"]:
        own_pct, collide = table[(cell, layer)]
        assert int(coll) == collide, (cell, layer, coll, own_pct, collide)
    assert any(o != c for o, c in table.values()), table
    # one demand row per placed instance and patterned layer
    assert d["demand"] and all(len(row) == 7 for row in d["demand"])
    insts = {row[0] for row in d["demand"]}
    assert "quad_0/cl_0" in insts and "l2" in insts


def test_the_shares_govern_the_next_session(tmp_path):
    shares = tmp_path / "td.buda"
    r = _tclsh(_SOC, 2, "-noheal", "-derive", shares, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]
    declared = re.findall(r"^set_cell_layer_share (\S+) (\S+) (\d+)",
                          shares.read_text(), re.M)
    assert declared
    rep = tmp_path / "bu.rep"
    r = _tclsh(_SOC, 2, "-bottomup", "-noheal", "-shares", shares,
               "-reserve", 1, "-report", rep, cwd=tmp_path)
    log = r.stdout + r.stderr
    for cell, layer, pct in declared:
        assert f"[LayerShare] {cell}: layer {layer} share {pct}%" in log, log[-3000:]
    assert "local solve under thinned view" in log
    assert "[LayerCaps] reserving the top 1 layer(s)" in log, log[-3000:]
    d = _report(rep)
    assert d["reserve"] == ["1"] and int(d["marks"][0]) > 0


def test_a_zero_step_is_refused_before_any_session_starts(tmp_path):
    """The blind loop steps `reserve` by `-step` until it passes
    `-maxreserve`; a zero step would re-run the same dirty round forever
    (Codex P2 on #935).  Refused up front, with the other two loop bounds,
    an `-arms` naming no arm (it wrote an empty table and exited 0), and a
    value-taking option with nothing after it — or an option-LOOKING word,
    a typo included: `-tag -heall` used to run the experiment tagged
    `-heall`."""
    out = tmp_path / "e1"
    for words, msg in [(["-step", 0], "-step takes a positive integer"),
                       (["-step", "x"], "-step takes a positive integer"),
                       (["-maxreserve", -1], "-maxreserve takes a non-negative"),
                       (["-informed", -1], "-informed takes a non-negative"),
                       (["-arms", ""], "-arms needs a value"),
                       (["-arms", ","], "-arms names no arm"),
                       (["-arms", "td,"], None),           # a stray comma is fine
                       (["-arms"], "-arms needs a value"),
                       (["-step"], "-step needs a value"),
                       # an option-LOOKING value, known or a typo, is missing
                       (["-tag", "-heall"], "-tag needs a value"),
                       (["-out", "-otu"], "-out needs a value"),
                       (["-arms", "-out", "x"], "-arms needs a value"),
                       # the server IGNORES an unreadable thread request;
                       # a readable out-of-range one is btcl's to clamp, so
                       # `-j -3` passes and the LATER arm check is what fires
                       (["-j", "foo"], "-j takes an integer or max"),
                       (["-j", "0x4"], "-j takes an integer or max"),   # Tcl-only spelling
                       (["-j", -3, "-arms", "nosuch"], "unknown arm 'nosuch'")]:
        if msg is None:
            continue
        r = _tclsh(_DRIVER, "soc", 2, *words, "-out", out, cwd=tmp_path)
        assert r.returncode != 0 and msg in r.stderr, (words, r.stderr[-500:])
        assert not list(out.glob("*.log")) if out.exists() else True


def test_a_rejected_reservation_is_a_failed_round_not_a_row(tmp_path):
    """`reserve_top_layers N` past the stack's ceiling (the six-layer SoC
    hosts N <= 4: the cells must keep an H and a V layer) prints `Error:`
    and the Tcl bridge RAISES on it, so the vehicle session dies before
    its report and the driver stops there with the engine's reason — a
    baseline route must never be recorded as `reserve 5` (Codex P2 on
    #935, whose premise was that the session carried on)."""
    rep = tmp_path / "r5.rep"
    r = _tclsh(_SOC, 2, "-bottomup", "-noheal", "-reserve", 5, "-report", rep,
               cwd=tmp_path)
    assert r.returncode != 0 and not rep.exists()
    assert "Error: reserve_top_layers" in r.stdout + r.stderr
    out = tmp_path / "e1"
    r = _tclsh(_DRIVER, "soc", 2, "-arms", "blind", "-step", 5,
               "-maxreserve", 5, "-out", out, cwd=tmp_path)
    assert r.returncode != 0, r.stdout[-2000:]
    assert "session 'soc2_blind_r2' left no report" in r.stderr, r.stderr[-1500:]
    assert "Error: reserve_top_layers" in r.stderr, r.stderr[-1500:]
    assert (out / "soc2_blind_r1.rep").exists()
    assert not (out / "soc2_blind_r2.rep").exists()
    assert not list(out.glob("e1_*.md"))            # no table, no reserve-5 row
    # a blind-only sweep derives no bu seed (Codex P2 on #935): the round-1
    # session is the blind arm's measurement alone, timed as such
    assert not (out / "soc2_bu_shares_r0.buda").exists()
    assert _report(out / "soc2_blind_r1.rep")["share"] == []


def test_zero_informed_rounds_summarize_the_measurement_itself(tmp_path):
    """`-informed 0` is legal: td's endpoint is its top-down round, bu's the
    blind measurement — the summary used to read an empty round list
    (Codex P2 on #935).  With `bu` and not `blind`, the blind sweep is
    round 1 alone and the summary carries no blind row."""
    out = tmp_path / "e1"
    r = _tclsh(_DRIVER, "soc", 2, "-arms", "td,bu", "-informed", 0,
               "-out", out, cwd=tmp_path)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    table = (out / "e1_soc_healerless_step1.md").read_text()
    summ = {ln.split("|")[2].strip(): [c.strip() for c in ln.split("|")[1:-1]]
            for ln in table.splitlines()
            if re.match(r"\| 2 \| (blind|td|bu) \|", ln)}
    assert set(summ) == {"td", "bu"}, table
    assert summ["td"][2] == "1" and summ["bu"][2] == "1", summ
    assert all(v[4] in ("clean", "dirty") for v in summ.values())
    # exactly the two sessions: blind r1 (bu's measurement) and td r0
    assert sorted(p.name for p in out.glob("*.rep")) == \
        ["soc2_blind_r1.rep", "soc2_td_r0.rep"], list(out.iterdir())


def test_the_lib_reads_a_scope_and_decides_the_blind_sweep(tmp_path):
    """`converge::scope_of` reads the derivation file's `# scope:` header —
    a cell with no line is still in scope — and falls back to the lines on
    a header-less file; an explicitly EMPTY scope is `(none)`, which
    `converge::scope_empty` tells from an unspecified one (the driver runs
    no informed round on it rather than re-deriving under the vehicle's
    default scope — Codex P2 on #935); `converge::blind_more` runs another
    blind round only while dirty AND the blind arm is selected."""
    hdr = tmp_path / "hdr.buda"
    hdr.write_text("# derive_cell_layer_shares: ...\n# scope: a_cell,b_cell,c_cell\n"
                   "set_cell_layer_share a_cell M6 75\n")
    old = tmp_path / "old.buda"
    old.write_text("set_cell_layer_share a_cell M6 75\n"
                   "set_cell_layer_share a_cell M7 50\n"
                   "set_cell_layer_share b_cell M6 60\n")
    none = tmp_path / "none.buda"
    none.write_text("# scope: (none)\n")
    script = f"""
        source {_ROOT / 'flow' / 'tcl' / 'converge_lib.tcl'}
        puts [converge::scope_of {hdr}]
        puts [converge::scope_of {old}]
        puts "<[converge::scope_of {none}]>"
        puts [list [converge::scope_empty [converge::scope_of {none}]] \
                   [converge::scope_empty [converge::scope_of {hdr}]] \
                   [converge::scope_empty ""]]
        puts [list [converge::blind_more {{blind td bu}} 0] \
                   [converge::blind_more {{blind td bu}} 1] \
                   [converge::blind_more {{bu}} 0] \
                   [converge::blind_more {{td bu}} 1]]
    """
    tcl = tmp_path / "lib.tcl"
    tcl.write_text(script)
    r = _tclsh(tcl, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["a_cell,b_cell,c_cell", "a_cell,b_cell",
                                     "<(none)>", "1 0 0", "1 0 0 0"], r.stdout


def test_the_driver_runs_the_three_arms_and_writes_the_table(tmp_path):
    out = tmp_path / "e1"
    r = _tclsh(_DRIVER, "soc", 2, "-informed", 1, "-maxreserve", 2,
               "-out", out, cwd=tmp_path)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    table = (out / "e1_soc_healerless_step1.md").read_text()
    assert table.splitlines()[0].startswith("<!-- converge.tcl soc 2")
    rows = [ln for ln in table.splitlines() if ln.startswith("| soc | 2 |")]
    arms = [ln.split("|")[4].strip() for ln in rows]
    assert "blind" in arms and "td" in arms and "bu" in arms
    # the blind arm's round 1 is the bu arm's measurement: ONE session
    assert (out / "soc2_blind_r1.rep").exists()
    assert (out / "soc2_bu_shares_r0.buda").exists()
    assert not (out / "soc2_bu_r0.rep").exists()
    # every informed round sourced the previous file and derived the next
    assert (out / "soc2_td_r0.rep").exists() and (out / "soc2_td_r1.rep").exists()
    assert (out / "soc2_td_shares_r0.buda").exists()
    # the summary names every arm with its rounds and endpoint
    summ = [ln for ln in table.splitlines() if re.match(r"\| 2 \| (blind|td|bu) \|", ln)]
    assert len(summ) == 3, table
    for ln in summ:
        cells = [c.strip() for c in ln.split("|")[1:-1]]
        assert int(cells[2]) >= 1 and cells[4] in ("clean", "dirty")
        assert re.fullmatch(r"\d+/\d+/\d+", cells[5]), ln


def test_reservation_efficiency_is_reserved_over_used(tmp_path):
    """The blind round with `reserve N` reserves EVERY track of the top N
    layers over every instance of a CAPPED cell — `reserve_top_layers`
    leaves the top level (the SoC's `quad_cell`) unrestricted, and its
    rows must not count (Codex P2 on #935); the informed round reserves the
    fraction the thinning removes.  Both computed from the report the way
    the write-up states, and checked here against a recount."""
    rep = tmp_path / "bl.rep"
    r = _tclsh(_SOC, 2, "-bottomup", "-noheal", "-reserve", 2,
               "-report", rep, cwd=tmp_path)
    d = _report(rep)
    layers = sorted({row[2] for row in d["demand"]},
                    key=lambda n: int(re.search(r"(\d+)$", n).group(1)))
    top = set(layers[-2:])
    # the report says which cells the reservation capped, and at what
    capped = {row[0]: row[2] for row in d["cap"]}
    assert capped and "quad_cell" not in capped and "cluster_cell" in capped
    assert set(capped.values()) == {layers[-3]}, capped
    cells_seen = {row[1] for row in d["demand"]}
    assert "quad_cell" in cells_seen               # so the exclusion bites
    reserved = sum(int(row[5]) for row in d["demand"]
                   if row[2] in top and row[1] in capped)
    used = sum(int(row[4]) for row in d["demand"]
               if row[2] in top and row[1] in capped)
    over_all = sum(int(row[5]) for row in d["demand"] if row[2] in top)
    assert over_all > reserved
    script = f"""
        source {_ROOT / 'flow' / 'tcl' / 'converge_lib.tcl'}
        set rep [converge::read_report {rep}]
        puts [converge::efficiency $rep $rep]
    """
    tcl = tmp_path / "eff.tcl"
    tcl.write_text(script)
    r = _tclsh(tcl, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    got = r.stdout.split()
    assert int(got[0]) == reserved and int(got[1]) == used, (got, reserved, used)


def test_the_reserve_primitive_hands_down_named_tracks(tmp_path):
    """`-primitive reserve` (ladder item 6): the top-down session derives
    `set_cell_layer_reserve` lines — the top's placed tracks over each
    cell's instances, in the cell's frame — and the report carries one
    `tracks CELL LAYER N` row per line under its OWN key (`reserve N` is
    the blind scalar; a row under the same key swallowed it).  The next
    session, bottom-up under the file, keeps every reserved track free of
    the cell's own metal — nested templates included, since a corridor
    over a cluster is inherited by the core solved inside it — which the
    `LAYER_RESERVE` audit reads as `own metal ... 0..0` on every row.
    Whether the design then routes CLEAN is E5's measurement, not this
    test's claim: at NQ=2 healerless it does not (the core's 32-bit bus
    moves off the reserved M5 seat onto LOW layers that cannot host it)."""
    rep = tmp_path / "td.rep"
    lines = tmp_path / "td.buda"
    r = _tclsh(_SOC, 2, "-noheal", "-derive", lines, "-primitive", "reserve",
               "-report", rep, cwd=tmp_path)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    d = _report(rep)
    assert d["reserve"] == ["0"] and d["share"] == [], d
    assert d["tracks"], d
    text = lines.read_text()
    assert text.startswith("# derive_cell_layer_reserves:") and "# scope: " in text
    for cell, layer, n in d["tracks"]:
        m = re.search(rf"^set_cell_layer_reserve {cell} {layer} (\S+)$", text, re.M)
        assert m and len(m[1].split(",")) == int(n), (cell, layer, n)
    rep2 = tmp_path / "bu.rep"
    r = _tclsh(_SOC, 2, "-bottomup", "-noheal", "-shares", lines,
               "-primitive", "reserve", "-report", rep2, cwd=tmp_path)
    log = r.stdout + r.stderr
    for cell, layer, n in d["tracks"]:
        assert f"[LayerReserve] {cell}: layer {layer} reserves {n} track(s)" in log, log[-3000:]
    assert re.search(r"\[LayerReserve\] cell 'core_cell': local solve with \d+ "
                     r"reserved track\(s\) kept free on .*from cluster_cell", log), log[-3000:]
    audit = re.findall(r"LAYER_RESERVE: (\S+) (\S+): .* own metal on reserved "
                       r"tracks: (\d+)\.\.(\d+) per instance(.*)$", log, re.M)
    assert audit and all(lo == "0" and hi == "0" and "VIOLATED" not in rest
                         for _c, _l, lo, hi, rest in audit), audit
    assert "BUDA-1920" not in log
    d2 = _report(rep2)
    assert int(d2["marks"][0]) > 0 and d2["tracks"] == []
    # ... and through the DRIVER: the option reaches every session it
    # spawns (a `session` proc reading `primitive` without declaring it
    # global crashed the first blind round), the table is the `_reserve`
    # twin, and the informed round's report carries the `tracks` rows
    out = tmp_path / "e1"
    r = _tclsh(_DRIVER, "soc", 2, "-arms", "td", "-informed", 1,
               "-primitive", "reserve", "-out", out, cwd=tmp_path)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert (out / "e1_soc_healerless_step1_reserve.md").exists(), list(out.iterdir())
    d0 = _report(out / "soc2_td_r0.rep")
    assert d0["tracks"] and d0["share"] == []
    assert "set_cell_layer_reserve" in (out / "soc2_td_shares_r0.buda").read_text()
    d1 = _report(out / "soc2_td_r1.rep")
    assert re.fullmatch(r"\d+", d1["verdict"][1])
