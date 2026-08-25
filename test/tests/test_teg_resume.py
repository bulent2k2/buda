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

"""Multi-rect + teg_mode across a checkpoint/resume (teg_multirect_status.md
open 6, the (a) measurement made durable).

A flat stage-resume (`btcl -r -s <stage>`) replays the recorded trace's SETUP
lines verbatim through do_command and then calls load_pipeline — measured
2026-08-23: the recorder writes `add_block L rect ... teg_mode over` VERBATIM,
so the resumed floorplan re-declares the rects and the OVER mode, and NOTHING
is lost on that path (the status doc's earlier "a resumed session loses
per-rect geometry" claim was wrong for the flat resume — the setup replay IS
the re-declaration the load_pipeline contract requires).

These tests drive the REAL recording half of that path: the build session
runs with `BUDA_RECORD` armed (the recorder is Python-side, written at
do_command — the choke point every driver passes through), the trace is
asserted to carry the multi-rect add_block line VERBATIM, and the resume
session replays the setup derived FROM THAT TRACE rather than from a
hand-reconstructed list — so if the recorder ever drops or mangles the line,
these tests fail.

What remains untested here (no tclsh in the harness environment): the
Tcl-side replay FILTER — which verbs `tools/buda_interact.tcl` classifies as
flat setup — has no Python twin, so `_flat_setup_from_trace` below MIRRORS
its rule (pipeline_prefixes / never_prefixes, flat = everything else
replays).  The trace CONTENT and the replay semantics are pinned here; only
"Tcl applies the same classification" rests on the mirror staying in sync
with `tools/buda_interact.tcl`.

The two halves that would silently break if the seam regressed:

* the CLEAN vehicle (flow/teg_over_audit.buda's L-shape, pinned
  TRUNK_V@x250 whose connector leg is real metal) must resume to the SAME
  routed endpoint — same bit-wires, same placed geometry, both audits clean;
* the DIRTY vehicle (an MST candidate on an OVER block — open 1 residual
  (iii), the remaining path that emits no TEG connection metal, since the
  trunk shapes now stub every rect) must still FIRE TEG_OPEN after the
  resume:
  detect_teg_open reads rects + teg_mode off the session FLOORPLAN
  (src/verify.cpp), so a resume that lost the re-declaration would read
  Success over an electrically open net — exactly the silent shape the audit
  exists to remove.

Restored candidates additionally carry busterm rects + teg_mode through the
seg-busterm persist bridge (pinned by test_seg_busterm_persist.py); these
tests cover the flow-level behavior on top of that row-level guarantee.
"""
import contextlib
import io

import buda
import buda_cli


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def _session(cmds=()):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in cmds:
        _run(s, c)
    return s


def _check(s, stage):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = s._check_design(stage)
    return verdict, buf.getvalue()


def _bits(s):
    """Placed bit-wire geometry as a comparable set."""
    return {(ns.bundle_id, ns.seg_idx, ns.bit_index, ns.layer,
             ns.track_position, ns.span_lo, ns.span_hi)
            for ns in s.detailed_result.net_segments}


# ── the flat-resume setup classification, mirrored from
#    tools/buda_interact.tcl (the Tcl code cannot run here — no tclsh; keep
#    these lists in sync with its pipeline_prefixes / never_prefixes) ──
_PIPELINE_PREFIXES = ("run_bundler", "run_hier_bundler", "generate_",
                      "run_planner", "run_nuts", "run_detailed_nuts",
                      "ripup_reroute", "negotiate_congestion",
                      "refine_selection", "check_")
_NEVER_PREFIXES = ("visualize", "save_bdb", "exit", "load_pipeline", "dump_",
                   "edit_", "emit_", "export_", "select_topolog",
                   "unpin_topology")


def _trace_lines(trace_path):
    """The recorded command lines (comments/blanks stripped)."""
    lines = []
    with open(trace_path) as fh:
        for ln in fh:
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                lines.append(ln)
    return lines


def _flat_setup_from_trace(trace_path, cut_verb):
    """The setup lines a flat `btcl -r -s <stage>` replays: everything before
    the cut (the first line whose verb is cut_verb) that is neither a
    pipeline command nor a never-replayed verb — FLAT setup is session state
    and replays wholesale."""
    setup = []
    for ln in _trace_lines(trace_path):
        verb = ln.split()[0]
        if verb == cut_verb:
            return setup
        if verb.startswith(_PIPELINE_PREFIXES) or verb.startswith(_NEVER_PREFIXES):
            continue
        setup.append(ln)
    raise AssertionError(f"cut verb {cut_verb!r} not found in trace")


