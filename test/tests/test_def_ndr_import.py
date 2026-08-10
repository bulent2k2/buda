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

"""DEF NONDEFAULTRULES -> def_ndr/set_ndr (opens_interchange.md item 7).

A translation job, not a lookup: DEF states ABSOLUTE per-layer widths in
DBU, BUDA's rule model states MULTIPLIERS of each layer's default.  Each
rule's clauses divide by the LEF defaults; only a rule whose layers agree
on the multiplier is expressible, and one that is not is skipped LOUD
(BUDA-1613) rather than approximated with a max the DEF never stated.
"""
import contextlib
import io

import pytest

import buda_cli

_LEF = """VERSION 5.8 ;
LAYER metal2
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.2 ;
  WIDTH 0.07 ;
  SPACING 0.12 ;
END metal2
LAYER metal3
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.2 ;
  WIDTH 0.07 ;
  SPACING 0.12 ;
END metal3
MACRO m
  CLASS BLOCK ;
  SIZE 10 BY 4 ;
  PIN a
    DIRECTION INPUT ;
    PORT
      LAYER metal2 ;
      RECT 4 1 6 3 ;
    END
  END a
END m
END LIBRARY
"""

# UNITS 2000, so metal2's default WIDTH 0.07um = 140 DBU and SPACING 0.12um
# = 240 DBU.  Rule `fat` doubles both on both layers (280/480); rule `mixed`
# states multipliers that DISAGREE between its layers (x2 vs x4).
_DEF = """VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 2000 ;
DIEAREA ( 0 0 ) ( 200000 200000 ) ;
NONDEFAULTRULES 2 ;
  - fat
    + LAYER metal2 WIDTH 280 SPACING 480
    + LAYER metal3 WIDTH 280 SPACING 480 ;
  - mixed
    + LAYER metal2 WIDTH 280
    + LAYER metal3 WIDTH 560 ;
END NONDEFAULTRULES
COMPONENTS 2 ;
  - i0 m + PLACED ( 1000 2000 ) N ;
  - i1 m + PLACED ( 50000 60000 ) N ;
END COMPONENTS
NETS 3 ;
  - clk ( i0 a ) + NONDEFAULTRULE fat ;
  - clk_late ( i1 a ) ;
  - plain ( i1 a ) + NONDEFAULTRULE mixed ;
END NETS
END DESIGN
"""


def _run(deff=_DEF, lef=_LEF, tmp_path=None):
    (tmp_path / "m.lef").write_text(lef)
    (tmp_path / "d.def").write_text(deff)
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(f"open_bdb {tmp_path / 'a.bdb'}")
        s.do_command(f"import_lef_tech {tmp_path / 'm.lef'}")
        s.do_command(f"import_def_lef {tmp_path / 'd.def'} "
                     f"{tmp_path / 'm.lef'}")
    return s, out.getvalue()


def test_an_agreeing_rule_translates_to_multipliers(tmp_path):
    s, out = _run(tmp_path=tmp_path)
    rule = s._ndr_rules["fat"]
    assert rule["width_x"] == pytest.approx(2.0)      # 280 dbu / 0.07um@2000
    assert rule["spacing_x"] == pytest.approx(2.0)    # 480 dbu / 0.12um@2000
    # layers restricted to the two the DEF named (metal2=2, metal3=3)
    assert rule["layers"] == [2, 3]


def test_the_net_scope_attaches_by_name(tmp_path):
    s, out = _run(tmp_path=tmp_path)
    assert s._ndr_scopes.get("clk") == "fat"
    # `plain` asked for `mixed`, which was NOT translated — stays default.
    assert "plain" not in s._ndr_scopes


def test_disagreeing_multipliers_skip_loud(tmp_path):
    _s, out = _run(tmp_path=tmp_path)
    assert "BUDA-1613" in out and "'mixed'" in out
    assert "disagree" in out
    # …and the net that asked for it is told, by name.
    assert "net 'plain' asks for rule 'mixed'" in out


def test_prefix_shadowing_is_reported(tmp_path):
    """set_ndr scopes are PREFIXES: attaching to net `clk` also governs
    `clk_late`.  The DEF stated a per-net rule, so the over-match is
    reported rather than silent."""
    _s, out = _run(tmp_path=tmp_path)
    assert "PREFIXES" in out and "clk_late" in out


def test_a_rule_on_an_undeclared_layer_skips_loud(tmp_path):
    deff = _DEF.replace("+ LAYER metal2 WIDTH 280 SPACING 480",
                        "+ LAYER metal9 WIDTH 280 SPACING 480")
    _s, out = _run(deff=deff, tmp_path=tmp_path)
    assert "BUDA-1613" in out and "metal9" in out


