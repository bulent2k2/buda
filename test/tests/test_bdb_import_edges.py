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

"""Netlist-reader edges the feature suite does not reach.

Both cases here were found by review of the DEF+Verilog merge (#650) and
both are about a classification that LOOKS right on the designs already
tested and is wrong just outside them.
"""
import textwrap

import buda


def _v(tmp_path, text, name="t.v"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    db = buda.BDB(str(tmp_path / "x.bdb"))
    db.import_verilog(str(p))
    return db


# ── ANSI port headers ──────────────────────────────────────────────────────

def test_each_direction_clause_in_an_ansi_header_is_parsed_separately(tmp_path):
    """`module top (input a, output b, inout c);` is ONE statement, so a
    clause that runs to the next `;`/`)` let the first `input` swallow the
    rest — every port in the module recorded INPUT, including the outputs.

    It reaches `cell_pin` directions, which is what instance pins are
    inferred from, so an output pin was indexed as an input."""
    db = _v(tmp_path, """\
        module leafcell (a, b);
          input a;
          output b;
        endmodule

        module top (input a, output b, inout c);
          leafcell u0 (.a(a), .b(b));
        endmodule
        """)
    dirs = {(cp.cell, cp.pin_name): cp.dir for cp in db.all_cell_pins()}
    assert dirs[("top", "a")] == "INPUT"
    assert dirs[("top", "b")] == "OUTPUT"
    assert dirs[("top", "c")] == "INOUT"
    # …and the non-ANSI form it always handled still works.
    assert dirs[("leafcell", "a")] == "INPUT"
    assert dirs[("leafcell", "b")] == "OUTPUT"


def test_a_multi_line_ansi_header_is_parsed_too(tmp_path):
    db = _v(tmp_path, """\
        module top (
            input  a,
            output b
        );
          sub u0 (.a(a), .b(b));
        endmodule
        """)
    dirs = {(cp.cell, cp.pin_name): cp.dir for cp in db.all_cell_pins()}
    assert dirs[("top", "a")] == "INPUT"
    assert dirs[("top", "b")] == "OUTPUT"


# ── leaf classification ────────────────────────────────────────────────────

_NESTED = """\
    module leafcell (a, b);
      input a;
      output b;
    endmodule

    module env (a, b);
      input a;
      output b;
      leafcell k0 (.a(a), .b(b));
    endmodule

    module top ();
      env e0 ();
    endmodule
    """


def test_a_module_with_instances_is_a_container_however_it_was_placed(tmp_path):
    """A DEF may place hierarchy ENVELOPES as well as their descendants, and
    an envelope has children whether or not anything placed it.  Keying
    leaf-ness on "was it placed" (rather than on what the module contains)
    left every placed envelope marked a leaf: its footprint would block low
    layers it does not occupy, and busterm derivation would resolve it as a
    PORT rather than a BLOCK (Codex P1 on #650)."""
    db = _v(tmp_path, _NESTED)
    by_name = {c.name: c for c in db.all_components()}
    assert not by_name["e0"].is_leaf          # has an instance inside
    # Placing it must not change the answer.
    db.set_comp_bbox("e0", 0, 0, 100, 100)
    db.import_verilog(str(tmp_path / "t.v"))
    by_name = {c.name: c for c in db.all_components()}
    assert not by_name["e0"].is_leaf, "a placed envelope is still a container"


def test_a_defined_empty_module_stays_a_container_when_only_the_netlist_says_so(
        tmp_path):
    """The landed rule (features/bdb_import.feature): declaring a module
    makes it a hierarchy level even with an empty body.  Only a row a DEF
    PLACED from a LEF footprint is reclassified as the physical macro it
    is."""
    db = _v(tmp_path, _NESTED)
    by_name = {c.name: c for c in db.all_components()}
    assert not by_name["e0/k0"].is_leaf


# ── library-cell filtering (opens_interchange item 1) ──────────────────────

_MACRO_LEF = """\
MACRO fakeram45_256x16
  CLASS BLOCK ;
  SIZE 100 BY 60 ;
  PIN A
    DIRECTION INPUT ;
    PORT
      LAYER metal1 ;
      RECT 1 1 2 2 ;
    END
  END A
END fakeram45_256x16
END LIBRARY
"""

_MACRO_DEF = """\
VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 500000 500000 ) ;
COMPONENTS 1 ;
  - core/u_mem fakeram45_256x16 + PLACED ( 10000 20000 ) N ;
END COMPONENTS
END DESIGN
"""

_MACRO_V = """\
module core (a);
  input a;
  fakeram45_256x16 u_mem (.A(a));
endmodule

module top ();
  core core ();
endmodule
"""


def _merged(tmp_path):
    for n, t in (("a.lef", _MACRO_LEF), ("a.def", _MACRO_DEF), ("a.v", _MACRO_V)):
        (tmp_path / n).write_text(t)
    db = buda.BDB(str(tmp_path / "x.bdb"))
    db.import_def_lef(str(tmp_path / "a.def"), str(tmp_path / "a.lef"))
    st = db.import_verilog(str(tmp_path / "a.v"))
    return db, st


def test_a_macro_the_def_placed_joins_the_hierarchy(tmp_path):
    """An instance of a module the netlist does not define is a library cell,
    and dropping those is what stops a million gates becoming a million rows.
    But the test used to be "the instance name is not backslash-escaped", and
    a hard macro is normally instantiated with an ordinary name — so
    `fakeram45_256x16 u_mem (...)` read as a standard cell.

    The DEF is the authority on what exists physically, so it is asked: a
    name the placement already contains is kept, whatever its spelling."""
    db, st = _merged(tmp_path)
    by_name = {c.name: c for c in db.all_components()}
    mem, core = by_name["core/u_mem"], by_name["core"]
    assert mem.depth == 1 and mem.parent_id == core.id, \
        "the macro was left orphaned at depth 0"
    assert st.skipped_library_cells == 0, st.skipped_cells


def test_an_orphaned_macro_takes_its_container_down_with_it(tmp_path):
    """Why the orphan matters, rather than being untidy: `core` has no other
    child, so with the macro unlinked it has NO placed descendant —
    `derive_container_bboxes` cannot size it, it gets no busterm, and the
    routing interface loses a level.  One dropped instance, a missing
    hierarchy level."""
    db, _st = _merged(tmp_path)
    n, unresolved = db.derive_container_bboxes(0.0)
    assert n == 1 and unresolved == [], (n, unresolved)
    core = {c.name: c for c in db.all_components()}["core"]
    # The macro's own extent — in LAYOUT UNITS, and at the default import
    # scale one layout unit is 1 µm, so the DEF's 10000/20000 DBU are 10/20.
    assert (core.x1, core.y1) == (10.0, 20.0)


def test_a_verilog_only_import_still_filters_standard_cells(tmp_path):
    """No placement to ask, so the legacy heuristic stands unchanged — an
    unescaped instance of an undefined module is a library cell.  This is the
    behaviour that keeps a gate-level netlist from becoming a million
    component rows, and the fix above must not weaken it."""
    (tmp_path / "t.v").write_text("""\
module top ();
  fakeram45_256x16 u_mem (.A(x), .Z(y));
  BUFX2 u_buf (.A(y), .Z(z));
endmodule
""")
    db = buda.BDB(str(tmp_path / "x.bdb"))
    st = db.import_verilog(str(tmp_path / "t.v"))
    assert [c.name for c in db.all_components()] == []
    assert st.skipped_library_cells == 2


def test_what_was_skipped_is_always_counted_and_named(tmp_path):
    """The half that is wrong in every flow: an instance that silently never
    existed is indistinguishable from a design that never had one.  The
    census names the cell KINDS, which is what tells you a macro went
    missing rather than a buffer."""
    (tmp_path / "t.v").write_text("""\
module top ();
  BUFX2 b0 (.A(a), .Z(w0));
  BUFX2 b1 (.A(w0), .Z(w1));
  DFFR_X1 f0 (.D(w1), .Q(q));
endmodule
""")
    db = buda.BDB(str(tmp_path / "x.bdb"))
    st = db.import_verilog(str(tmp_path / "t.v"))
    assert st.skipped_library_cells == 3          # instances
    assert sorted(st.skipped_cells) == ["BUFX2", "DFFR_X1"]   # distinct kinds


def test_an_escaped_def_name_matches_its_verilog_path(tmp_path):
    """What the removed `normalize_def_name` claimed to do, pinned where it
    can be checked.

    It stripped a backslash only before `[` or `]`, so `\\mem\\[0\\]/u1`
    normalized to `\\mem[0]/u1` — keeping the LEADING escape and therefore
    NOT matching the Verilog path `mem[0]`, which is exactly what it existed
    to match.  `def_io.cpp`'s `unescape` strips the escape wherever it
    appears, and the two sides agree."""
    (tmp_path / "a.def").write_text("""\
VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
COMPONENTS 1 ;
  - \\mem\\[0\\]/u1 m + PLACED ( 1000 2000 ) N ;
END COMPONENTS
END DESIGN
""")
    d = buda.read_def(str(tmp_path / "a.def"))
    assert d.components[0].name == "mem[0]/u1"

    (tmp_path / "t.v").write_text("""\
module inner ();
endmodule

module top ();
  inner \\mem[0] ();
endmodule
""")
    db = buda.BDB(str(tmp_path / "x.bdb"))
    db.import_verilog(str(tmp_path / "t.v"))
    assert [c.name for c in db.all_components()] == ["mem[0]"]
    # …so the DEF path and the elaborated path share their prefix, which is
    # what makes the merge line up at all.
    assert d.components[0].name.startswith("mem[0]/")
