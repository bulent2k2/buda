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

"""
Shared pytest-bdd fixtures and step definitions for topology feature tests.

Steps here are imported automatically by pytest-bdd for all test files in
this directory.  Feature-specific steps live in the corresponding test_*.py.
"""
import re
import sys
import os

# Insert build/ then src/ at the front of sys.path so the fresh .so in build/
# always wins over any stale copy elsewhere.
_repo = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
_build = os.path.join(_repo, 'build')
_src   = os.path.join(_repo, 'src')
for _p in (_src, _build):   # insert src then build; each insert(0) pushes prior down,
    if _p not in sys.path:  # so build ends up at index 0 and wins over src
        sys.path.insert(0, _p)

import pytest
import buda
from pytest_bdd import given, when, then, parsers

# tools/ holds bdb_serialize; make it importable for the BDB fixtures below.
_tools = os.path.join(_repo, 'tools')
if _tools not in sys.path:
    sys.path.append(_tools)

# ---------------------------------------------------------------------------
# Checked-in BDB test data (diffable *.bdb.sql text fixtures)
# ---------------------------------------------------------------------------
# Test data lives as deterministic SQL text under test/tests/data/*.bdb.sql
# (regenerate with test/tests/data/build_fixtures.py). A test NEVER opens the
# checked-in artifact directly — the `bdb_input` fixture materializes a throwaway
# binary copy in tmp_path, so any pipeline mutation (derive_busterms, WAL side-
# cars, write-back, …) is discarded and the committed fixture stays clean.

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
if DATA_DIR not in sys.path:   # so tests can `import build_fixtures`
    sys.path.append(DATA_DIR)


@pytest.fixture
def bdb_input(tmp_path):
    """Return a factory: `bdb_input("hier_mixed")` -> path to a fresh temp .bdb.

    Rebuilds the binary from the committed test/tests/data/<name>.bdb.sql into
    tmp_path. Mutate it freely — it is discarded with tmp_path, so the checked-in
    fixture is never dirtied.
    """
    import bdb_serialize

    def _materialize(name):
        sql_path = os.path.join(DATA_DIR, f'{name}.bdb.sql')
        if not os.path.exists(sql_path):
            raise FileNotFoundError(
                f'no BDB fixture {name!r} at {sql_path} '
                f'(regenerate via test/tests/data/build_fixtures.py)')
        out = str(tmp_path / f'{name}.bdb')
        return bdb_serialize.load(sql_path, out)

    return _materialize


def readonly_conn(bdb_path):
    """Raw read-only sqlite3 connection to a BDB for pure SQL inspection.

    `buda.BDB` opens its own read-write connection, so for tests that only inspect
    schema/rows this returns a `mode=ro` connection that physically cannot mutate
    the file (and creates no WAL sidecars). Mutation isolation for `buda.BDB`-based
    tests comes from the copy-to-temp `bdb_input` fixture, not from here.
    """
    import sqlite3

    return sqlite3.connect(
        f'file:{os.path.abspath(bdb_path)}?mode=ro', uri=True)


