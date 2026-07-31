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

"""qor_table's serial-timing sidecar (`sec1` column).

A parallel snapshot's `sec` is contention-inflated, so the table carries the
last known FULL `-j 1` run's per-flow wall-clocks as a stamped `sec1` column,
persisted in qor_serial_times.json.  These tests cover the sidecar round-trip
and the render wiring without running any real flow."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import qor_table as qt  # noqa: E402


def _row(flow, sec, clean=True):
    m = dict(flow=flow, bund=1, busS=2, netS=3, busWL=10, netWL=20, sec=sec)
    m.update(dict(ovl=0, unpl=0, viol=0) if clean else dict(ovl=1, unpl=0, viol=0))
    return m


def test_sidecar_round_trip(tmp_path):
    p = str(tmp_path / "serial.json")
    rows = [_row("flow/a.buda", 1.5), _row("flow/b.buda", 2.5),
            {"flow": "flow/broken.buda", "err": "boom"}]     # err rows excluded
    qt.save_serial_times(rows, "2026-07-31 (main @ abc1234)", path=p)
    data = qt.load_serial_times(path=p)
    assert data["stamp"] == "2026-07-31 (main @ abc1234)"
    assert data["times"] == {"flow/a.buda": 1.5, "flow/b.buda": 2.5}


def test_load_missing_or_malformed_is_none(tmp_path):
    assert qt.load_serial_times(path=str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text('{"stamp": "x"}')                # no 'times' dict
    assert qt.load_serial_times(path=str(bad)) is None


def test_load_rejects_missing_or_blank_stamp(tmp_path):
    # A hand-edited/conflict-resolved sidecar with times but no usable stamp
    # must load as None (the 'none recorded yet' fallback), not KeyError in
    # render() after the lengthy sweep (Codex #545).
    for content in ('{"times": {"flow/a.buda": 1.0}}',
                    '{"stamp": "  ", "times": {"flow/a.buda": 1.0}}',
                    '{"stamp": 7, "times": {"flow/a.buda": 1.0}}'):
        p = tmp_path / "s.json"
        p.write_text(content)
        assert qt.load_serial_times(path=str(p)) is None, content
    # And the render of such a load is the documented fallback, crash-free.
    md = qt.render([_row("flow/a.buda", 1.0)], "stamp",
                   qt.load_serial_times(path=str(tmp_path / "s.json")))
    assert "none recorded yet" in md


def test_render_shows_stamped_sec1_column():
    rows = [_row("flow/a.buda", 9.0), _row("flow/new.buda", 3.0),
            _row("flow/d.buda", 8.0, clean=False)]
    serial = {"stamp": "2026-07-31 (main @ abc1234)",
              "times": {"flow/a.buda": 1.5, "flow/d.buda": 7.0}}
    md = qt.render(rows, "2026-08-01 (main @ def5678)", serial)
    assert "sec1" in md                             # column present, both tables
    assert "captured 2026-07-31 (main @ abc1234)" in md   # the stamp
    assert "1.5" in md and "7.0" in md              # known serial times shown
    # A flow with no recorded serial time (added since the -j 1 run) shows '-'.
    new_line = next(ln for ln in md.splitlines() if ln.startswith("new"))
    assert new_line.rstrip().endswith("-")


def test_render_without_sidecar_says_none_recorded():
    md = qt.render([_row("flow/a.buda", 1.0)], "stamp", None)
    assert "none recorded yet" in md
