"""`flow/librelane/tier1a/tap_census.py` on synthetic DEFs: a row fragment
with a cell and no well tap is the LVS verdict it exists to predict, and the
DEF's UNITS must reach the ROW coordinates as they reach the components'
(Codex on #952: a 2000-DBU DEF read at 1000 matched no cell to any fragment
and passed vacuously)."""
import importlib.util
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("tap_census", _ROOT / "flow/librelane/tier1a/tap_census.py")
tc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tc)


def _def(units, tap):
    # one row of 100 sites from x = 0; a flop at 10 um, a tap at 20 um when asked
    s = units // 1000
    cells = f"    - ff sky130_fd_sc_hd__dfxtp_1 + PLACED ( {10000 * s} 0 ) N ;\n"
    if tap:
        cells += f"    - t sky130_fd_sc_hd__tapvpwrvgnd_1 + PLACED ( {20000 * s} 0 ) N ;\n"
    return textwrap.dedent(f"""\
        VERSION 5.8 ;
        DESIGN t ;
        UNITS DISTANCE MICRONS {units} ;
        DIEAREA ( 0 0 ) ( {100000 * s} {10000 * s} ) ;
        ROW ROW_0 unithd 0 0 N DO 100 BY 1 STEP {920 * s} 0 ;
        COMPONENTS {2 if tap else 1} ;
        """) + cells + "END COMPONENTS\nEND DESIGN\n"


@pytest.mark.parametrize("units", [1000, 2000])
def test_an_untapped_fragment_with_a_cell_is_reported_in_either_unit(tmp_path, units):
    p = tmp_path / "t.def"
    p.write_text(_def(units, tap=False))
    rows, cells, bad = tc.census(str(p))
    assert len(rows) == 1 and len(cells) == 1, "the flop lands in the one fragment"
    assert len(bad) == 1 and bad[0][1] == {"dfxtp": 1}
    p.write_text(_def(units, tap=True))
    rows, cells, bad = tc.census(str(p))
    assert len(cells) == 1 and bad == [], "the tap discharges it"
