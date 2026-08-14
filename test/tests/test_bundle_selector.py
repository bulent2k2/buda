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

"""The bundle selector, across every command that takes one.

`select_topology`, `dump_topologies` and `visualize_topologies` all accept the
same `<sel>`: a bare integer is a bundle ID, a bare non-numeric token is a
net-name prefix, and `id:` / `net:` force a reading.  They have not always
agreed — `dump_topologies 8` searched for a bus named "8" while
`select_topology 8` pinned bundle 8 — which made the id `check_design` prints
("Bundle 8: ...") the one thing a user could not look up.  These tests are the
agreement, so the three cannot drift apart again.
"""

import io
from contextlib import redirect_stdout


def _run(lines):
    from buda_cli import BudaSession
    s = BudaSession()
    for line in lines:
        s.do_command(line)
    return s

# --- the selector: `dump_topologies 8` must mean bundle 8 -------------------

def _two_bundle_session():
    """Two bundles with distinct net prefixes, so id and hint can disagree."""
    return _run([
        "def_layer 4 M4 H TOP 0.0",
        "def_layer 5 M5 V TOP 0.0",
        "add_block drv  0    0  400 200",
        "add_block r1   1000 0  1400 200",
        "add_block r2   1000 800 1400 1000",
        "add_bus a[2] drv r1",
        "add_bus b[2] drv r2",
        "run_bundler",
        "generate_topologies",
        "run_planner",
    ])


def _dump(session, arg):
    buf = io.StringIO()
    with redirect_stdout(buf):
        session.do_command(f"dump_topologies {arg}")
    return buf.getvalue()


def test_a_bare_integer_selects_the_bundle_id_not_a_net_prefix():
    """The number `check_design` prints ("Bundle 8: ...") must be typeable
    straight back.  It used to be read as a NET-NAME PREFIX here while
    `select_topology` read the same token as a bundle ID — one token, two
    meanings, in adjacent commands — so the id the audit named was the one
    thing you could not look up."""
    s = _two_bundle_session()
    ids = sorted(w.input.original_bundle.id for w in s.bundles)
    assert len(ids) >= 2, ids
    for bid in ids:
        out = _dump(s, str(bid))
        assert f"── bundle {bid} " in out, out
        # ...and ONLY that bundle.
        assert out.count("── bundle ") == 1, out


def test_a_net_prefix_still_selects_by_name():
    s = _two_bundle_session()
    out = _dump(s, "b_")
    assert "── bundle " in out
    for line in out.splitlines():
        if line.startswith("── bundle "):
            assert "(b_" in line, line


def test_net_forces_the_prefix_reading_for_a_numeric_name():
    """`net:2` must not become bundle 2 — the escape hatch for a bus whose
    name starts with a digit."""
    s = _two_bundle_session()
    out = _dump(s, "net:2")
    assert "── bundle " not in out
    assert "no bundle whose first net starts with '2'" in out


def test_an_unknown_id_says_which_ids_exist():
    """The old message ('no bundle whose first net name starts with 9999')
    described a search the user did not ask for.  Name the real ids instead."""
    s = _two_bundle_session()
    out = _dump(s, "9999")
    assert "9999" in out and "bundle ids present" in out, out


def test_an_id_selector_does_not_also_prefix_match(monkeypatch):
    """A resolved id must not fall through to the net-name test.

    The explorer kept both readings for one token, so `visualize_topologies 8`
    matched bundle 8 OR any bundle whose first net starts with '8' — and the
    scan takes the lowest-id match, so the promised ID-only meaning held only
    while no such bus existed (Codex P2 on #746).  Built here as the shape
    that exposes it: a bus literally named `8x`, on a session where bundle
    ids and that name collide.
    """
    s = _run([
        "def_layer 4 M4 H TOP 0.0",
        "def_layer 5 M5 V TOP 0.0",
        "add_block drv  0    0  400 200",
        "add_block r1   1000 0  1400 200",
        "add_block r2   1000 800 1400 1000",
        "add_bus 8x[2] drv r1",          # a bus whose NAME starts with a digit
        "add_bus q[2]  drv r2",
        "run_bundler",
        "generate_topologies",
        "run_planner",
    ])
    by_first_net = {}
    for w in s.bundles:
        names = w.input.original_bundle.get_net_names()
        by_first_net[names[0]] = w.input.original_bundle.id
    digit_bus_id = next(i for n, i in by_first_net.items() if n.startswith("8"))

    # The id reading: only that bundle, whatever the digit bus is called.
    for bid in sorted(by_first_net.values()):
        out = _dump(s, str(bid))
        assert out.count("── bundle ") == 1, out
        assert f"── bundle {bid} " in out, out

    # ...and the prefix reading is still reachable, naming the digit bus.
    out = _dump(s, "net:8")
    assert f"── bundle {digit_bus_id} " in out, out


