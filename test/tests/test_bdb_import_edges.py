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

Every case here was found by review of the DEF+Verilog merge (#650, #654),
and they share a shape: a classification that LOOKS right on the designs
already tested and is wrong just outside them.  Twice over for the
library-cell filter, whose rule went cell-name → placement → LEF CLASS,
each step correct on the case that prompted it and wrong on the next one.
"""
import sqlite3
import textwrap

import buda


def _v(tmp_path, text, name="t.v"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    db = buda.BDB(str(tmp_path / "x.bdb"))
    db.import_verilog(str(p))
    return db


def _wiring(db):
    """{(instance, pin): net} — the connectivity the reader actually built."""
    nets = {n.id: n.name for n in db.all_nets()}
    comps = {c.id: c.name for c in db.all_components()}
    return {(comps.get(p.comp_id), p.pin_name): nets.get(p.net_id)
            for p in db.all_pins()}


def _bus_props(path):
    """{net name: (bus_name, bit_index)} straight from the table.

    Read over SQL because `net_props` has no Python binding — the columns
    have been in the schema for this and nothing filled them in."""
    con = sqlite3.connect(str(path))
    try:
        return {n: (b, i) for n, b, i in con.execute(
            "SELECT net.name, net_props.bus_name, net_props.bit_index "
            "FROM net_props JOIN net ON net.id = net_props.net_id")}
    finally:
        con.close()


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


def test_a_macro_the_lef_calls_a_block_joins_the_hierarchy(tmp_path):
    """An instance of a module the netlist does not define is a library cell,
    and dropping those is what stops a million gates becoming a million rows.
    But the test used to be "the instance name is not backslash-escaped", and
    a hard macro is normally instantiated with an ordinary name — so
    `fakeram45_256x16 u_mem (...)` read as a standard cell.

    The LEF states the answer outright — `CLASS BLOCK` vs `CLASS CORE` — so
    it is asked, and the instance name is not consulted at all."""
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


_GATE_LEF = _MACRO_LEF.replace("END LIBRARY", "") + """\
MACRO BUFX2
  CLASS CORE ;
  SIZE 1 BY 2 ;
END BUFX2
MACRO DFFR_X1
  CLASS CORE ;
  SIZE 3 BY 2 ;
END DFFR_X1
END LIBRARY
"""

_GATE_DEF = """\
VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 500000 500000 ) ;
COMPONENTS 5 ;
  - core/u_mem fakeram45_256x16 + PLACED ( 10000 20000 ) N ;
  - core/b0 BUFX2 + PLACED ( 1000 1000 ) N ;
  - core/b1 BUFX2 + PLACED ( 3000 1000 ) N ;
  - core/b2 BUFX2 + PLACED ( 5000 1000 ) N ;
  - core/f0 DFFR_X1 + PLACED ( 7000 1000 ) N ;
END COMPONENTS
END DESIGN
"""

_GATE_V = """\
module core (a);
  input a;
  fakeram45_256x16 u_mem (.A(a));
  BUFX2 b0 (.A(a), .Z(w0));
  BUFX2 b1 (.A(w0), .Z(w1));
  BUFX2 b2 (.A(w1), .Z(w2));
  DFFR_X1 f0 (.D(w2), .Q(q));
endmodule

module top ();
  core core ();
