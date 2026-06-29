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

"""`run_planner post_nuts top` must never fall back to a LOW layer.

Top-only mode reassigns stubs WITHIN the TOP layers, keeping them off the LOW
escape layers (which are often track-starved over cells).  Regression (Codex
review on PR #80): when a direction has fewer than two TOP layers but extra LOW
layers, the old `if len(tops) >= 2` guard left `layers_sorted` as the full
TOP+LOW list, so `lo_layer = layers_sorted[0]` became a LOW layer and short
stubs were moved onto it — the exact track-starved LOW placement top-only mode
exists to avoid.  The fix restricts to TOP layers unconditionally and lets the
`< 2 layers` guard no-op the direction instead.
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402


def _run_top_only_v():
    """Drive a tiny flow whose V direction has ONE TOP layer (M5) + one LOW (M3).

    H has two TOP layers (M4/M6).  `post_nuts top V` must no-op the V direction
    rather than spill V stubs onto the LOW M3.
    """
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = (
        "def_layer 3 M3 V LOW 10",
        "def_layer 4 M4 H TOP 10",
        "def_layer 5 M5 V TOP 10",
        "def_layer 6 M6 H TOP 10",
        # Vertically separated blocks → a V trunk; horizontally offset → H stubs.
        "add_block b1 0   0   40  20",
        "add_block b2 200 400 240 420",
        "add_bus v[8] b1.o b2.i",
        "run_bundler strict",
        "generate_topologies",
        "run_planner",
        "run_nuts",
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
        # short_thresh huge so EVERY V stub is classified "short" → would be
        # pushed to lo_layer; that lo_layer must not be the LOW M3.
        s.do_command("run_planner post_nuts top V 100000 200000")
    return s, buf.getvalue()


def test_post_nuts_top_does_not_spill_v_to_low():
    s, out = _run_top_only_v()

    # The V direction has only one TOP layer, so top-only mode no-ops it.
    assert "fewer than 2 TOP V layers" in out, (
        "post_nuts top V should report the no-op for a single-TOP-V stack:\n" + out
    )

    # And, decisively, no segment may sit on the LOW V layer M3 (id 3) — the
    # pre-fix bug moved short V stubs there.
    on_m3 = [seg for seg in s.nuts_result.segments if seg.layer == 3]
    assert not on_m3, (
        f"post_nuts top spilled {len(on_m3)} segment(s) onto the LOW layer M3"
    )