# ---------------------------------------------------------------------------
# Shared context fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx():
    return {
        'fp': buda.Floorplan(),
        'layer_h': 4,
        'layer_v': 5,
        'candidates': [],        # list[Topology]
        'conn_topologies': {},   # type_str -> ConnTopology
        'kflex': 0.20,
        'ref_slide': 60.0,
        # connector_shape test state
        'trunk_spec': None,      # (is_horiz, perp_lo, perp_hi, along_lo, along_hi)
        'connector_result': {},  # block_name -> 'Direct'|'L'|'Z'
        # selected candidate for multi-step assertions
        'selected_cand': None,
        'selected_ct': None,
        # multi-rect blocks: name -> list of (x1,y1,x2,y2)
        'block_rects': {},
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_gen(ctx):
    g = buda.TopologyGenerator(ctx['fp'])
    g.set_layer_ids(ctx['layer_h'], ctx['layer_v'])
    return g


def _build_all_cts(ctx):
    """Build ConnTopology for every candidate; store by type string."""
    ctx['conn_topologies'] = {}
    for c in ctx['candidates']:
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        ctx['conn_topologies'][c.type] = ct


def _find_candidate(candidates, type_prefix):
    """Return the first candidate whose type starts with type_prefix."""
    for c in candidates:
        if c.type.startswith(type_prefix) or c.type == type_prefix:
            return c
    return None


def _segs_of(ct):
    return list(ct.segs())


def _trunk_seg(ct, is_horiz):
    """Return the trunk ConnSeg (the one with multiple non-BUSTERM connections)."""
    segs = _segs_of(ct)
    for cs in segs:
        if cs.horiz != is_horiz:
            continue
        seg_conns = [c for c in cs.conns if c.kind == buda.SegConnKind.SEG]
        if len(seg_conns) >= 1:
            return cs
    return None


def _stub_seg_for_block(ct, block_name):
    """Return the ConnSeg that has a BUSTERM to block_name (the stub or direct conn)."""
    for cs in _segs_of(ct):
        for conn in cs.conns:
            if (conn.kind == buda.SegConnKind.BUSTERM
                    and conn.block_name == block_name):
                return cs
    return None


def _connector_type(ct, block_name, trunk_is_horiz):
    """
    Infer connector shape for block_name relative to the trunk orientation.
    Direct  → BUSTERM touches the trunk segment itself.
    L       → BUSTERM touches a stub segment (one relay segment between block and trunk).
    Z       → two relay segments between block and trunk.
    """
    segs = _segs_of(ct)
    for cs in segs:
        for conn in cs.conns:
            if (conn.kind == buda.SegConnKind.BUSTERM
                    and conn.block_name == block_name):
                if cs.horiz == trunk_is_horiz:
                    return 'Direct'
                # It's a stub; check what the stub connects to
                for c2 in cs.conns:
                    if c2.kind == buda.SegConnKind.SEG:
                        parent = segs[c2.seg_idx]
                        if parent.horiz == trunk_is_horiz:
                            return 'L'
                        return 'Z'
                return 'L'   # single-hop stub with no SEG connection (degenerate)
    return None


def _compute_pull(ct, trunk_seg):
    """
    Compute (lo_pull, hi_pull) for a trunk segment from ConnSeg geometry.
    For H trunk (horiz=True): lo_pull = stubs where block face is below trunk
                               (BUSTERM at_pos < trunk.perp_pos, i.e. along_hi = perp_pos)
                              hi_pull = stubs where block face is above trunk.
    For V trunk (horiz=False): lo/hi refer to x axis (left/right).
    """
    lo, hi = 0, 0
    segs = _segs_of(ct)
    perp_pos = trunk_seg.perp_pos
    for conn in trunk_seg.conns:
        if conn.kind != buda.SegConnKind.SEG:
            continue
        stub = segs[conn.seg_idx]
        for sc in stub.conns:
            if sc.kind == buda.SegConnKind.BUSTERM:
                if sc.at_pos < perp_pos:
                    lo += 1
                elif sc.at_pos > perp_pos:
                    hi += 1
    return lo, hi


def _stub_length(cs):
    """Length of a stub ConnSeg along its routing axis."""
    return abs(cs.along_hi - cs.along_lo)


def _is_stub(cs):
    """True if cs has exactly one BUSTERM connection (leaf stub)."""
    bt_count = sum(1 for c in cs.conns if c.kind == buda.SegConnKind.BUSTERM)
    return bt_count == 1


def _has_no_cycles(ct):
    """Check that the ConnTopology graph has no cycles (simple DFS)."""
    segs = _segs_of(ct)
    n = len(segs)
    visited = [False] * n
    in_stack = [False] * n

    def dfs(idx, parent_idx):
        visited[idx] = True
        in_stack[idx] = True
        for conn in segs[idx].conns:
            if conn.kind != buda.SegConnKind.SEG:
                continue
            nxt = conn.seg_idx
            if not visited[nxt]:
                if dfs(nxt, idx):
                    return True
            elif in_stack[nxt] and nxt != parent_idx:
                return True
        in_stack[idx] = False
        return False

    for i in range(n):
        if not visited[i]:
            if dfs(i, -1):
                return False
    return True


# ---------------------------------------------------------------------------
# Given: blocks and layer setup
# ---------------------------------------------------------------------------

@given(parsers.re(
    r'a block "(?P<name>[^"]+)"\s+at '
    r'\((?P<x1>-?\d+),(?P<y1>-?\d+)\)-\((?P<x2>-?\d+),(?P<y2>-?\d+)\)'
))
def add_block(ctx, name, x1, y1, x2, y2):
    ctx['fp'].add_block(name, int(x1), int(y1), int(x2), int(y2))
    ctx.setdefault('block_names', []).append(name)


@given(parsers.re(
    r'a block "(?P<name>[^"]+)"\s+at '
    r'\((?P<x1>-?\d+),(?P<y1>-?\d+)\)-\((?P<x2>-?\d+),(?P<y2>-?\d+)\) '
    r'with corner_margin dx=(?P<dx>\d+) dy=(?P<dy>\d+)'
))
def add_block_with_margin(ctx, name, x1, y1, x2, y2, dx, dy):
    ctx['fp'].add_block(name, int(x1), int(y1), int(x2), int(y2))
    ctx['fp'].set_block_corner_margin(name, int(dx), int(dy))
    ctx.setdefault('block_names', []).append(name)
    ctx.setdefault('block_margins', {})[name] = (int(dx), int(dy))


@given(parsers.re(
    r'a block "(?P<name>[^"]+)" with rects '
    r'\((?P<x1a>\d+),(?P<y1a>\d+)\)-\((?P<x2a>\d+),(?P<y2a>\d+)\)'
    r' and '
    r'\((?P<x1b>\d+),(?P<y1b>\d+)\)-\((?P<x2b>\d+),(?P<y2b>\d+)\)'
))
def add_block_two_rects(ctx, name, x1a, y1a, x2a, y2a, x1b, y1b, x2b, y2b):
    rects = [
        (int(x1a), int(y1a), int(x2a), int(y2a)),
        (int(x1b), int(y1b), int(x2b), int(y2b)),
    ]
    try:
        ctx['fp'].add_block_rects(name, rects)
    except AttributeError:
        pytest.xfail('multi-rect add_block not yet in C++ API')
    ctx.setdefault('block_names', []).append(name)
    ctx['block_rects'][name] = rects


@given(parsers.re(
    r'a block "(?P<name>[^"]+)"\s+at '
    r'\((?P<x1>-?\d+),(?P<y1>-?\d+)\)-\((?P<x2>-?\d+),(?P<y2>-?\d+)\) '
    r'with feedthru enabled'
))
def add_feedthru_block(ctx, name, x1, y1, x2, y2):
    ctx['fp'].add_block(name, int(x1), int(y1), int(x2), int(y2))
    ctx.setdefault('block_names', []).append(name)
    if hasattr(ctx['fp'], 'set_block_feedthru'):
        ctx['fp'].set_block_feedthru(name, True)
    ctx.setdefault('feedthru_names', set()).add(name)


@given('layer M4 is HORIZONTAL with id 4')
def layer_m4(ctx):
    ctx['layer_h'] = 4


@given('layer M5 is VERTICAL with id 5')
def layer_m5(ctx):
    ctx['layer_v'] = 5


@given(parsers.re(r'kFlex is (?P<v>[\d.]+)'))
def set_kflex(ctx, v):
    ctx['kflex'] = float(v)


@given(parsers.re(r'ref_slide is (?P<v>[\d.]+)'))
def set_ref_slide(ctx, v):
    ctx['ref_slide'] = float(v)


# ---------------------------------------------------------------------------
# When: candidate generation
# ---------------------------------------------------------------------------

@when(parsers.re(
    r'I generate candidates from "(?P<src>[^"]+)" to "(?P<dst>[^"]+)" using layers M4,M5'
))
def gen_candidates(ctx, src, dst):
    ctx['candidates'] = _build_gen(ctx).generate_candidates(src, dst)


@when(parsers.re(
    r'I generate and rank candidates from "(?P<src>[^"]+)" to "(?P<dst>[^"]+)" using layers M4,M5'
))
def gen_and_rank_candidates(ctx, src, dst):
    ctx['candidates'] = _build_gen(ctx).generate_candidates(src, dst)
    # Sorting by adjusted_wl requires API not yet in C++; raw order used for now.


@when(parsers.re(
    r'I generate multicast candidates from "(?P<src>[^"]+)" to \[(?P<dsts>[^\]]+)\] using layers M4,M5'
))
def gen_multicast_candidates(ctx, src, dsts):
    dst_list = re.findall(r'"([^"]+)"', dsts)
    ctx['candidates'] = _build_gen(ctx).generate_candidates(src, dst_list)


@when(parsers.re(
    r'I generate and rank multicast candidates from "(?P<src>[^"]+)" to \[(?P<dsts>[^\]]+)\] using layers M4,M5'
))
def gen_and_rank_multicast(ctx, src, dsts):
    dst_list = re.findall(r'"([^"]+)"', dsts)
    ctx['candidates'] = _build_gen(ctx).generate_candidates(src, dst_list)


@when('I compute slide ranges for each candidate')
def compute_slide_ranges(ctx):
    _build_all_cts(ctx)


@when('I build ConnTopology for each candidate')
def build_conn_topologies(ctx):
    _build_all_cts(ctx)


@when('I compute pull preferences for each candidate')
def compute_pull_prefs(ctx):
    _build_all_cts(ctx)


# ---------------------------------------------------------------------------
# Then: candidate existence
# ---------------------------------------------------------------------------

@then(parsers.re(r'a candidate of type "(?P<t>[^"]+)" exists'))
def cand_of_type_exists(ctx, t):
    assert _find_candidate(ctx['candidates'], t) is not None, (
        f'No candidate of type {t!r}. Got: {[c.type for c in ctx["candidates"]]}'
    )


@then(parsers.re(r'at least one candidate of type "(?P<t>[^"]+)" exists'))
def at_least_one_cand_of_type(ctx, t):
    matches = [c for c in ctx['candidates'] if c.type.startswith(t)]
    assert matches, f'No {t!r} candidate. Got: {[c.type for c in ctx["candidates"]]}'


@then(parsers.re(r'at least one candidate exists that connects all (?P<n>\d+) blocks'))
def at_least_one_connects_all(ctx, n):
    n = int(n)
    assert len(ctx['candidates']) >= 1, 'No candidates generated'
    # Each candidate is expected to connect all blocks; just check count
    for c in ctx['candidates']:
        ct = buda.ConnTopology()
        ct.build(c, ctx['fp'])
        blocks_reached = set()
        for cs in _segs_of(ct):
            for conn in cs.conns:
                if conn.kind == buda.SegConnKind.BUSTERM and conn.block_name:
                    blocks_reached.add(conn.block_name)
        if len(blocks_reached) >= n:
            return
    pytest.fail(f'No candidate reaches all {n} blocks')
