"""The DEF readers behind `flow/librelane/tier1a/render_arms.py`, on a DEF
small enough to check by hand.

The tool's whole claim is that what it draws and sums is what LibreLane
wrote: its top-level wire agrees with each arm's `route__wirelength` within
20 um and its F cell area with `design__instance__area__stdcell` within
0.02 % (module docstring).  Those need the run directories, which the tree
does not carry, so this pins the READERS on a synthetic DEF instead -- the
`*` coordinate rule, a via statement adding no wire, a `SPECIALNETS` strap
NOT counted, the ROW extent as the core, a component in every placement
state -- and the cell-kind rule the placement colours follow.  A reader that
drifts on any of these would still produce a plausible picture, which is
the failure this exists to catch.
"""
import importlib.util
import pathlib
import textwrap

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "render_arms", _ROOT / "flow" / "librelane" / "tier1a" / "render_arms.py")
ra = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ra)

DEF = textwrap.dedent("""\
    VERSION 5.8 ;
    DESIGN t ;
    UNITS DISTANCE MICRONS 1000 ;
    DIEAREA ( 0 0 ) ( 100000 80000 ) ;
    ROW ROW_0 unithd 5520 10880 N DO 100 BY 1 STEP 460 0 ;
    ROW ROW_1 unithd 5520 13600 FS DO 100 BY 1 STEP 460 0 ;
    COMPONENTS 4 ;
        - a sky130_fd_sc_hd__inv_2 + PLACED ( 10000 10880 ) N ;
        - b sky130_fd_sc_hd__dfxtp_1 + FIXED ( 20000 13600 ) FS ;
        - c sky130_fd_sc_hd__decap_4 + PLACED ( 30000 10880 ) N ;
        - u pe_cell ;
    END COMPONENTS
    SPECIALNETS 1 ;
        - VPWR + ROUTED met4 1600 ( 50000 0 ) ( * 80000 ) ;
    END SPECIALNETS
    NETS 2 ;
        - n1 ( a Y ) ( b D )
          + ROUTED met1 ( 10000 11000 ) ( 15000 * )
          NEW met2 ( 15000 11000 ) ( * 14000 ) ( 20000 * )
          NEW met1 ( 15000 11000 ) M1M2_PR ;
        - n2 ( b Q )
          + ROUTED met3 ( 0 5000 ) ( 100000 * ) ;
    END NETS
    END DESIGN
    """)


def test_parse_def_reads_die_core_and_placed_components(tmp_path):
    p = tmp_path / "t.def"
    p.write_text(DEF)
    die, comps, core = ra.parse_def(str(p))
    assert die == [0.0, 0.0, 100.0, 80.0]
    # the unplaced `u` has no location and is not a component here
    assert [(n, c, x, y, o) for n, c, x, y, o in comps] == [
        ("a", "sky130_fd_sc_hd__inv_2", 10.0, 10.88, "N"),
        ("b", "sky130_fd_sc_hd__dfxtp_1", 20.0, 13.6, "FS"),
        ("c", "sky130_fd_sc_hd__decap_4", 30.0, 10.88, "N")]
    # core = ROW extent: x 5.52..5.52+100*0.46, y 10.88..13.6+one 2.72 site
    assert core == [5.52, 10.88, 51.52, 16.32]


def test_routes_apply_the_star_rule_skip_vias_and_ignore_specialnets(tmp_path):
    p = tmp_path / "t.def"
    p.write_text(DEF)
    segs = ra.routes(str(p))
    assert segs["met1"] == [((10.0, 11.0), (15.0, 11.0))]           # the via statement adds none
    assert segs["met2"] == [((15.0, 11.0), (15.0, 14.0)), ((15.0, 14.0), (20.0, 14.0))]
    assert segs["met3"] == [((0.0, 5.0), (100.0, 5.0))]
    assert segs["met4"] == [] and segs["met5"] == []                # the VPWR strap is not signal wire
    L = ra.length(segs)
    assert (L["met1"], L["met2"], L["met3"]) == (5.0, 8.0, 100.0)
    # a block placed at (7, 9) shifts every wire with it
    shifted = ra.routes(str(p), dx=7.0, dy=9.0)
    assert shifted["met3"] == [((7.0, 14.0), (107.0, 14.0))]
    assert ra.length(shifted) == L


def test_cell_kinds_and_the_no_logic_filter():
    assert ra.kind("sky130_fd_sc_hd__dfxtp_1") == "ff"
    assert ra.kind("sky130_fd_sc_hd__dlxtp_1") == "ff"
    assert ra.kind("sky130_fd_sc_hd__clkbuf_16") == "buf"
    assert ra.kind("sky130_fd_sc_hd__dlygate4sd3_1") == "buf"
    assert ra.kind("sky130_fd_sc_hd__inv_2") == "comb"
    assert ra.kind("sky130_fd_sc_hd__a21oi_1") == "comb"
    for c in ("sky130_fd_sc_hd__fill_2", "sky130_fd_sc_hd__decap_8",
              "sky130_fd_sc_hd__tapvpwrvgnd_1", "sky130_fd_sc_hd__diode_2"):
        assert ra.SKIP.search(c), c
    assert not ra.SKIP.search("sky130_fd_sc_hd__nand2_1")
    assert ra.TAP.search("sky130_fd_sc_hd__tapvpwrvgnd_1") and not ra.TAP.search("sky130_fd_sc_hd__decap_8")
