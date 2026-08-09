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