def test_the_split_and_the_resolver_cannot_disagree():
    """`_split_bundle_selector` is the id-vs-prefix rule; the resolver must be
    built on it rather than re-deriving it, or the explorer and the pin can
    read the same token two ways."""
    from buda_cli import BudaSession
    s = BudaSession()
    for sel, kind, val in (("8", "id", 8), ("id:12", "id", 12),
                           ("bus_007", "prefix", "bus_007"),
                           ("net:8", "prefix", "8"),
                           ("hint:a", "prefix", "a")):
        assert s._split_bundle_selector(sel) == (kind, val), sel
    for bad in ("id:x", "net:", ""):
        assert s._split_bundle_selector(bad)[0] is None, bad
    # And the resolver agrees for the id forms without consulting any bundle.
    assert s._resolve_bundle_selector("8") == ([8], None)
    assert s._resolve_bundle_selector("id:12") == ([12], None)


# --- the explorer takes the same selector, and ONLY one reading of it ------
#
# Driven through `select_explorer_bundles` rather than the command: the command
# returns early under `no_viz` (BUDA-1903), so the decision this bug lived in
# is unreachable from any headless test through that door.

def _explore(session, hints, all_mode=False):
    from buda_cmds.verify_viz_cmds import select_explorer_bundles
    import buda_viz
    all_wrappers, _ = buda_viz.collect_candidate_bundles(session.bundles)
    buf = io.StringIO()
    with redirect_stdout(buf):
        wrappers, start = select_explorer_bundles(session, all_wrappers,
                                                  hints, all_mode)
    return [w.input.original_bundle.id for w in wrappers], start, buf.getvalue()


def _digit_bus_session():
    """A design where a bus NAME collides with another bundle's ID.

    Built in two passes because the collision cannot be written down in
    advance: bundle ids are assigned by the bundler, so pass 1 learns the id
    of the plain bus and pass 2 names the other bus after THOSE DIGITS.  Only
    then does `<id>` have two possible readings — bundle <id>, or the bus
    called "<id>x" — which is the whole shape of the bug.  A fixture that
    merely contains a digit-named bus does NOT reproduce it; mine did not, and
    the mutation sailed through until this was fixed.

    Returns (session, {"plain": id, "digit": id}) with the DIGIT bus declared
    first, so it is also the one a fall-through would open.
    """
    def build(first_bus):
        return _run([
            "def_layer 4 M4 H TOP 0.0",
            "def_layer 5 M5 V TOP 0.0",
            "add_block drv  0    0  400 200",
            "add_block r1   1000 0  1400 200",
            "add_block r2   1000 800 1400 1000",
            f"add_bus {first_bus}[2] drv r1",
            "add_bus q[2]  drv r2",
            "run_bundler",
            "generate_topologies",
            "run_planner",
        ])

    def ids_of(s):
        out = {}
        for w in s.bundles:
            names = w.input.original_bundle.get_net_names()
            out[names[0].split("_")[0]] = w.input.original_bundle.id
        return out

    probe = ids_of(build("zz"))
    plain_id = probe["q"]                       # the id we will select by
    s = build(f"{plain_id}x")                   # ...now also a bus NAME prefix
    got = ids_of(s)
    return s, {"plain": got["q"], "digit": got[f"{plain_id}x"]}


def test_the_explorer_opens_the_id_not_a_bus_whose_name_starts_with_it():
    """An id-form selector must not ALSO prefix-match.

    Keeping both readings for one token meant `visualize_topologies 8` matched
    bundle 8 OR any bundle whose first net starts with '8', and the scan takes
    the first match in wrapper order — so the promised ID-only meaning held
    only while no such bus existed (Codex P2 on #746).
    """
    s, ids = _digit_bus_session()
    digit_id, plain_id = ids["digit"], ids["plain"]
    assert digit_id != plain_id

    loaded, start, _ = _explore(s, [str(plain_id)])
    assert loaded[start] == plain_id, (loaded, start)

    # The prefix reading stays reachable, and names the digit bus.
    loaded, start, _ = _explore(s, [f"net:{plain_id}"])
    assert loaded[start] == digit_id, (loaded, start)


def test_all_mode_loads_only_the_selected_id():
    """`-all <id>` filters the loaded set, so the bug showed there as an extra
    bundle rather than as the wrong starting one."""
    s, ids = _digit_bus_session()
    loaded, _, _ = _explore(s, [str(ids["plain"])], all_mode=True)
    assert loaded == [ids["plain"]], loaded


def test_a_malformed_selector_is_reported_not_silently_ignored():
    s, _ = _digit_bus_session()
    _, _, out = _explore(s, ["id:x"])
    assert "ignoring selector" in out and "id:x" in out, out