def test_an_unmodelled_clause_is_noted_not_fatal(tmp_path):
    deff = _DEF.replace("- fat\n",
                        "- fat\n    + HARDSPACING\n    + VIA via2x ;\n"
                        .replace(" ;\n", "\n"))
    s, out = _run(deff=deff, tmp_path=tmp_path)
    assert "fat" in s._ndr_rules                     # still translated
    assert "VIA" in out and "no BUDA model" in out


def test_partial_spacing_coverage_skips_loud(tmp_path):
    """SPACING on only ONE of a rule's two LAYER clauses: the multiplier
    model states one spacing for ALL the rule's layers, so emitting it
    would broaden the rule onto metal3, which the DEF left at its default
    (Codex P1 on #667).  Refused, not approximated."""
    deff = _DEF.replace("+ LAYER metal3 WIDTH 280 SPACING 480",
                        "+ LAYER metal3 WIDTH 280")
    s, out = _run(deff=deff, tmp_path=tmp_path)
    assert "fat" not in getattr(s, "_ndr_rules", {})
    assert "BUDA-1613" in out and "'fat'" in out
    assert "only 1 of 2 LAYER clauses" in out


def test_reimport_replaces_the_previous_imports_rules(tmp_path):
    """A second import_def_lef in one session replaces the design, so it
    replaces the previous import's translated rules too — it used to hit
    def_ndr's duplicate-rule hard error after the design tables were
    already replaced (Codex P2 on #667)."""
    s, _ = _run(tmp_path=tmp_path)
    assert "fat" in s._ndr_rules
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(f"import_def_lef {tmp_path / 'd.def'} "
                     f"{tmp_path / 'm.lef'}")
    # No SystemExit above, the rule is declared exactly once, the scope
    # re-attached, and the BDB holds one persisted copy.
    assert s._ndr_rules["fat"]["width_x"] == pytest.approx(2.0)
    assert s._ndr_scopes.get("clk") == "fat"
    assert [r.name for r in s.bdb.ndr_rules()] == ["fat"]


def test_reimport_drops_a_rule_the_new_def_lacks(tmp_path):
    """The replacement is a REPLACEMENT: a rule translated by import 1 that
    import 2's DEF does not state must vanish (session and BDB), not
    linger as if the new design declared it."""
    s, _ = _run(tmp_path=tmp_path)
    assert "fat" in s._ndr_rules
    bare = _DEF.replace("""NONDEFAULTRULES 2 ;
  - fat
    + LAYER metal2 WIDTH 280 SPACING 480
    + LAYER metal3 WIDTH 280 SPACING 480 ;
  - mixed
    + LAYER metal2 WIDTH 280
    + LAYER metal3 WIDTH 560 ;
END NONDEFAULTRULES
""", "").replace(" + NONDEFAULTRULE fat", "").replace(
        " + NONDEFAULTRULE mixed", "")
    (tmp_path / "bare.def").write_text(bare)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"import_def_lef {tmp_path / 'bare.def'} "
                     f"{tmp_path / 'm.lef'}")
    assert "fat" not in s._ndr_rules
    assert "clk" not in s._ndr_scopes
    assert s.bdb.ndr_rules() == []


def test_a_session_typed_rule_wins_over_the_defs(tmp_path):
    """The user typed `def_ndr fat …` before importing a DEF whose rule of
    the same name says something else: session-typed wins (the restore
    path's convention), the DEF's content is skipped LOUD instead of
    def_ndr's duplicate hard error killing the import."""
    (tmp_path / "m.lef").write_text(_LEF)
    (tmp_path / "d.def").write_text(_DEF)
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(f"open_bdb {tmp_path / 'a.bdb'}")
        s.do_command(f"import_lef_tech {tmp_path / 'm.lef'}")
        s.do_command("def_ndr fat width x3")
        s.do_command(f"import_def_lef {tmp_path / 'd.def'} "
                     f"{tmp_path / 'm.lef'}")
    assert s._ndr_rules["fat"]["width_x"] == pytest.approx(3.0)  # not 2.0
    assert "BUDA-1613" in out.getvalue()
    assert "session declaration wins" in out.getvalue()


def test_a_def_without_ndrs_declares_nothing(tmp_path):
    deff = _DEF.replace("""NONDEFAULTRULES 2 ;
  - fat
    + LAYER metal2 WIDTH 280 SPACING 480
    + LAYER metal3 WIDTH 280 SPACING 480 ;
  - mixed
    + LAYER metal2 WIDTH 280
    + LAYER metal3 WIDTH 560 ;
END NONDEFAULTRULES
""", "").replace(" + NONDEFAULTRULE fat", "").replace(
        " + NONDEFAULTRULE mixed", "")
    s, out = _run(deff=deff, tmp_path=tmp_path)
    assert not getattr(s, "_ndr_rules", {})
    assert "BUDA-1613" not in out