_MULTIRECT_LINE_L = "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over"
_MULTIRECT_LINE_R2 = ("add_block r2 rect 500 0 600 100 rect 900 0 1000 100 "
                      "teg_mode over")


def _lshape_flow(ckpt):
    # The teg_over_audit.buda geometry: L block (tall arm + wide base), OVER.
    return [
        f"open_bdb {ckpt}",
        "add_block src 500 150 600 250",
        _MULTIRECT_LINE_L,
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "def_track_pattern 4 0 (SIGNAL 2 2)x8",
        "def_track_pattern 5 0 (SIGNAL 2 2)x8",
    ]


def test_flat_resume_keeps_rects_teg_and_routes_identically(tmp_path, monkeypatch):
    ckpt = str(tmp_path / "ckpt.bdb")
    trace = str(tmp_path / "ckpt.bdb.trace")

    # ── build session, RECORDED: pin the leg-carrying TRUNK_V@x250, route,
    #    checkpoint — the recorder is armed exactly as btcl -b arms it ──
    monkeypatch.setenv("BUDA_RECORD", trace)
    s1 = _session(_lshape_flow(ckpt) + ["run_bundler STRICT",
                                        "generate_topologies"])
    pin = None
    for i, c in enumerate(s1.bundles[0].input.candidates):
        if c.type.startswith("TRUNK_V@x250"):
            pin = i + 1
            break
    assert pin is not None, "TRUNK_V@x250 not generated"
    for cmd in (f"select_topology 1 {pin}", "run_planner", "run_nuts",
                "run_detailed_nuts"):
        _run(s1, cmd)
    monkeypatch.delenv("BUDA_RECORD")
    assert s1.detailed_result.num_unplaced == 0
    build_bits = _bits(s1)
    assert len(build_bits) == 12, "L-shape leg route is 3 segs x 4 bits"
    v1, _ = _check(s1, "dnuts")
    assert v1["by_kind"].get("TEG_OPEN", 0) == 0

    # ── the recorded trace carries the multi-rect declaration VERBATIM —
    #    the seam the resume machinery stands on ──
    lines = _trace_lines(trace)
    assert _MULTIRECT_LINE_L in lines, (
        "recorder dropped or mangled the multi-rect add_block line:\n"
        + "\n".join(lines))
    assert any(ln.startswith("open_bdb ") and "ckpt.bdb" in ln
               for ln in lines), lines

    # ── resume session: the flat stage-resume shape — setup derived FROM
    #    THE RECORDED TRACE (not a hand-built list), load_pipeline, tail
    #    from the nuts cut ──
    setup = _flat_setup_from_trace(trace, "run_nuts")
    assert _MULTIRECT_LINE_L in setup, setup
    assert not any(ln.startswith("select_topology") for ln in setup), \
        "pins are load_pipeline's to restore, never replayed as setup"
    s2 = _session(setup)
    # the re-declared floorplan holds the rects and the OVER mode
    assert s2.fp.get_block_teg_mode("L") == buda.TegMode.OVER
    assert len(s2.fp.get_block_rects("L")) == 2
    _run(s2, "load_pipeline")
    # restored candidates carry busterm rects + teg_mode (the persist bridge)
    sel = None
    for c in s2.bundles[0].input.candidates:
        if c.type.startswith("TRUNK_V@x250"):
            sel = c
            break
    assert sel is not None, "pinned candidate lost on restore"
    l_bts = [bt for pair in (sel.seg_busterms.get(i, (None, None))
                             for i in range(len(sel.segments)))
             for bt in pair if bt is not None and bt.block_name == "L"]
    assert l_bts and all(len(bt.rects) == 2 and
                         bt.teg_mode == buda.TegMode.OVER for bt in l_bts)
    for cmd in ("run_nuts", "run_detailed_nuts"):
        _run(s2, cmd)
    assert _bits(s2) == build_bits, "resume must reproduce the routed endpoint"
    v2, out2 = _check(s2, "dnuts")
    assert v2["by_kind"].get("TEG_OPEN", 0) == 0, out2


