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

"""`set_teg_mode <thru|over>` — the global default TEG mode
(teg_multirect_status.md open 14).

`teg_mode` was per-block only: every multi-rect block of an all-OVER design
had to repeat the keyword, and there was no way to state the design's
convention once.  `set_teg_mode` sets the Floorplan-level default that a
multi-rect block declared WITHOUT an explicit per-block `teg_mode` keyword
takes; the per-block keyword always wins (most-specific-first, the
set_feedthru convention).  Resolution is at DECLARATION time — prospective
only, matching add_block's other declaration-time resolutions — so a block
declared before the command keeps the mode it was declared under.
"""
import contextlib
import io

import buda
import buda_cli

_BASE = ("def_layer 3 M3 V TOP 0", "def_layer 4 M4 H TOP 0")


def _run(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            for c in _BASE + cmds:
                s.do_command(c)
        except SystemExit as e:
            code = e.code
    return s, code, out.getvalue()


def _err(out):
    return next((l for l in out.splitlines() if l.startswith("Error:")), "")


_B = "add_block B rect 200 0 300 100 rect 200 300 300 400"


def test_default_without_the_command_is_thru():
    s, code, out = _run(_B)
    assert code is None, _err(out)
    assert s.fp.get_block_teg_mode("B") == buda.TegMode.THRU


def test_global_over_applies_to_keywordless_blocks():
    s, code, out = _run("set_teg_mode over", _B)
    assert code is None, _err(out)
    assert s.fp.get_block_teg_mode("B") == buda.TegMode.OVER


def test_per_block_keyword_wins_over_the_global():
    # Most-specific-first, like set_feedthru: an explicit per-block teg_mode
    # outranks the global default in either direction.
    s, code, out = _run(
        "set_teg_mode over",
        _B + " teg_mode thru",
        "add_block C rect 400 0 500 100 rect 400 300 500 400")
    assert code is None, _err(out)
    assert s.fp.get_block_teg_mode("B") == buda.TegMode.THRU   # override wins
    assert s.fp.get_block_teg_mode("C") == buda.TegMode.OVER   # global default


def test_prospective_only_a_prior_block_keeps_its_mode():
    # Declaration-time resolution: the default does not re-mode blocks that
    # were declared before it (matching add_block's other declaration-time
    # resolutions; documented in docs/script_reference/setup.md).
    s, code, out = _run(_B, "set_teg_mode over")
    assert code is None, _err(out)
    assert s.fp.get_block_teg_mode("B") == buda.TegMode.THRU


def test_set_teg_mode_back_to_thru():
    s, code, out = _run("set_teg_mode over", "set_teg_mode thru", _B)
    assert code is None, _err(out)
    assert s.fp.get_block_teg_mode("B") == buda.TegMode.THRU


def test_unknown_mode_is_a_hard_error():
    _s, code, out = _run("set_teg_mode sideways")
    assert code == 1
    msg = _err(out)
    assert "set_teg_mode" in msg and "sideways" in msg
    assert "thru" in msg and "over" in msg


def test_missing_argument_is_a_hard_error():
    _s, code, out = _run("set_teg_mode")
    assert code == 1
    assert "set_teg_mode" in _err(out)


def test_extra_argument_is_a_hard_error():
    _s, code, out = _run("set_teg_mode over please")
    assert code == 1
    assert "set_teg_mode" in _err(out)


def test_engine_default_is_declaration_time_too():
    # The same contract at the engine API: add_block_rects with no teg_mode
    # takes the floorplan's CURRENT default.
    fp = buda.Floorplan()
    fp.add_block_rects("early", [(0, 0, 100, 100), (200, 0, 300, 100)])
    fp.set_default_teg_mode(buda.TegMode.OVER)
    fp.add_block_rects("late", [(0, 200, 100, 300), (200, 200, 300, 300)])
    assert fp.get_block_teg_mode("early") == buda.TegMode.THRU
    assert fp.get_block_teg_mode("late") == buda.TegMode.OVER
    # An explicit mode still outranks the default.
    fp.add_block_rects("expl", [(0, 400, 100, 500), (200, 400, 300, 500)],
                       teg_mode=buda.TegMode.THRU)
    assert fp.get_block_teg_mode("expl") == buda.TegMode.THRU