endmodule
"""


def test_a_gate_level_merge_keeps_the_macro_and_drops_the_standard_cells(
        tmp_path):
    """The other half of the same question, and the one that makes the answer
    "ask the LEF" rather than "ask the placement" (Codex P1 on #654).

    A DEF for a gate-level design places EVERY standard cell — that is what a
    DEF is — so "the placement already has an instance by this name" is true
    of every buffer and flop in the design.  Under that rule the filter
    admitted all of them: measured 8 of 8 std cells elaborated into component,
    pin and net rows, which is precisely the explosion the filter exists to
    prevent.  CLASS separates them, and the same DEF that defeats the
    placement rule is the fixture here."""
    for n, t in (("a.lef", _GATE_LEF), ("a.def", _GATE_DEF), ("a.v", _GATE_V)):
        (tmp_path / n).write_text(t)
    db = buda.BDB(str(tmp_path / "x.bdb"))
    db.import_def_lef(str(tmp_path / "a.def"), str(tmp_path / "a.lef"))
    st = db.import_verilog(str(tmp_path / "a.v"))

    # The macro joined the hierarchy…
    hier = sorted(c.name for c in db.all_components() if c.depth > 0)
    assert hier == ["core/u_mem"], hier
    # …and every CLASS CORE instance was filtered, counted and named.
    assert st.skipped_library_cells == 4                     # 3 BUFX2 + 1 DFF
    assert sorted(st.skipped_cells) == ["BUFX2", "DFFR_X1"]
    assert st.skipped_kinds == 2


def test_the_truncated_kind_list_says_it_is_truncated(tmp_path):
    """The list caps at eight distinct kinds, and its own entries are unique
    by construction — so `len(list) >= len(set(list))` is true whether or not
    anything was dropped, and the ellipsis it gated never appeared (Codex P2
    on #654).  A library of nine kinds must not present eight as the whole
    story."""
    body = "\n".join(f"  CELL{i} u{i} (.A(a));" for i in range(10))
    (tmp_path / "t.v").write_text(f"module top ();\n{body}\nendmodule\n")
    db = buda.BDB(str(tmp_path / "x.bdb"))
    st = db.import_verilog(str(tmp_path / "t.v"))
    assert st.skipped_library_cells == 10
    assert st.skipped_kinds == 10           # the TRUE distinct count
    assert len(st.skipped_cells) == 8       # …of which the list shows eight


def test_an_import_with_no_lef_still_filters_standard_cells(tmp_path):
    """No LEF to ask, so the legacy heuristic stands unchanged — an
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


# ── vector port maps (opens_interchange item 2) ────────────────────────────

_SUB = """\
    module sub (a0, a1, z);
      input a0;
      input a1;
      output z;
    endmodule
"""


def test_a_bit_select_names_the_bit_not_the_bus(tmp_path):
    """`.a0(w[0])` used to have its selector cut off (`e.resize(e.find('['))`),
    so it named net `w`.

    Filed as a width collapse — a 4-bit bus arriving 1 bit wide — and that is
    the smaller half.  The larger half is that it SHORTS the bus: `w[0]` and
    `w[1]` are different nets, both landed on `w`, and the reader therefore
    reported two pins joined that the netlist keeps apart.  No diagnostic,
    and every later stage treats the result as the design."""
    db = _v(tmp_path, _SUB + """\
        module top ();
          wire [1:0] w;
          sub u0 (.a0(w[0]), .a1(w[1]), .z(q));
          sub u1 (.a0(w[1]), .a1(w[0]), .z(r));
        endmodule
        """)
    assert sorted(n.name for n in db.all_nets()) == ["q", "r", "w[0]", "w[1]"]
    w = _wiring(db)
    assert w[("u0", "a0")] == "w[0]" and w[("u0", "a1")] == "w[1]"
    # …and crucially the two are NOT the same net, in either instance.
    assert w[("u1", "a0")] == "w[1]" and w[("u1", "a1")] == "w[0]"


def test_the_bus_bits_are_classified_in_net_props(tmp_path):
    """`net_props.bus_name` / `bit_index` have been in the schema for exactly
    this and nothing wrote them (`bdb_edit_bus.py` says so in its docstring).
    One reading of `<base>[<k>]`, shared by both importers, so a DEF net and
    the Verilog net it merges with cannot be classified differently."""
    db = _v(tmp_path, _SUB + """\
        module top ();
          wire [1:0] w;
          sub u0 (.a0(w[0]), .a1(w[1]), .z(scalar));
        endmodule
        """)
    del db
    props = _bus_props(tmp_path / "x.bdb")
    assert props["w[0]"] == ("w", 0)
    assert props["w[1]"] == ("w", 1)
    # A net that is not a bus bit is left unclassified rather than given a
    # bus of one — "no bus" and "a 1-bit bus" are different claims.
    assert props["scalar"] == (None, None)


def test_a_bit_select_resolves_through_a_port(tmp_path):
    """The reason the selector is re-applied AFTER the ctx lookup rather than
    kept in the lookup key.

    The parent connects the whole bus to a port (`.p(w)`), and the child
    selects bits of that port (`p[0]`).  Resolving base-then-select maps
    `p[0]` onto `w[0]` — the same net the parent's own `.a0(w[0])` names.
    Keying the lookup on the literal `p[0]` would miss the ctx entry (`p`)
    and invent a fresh local net, i.e. an open across the boundary."""
    db = _v(tmp_path, _SUB + """\
        module mid (p);
          input p;
          sub k0 (.a0(p[0]), .a1(p[1]), .z(t));
        endmodule

        module top ();
          wire [1:0] w;
          mid m0 (.p(w));
          sub u0 (.a0(w[0]), .a1(w[1]), .z(q));
        endmodule
        """)
    w = _wiring(db)
    assert w[("m0/k0", "a0")] == "w[0]", "the child's bit did not reach the bus"
    assert w[("m0/k0", "a1")] == "w[1]"
    # Same net object as the top-level instance's — that is the whole point.
    assert w[("u0", "a0")] == w[("m0/k0", "a0")]


def test_an_escaped_identifier_keeps_its_brackets_as_a_name(tmp_path):
    """The trap in reading `[` as a select: a Verilog ESCAPED identifier runs
    from `\\` to whitespace and takes its brackets with it, so `\\w[0]` is an
    identifier NAMED `w[0]` — not bit 0 of a bus `w`.

    For that spelling the two readings happen to converge (both give the net
    name `w[0]`), and converging is what makes the merge work at all, since a
    DEF writes a bus bit as `\\w\\[0\\]`.  They part company on a name the
    select parser cannot read: `\\w[1][0]`, which synthesis writes for an
    element of a 2-D array.  Parsed as a select its index is `1][0`, which is
    not a literal — so the connection is dropped as unresolvable and the pin
    silently loses its net.  As a name it is simply a name."""
    db = _v(tmp_path, _SUB + """\
        module top ();
          wire \\w[0] , \\w[1][0] ;
          sub u0 (.a0(\\w[0] ), .a1(\\w[1][0] ), .z(q));
        endmodule
        """)
    w = _wiring(db)
    assert w[("u0", "a0")] == "w[0]"
    assert w[("u0", "a1")] == "w[1][0]", "the 2-D element lost its net"


def test_a_part_select_connects_every_bit_it_names(tmp_path):
    """A part-select on a port BUDA models as ONE pin.  Expanding it to a pin
    per named bit is what keeps the design connected: the alternative that
    reads naturally — keep the base name `w` — would leave this pin on a net
    no bit-select ever touches, so a design mixing the two shapes would come
    apart at exactly the bus the fix is about.

    The other alternative, a net literally called `w[3:0]`, would invent a
    name the netlist never declared."""
    db = _v(tmp_path, """\
        module sink (s);
          input s;
        endmodule
        """ + _SUB + """\
        module top ();
          wire [3:0] w;
          sub  u0 (.a0(w[0]), .a1(w[1]), .z(q));
          sink u1 (.s(w[3:0]));
        endmodule
        """)
    nets = {n.id: n.name for n in db.all_nets()}
    assert {"w[0]", "w[1]", "w[2]", "w[3]"} <= set(nets.values())
    assert "w[3:0]" not in set(nets.values()), \
        "invented a net the netlist never declared"

    # One pin ROW per named bit — the pin key is (net, comp, pin), so a port
    # touching four nets is representable without renaming the pin.  Counted
    # off the pin table rather than through a (instance, pin) dict, which
    # would collapse the very rows this is about.
    comps = {c.id: c.name for c in db.all_components()}
    on_s = sorted(nets[p.net_id] for p in db.all_pins()
                  if comps.get(p.comp_id) == "u1" and p.pin_name == "s")
    assert on_s == ["w[0]", "w[1]", "w[2]", "w[3]"], on_s
    # …so the part-select and the bit-selects meet on the same nets.
    assert _wiring(db)[("u0", "a0")] in on_s