def test_flat_resume_keeps_teg_open_audit_armed(tmp_path, monkeypatch):
    ckpt = str(tmp_path / "dirty.bdb")
    trace = str(tmp_path / "dirty.bdb.trace")
    flow = [
        f"open_bdb {ckpt}",
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        _MULTIRECT_LINE_R2,
        "add_block r3 300 600 400 700",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c",
        "def_track_pattern 4 0 (SIGNAL 2 2)x8",
        "def_track_pattern 5 0 (SIGNAL 2 2)x8",
    ]

    # ── build session, RECORDED: an MST candidate on the OVER block (open 1
    #    residual (iii) — edges land on the closest rect pair only, so the
    #    far rect is untouched by placed metal and TEG_OPEN fires; the trunk
    #    shapes now emit per-rect connection metal, so the MST path is the
    #    remaining missing-metal vehicle) ──
    monkeypatch.setenv("BUDA_RECORD", trace)
    s1 = _session(flow + ["run_bundler STRICT", "generate_topologies"])
    pin = None
    for i, c in enumerate(s1.bundles[0].input.candidates):
        if c.type.startswith("MST_"):
            pin = i + 1
            break
    assert pin is not None, "no MST candidate found"
    for cmd in (f"select_topology 1 {pin}", "run_planner", "run_nuts"):
        _run(s1, cmd)
    monkeypatch.delenv("BUDA_RECORD")
    v1, out1 = _check(s1, "nuts")
    assert v1["by_kind"].get("TEG_OPEN", 0) >= 1, out1

    # ── the trace carries the OVER declaration verbatim ──
    assert _MULTIRECT_LINE_R2 in _trace_lines(trace)

    # ── resume session from the recorded trace: the audit must stay ARMED —
    #    detect_teg_open reads the FLOORPLAN's rects + teg_mode, which the
    #    setup replay re-declares; a resume that lost them would report
    #    Success here ──
    s2 = _session(_flat_setup_from_trace(trace, "run_nuts"))
    _run(s2, "load_pipeline")
    _run(s2, "run_nuts")
    v2, out2 = _check(s2, "nuts")
    assert v2["by_kind"].get("TEG_OPEN", 0) >= 1, (
        "TEG_OPEN went silent across the resume — the floorplan lost its "
        "rects/teg_mode re-declaration:\n" + out2)
    assert "OVER block 'r2'" in out2


# ── the HIER stage-resume whitelist (teg_multirect_status open 14 follow-up,
#    Codex P1 on PR #839): a hier flow's setup replays SESSION-STATE VERBS
#    ONLY (construction is in the checkpoint), so a verb missing from
#    tools/buda_interact.tcl's session_verbs is silently HELD.  `add_block`
#    is whitelisted, so a recorded `set_teg_mode over` before a keywordless
#    multi-rect add_block would replay the block WITHOUT the default — the
#    block reverts to THRU (the default is declaration-time-resolved) and
#    the candidate pool silently changes.  No tclsh runs in this harness
#    (the mid-tier btcl tests skip without one), so these tests parse the
#    REAL lists out of the Tcl source — they cannot drift, and the
#    membership test fails on the pre-fix whitelist. ──

_INTERACT_TCL = __import__("pathlib").Path(__file__).resolve().parents[2] \
    / "tools" / "buda_interact.tcl"


def _tcl_list(name):
    """The words of `set <name> { ... }` in buda_interact.tcl."""
    import re
    text = _INTERACT_TCL.read_text()
    m = re.search(r"set %s \{([^}]*)\}" % re.escape(name), text)
    assert m, f"set {name} {{...}} not found in {_INTERACT_TCL}"
    return m.group(1).split()


def test_hier_resume_session_verbs_include_set_teg_mode():
    verbs = _tcl_list("session_verbs")
    # Anchors: the list we parsed is the real whitelist.
    assert "set_feedthru" in verbs and "add_block" in verbs
    assert "set_teg_mode" in verbs, (
        "set_teg_mode is missing from buda_interact.tcl's session_verbs: a "
        "hier stage-resume replays add_block but HOLDS the recorded global "
        "TEG default, so a keywordless multi-rect block silently reverts "
        "to THRU with a different candidate pool")


def test_hier_resume_classification_replays_teg_default_in_order():
    # The hier classification rule, applied with the REAL Tcl lists: session
    # verbs replay in trace order, construction is held.  The default is
    # declaration-time-resolved, so ORDER matters too — set_teg_mode must
    # replay BEFORE the add_block it governed in the build.
    session_verbs = _tcl_list("session_verbs")
    construction = _tcl_list("construction_verbs")
    trace = ["open_bdb ck.bdb",
             "set_teg_mode over",
             "add_block B rect 200 0 300 100 rect 200 300 300 400",
             "add_inst i0 leaf top 0 0"]
    replayed = [ln for ln in trace if ln.split()[0] in session_verbs]
    held = [ln for ln in trace if ln.split()[0] in construction]
    assert "set_teg_mode over" in replayed
    assert held == ["add_inst i0 leaf top 0 0"]      # construction stays held
    assert replayed.index("set_teg_mode over") \
        < replayed.index("add_block B rect 200 0 300 100 rect 200 300 300 400")
