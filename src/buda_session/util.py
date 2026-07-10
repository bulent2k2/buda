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

"""Module-level helpers shared by the BudaSession mixins.

Moved verbatim from buda_cli so the mixin modules (and buda_cmds) can
import them without a buda_cli import cycle; buda_cli re-exports them
for compatibility.
"""
import functools

# ripup_reroute tuning (greedy hill-climb over topology selections).
_RR_MAX_CANDIDATES_PER_BUNDLE = 8   # alternate candidates tried per contender / iter
_RR_DEFAULT_MAX_ITER = 10           # outer-loop cap when no arg given


def bottom_up_congruence_issues(comps, cell):
    """Verify every instance of `cell` is a pure translated copy of the
    first one — the precondition for bottom-up template planning, whose
    per-instance copies are translation-only (offset_topology).

    Checks, per instance: identity orientation 'N', equal outline
    dimensions, and an identical FULL SUBTREE — every descendant matched
    by path suffix on (cell type, orient, bbox relative to the instance
    origin). The geometric comparison matters because rotate_comp /
    flip_comp on a *hierarchical* block rewrite the children's absolute
    bboxes and deliberately keep orient='N' — the orient token alone
    cannot detect such an instance; the full-depth walk matters because
    a moved GRANDCHILD leaves outline + direct children matching, and
    the per-descendant orient matters because a rotated *square* leaf
    descendant leaves the geometry matching too.

    A pure function of the `all_components()` rows, shared by the CLI
    (`set_bottom_up` / `run_planner hier`) and the Floorplanner's cell
    settings so the two can never diverge on what "congruent" means.

    Returns a list of human-readable issue strings (empty = congruent).
    """
    insts, by_parent = [], {}
    for c in comps:
        if c.cell == cell:
            insts.append(c)
        by_parent.setdefault(c.parent_id, []).append(c)
    issues = [f"{c.name}: orientation {c.orient} (need 'N')"
              for c in insts if c.orient != "N"]
    if len(insts) < 2:
        return issues

    def dims(c):
        return (round(c.x2 - c.x1, 3), round(c.y2 - c.y1, 3))

    def shape(inst):
        # Full subtree, keyed by path suffix relative to the instance.
        out, stack, prefix = {}, [inst], inst.name + "/"
        while stack:
            node = stack.pop()
            for k in by_parent.get(node.id, []):
                rel = (k.name[len(prefix):]
                       if k.name.startswith(prefix) else k.name)
                out[rel] = (k.cell, k.orient,
                            round(k.x1 - inst.x1, 3),
                            round(k.y1 - inst.y1, 3),
                            round(k.x2 - inst.x1, 3),
                            round(k.y2 - inst.y1, 3))
                stack.append(k)
        return out

    ref = insts[0]
    ref_shape = shape(ref)
    for c in insts[1:]:
        if dims(c) != dims(ref):
            issues.append(f"{c.name}: outline {dims(c)} differs from "
                          f"{ref.name} {dims(ref)}")
            continue
        s = shape(c)
        if s != ref_shape:
            bad = (sorted(set(ref_shape) ^ set(s))
                   or sorted(k for k in ref_shape
                             if s.get(k) != ref_shape[k]))
            issues.append(f"{c.name}: subtree differs from "
                          f"{ref.name} (e.g. {', '.join(bad[:3])})")
    return issues


def _batched(method):
    """Run a BDB-persist method inside ONE transaction (see BudaSession._bdb_batch).

    Every add_* row insert autocommits by default, so a bulk persist (1000s of
    rows) pays one WAL fsync per statement — this collapses them to a single
    commit (measured: generate_hier_topologies persist 23s -> ~1.7s on mix).
    Nestable: the C++ depth counter makes composing persist helpers (e.g.
    _persist_nuts -> _persist_planner_output) issue only one real BEGIN/COMMIT.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._bdb_batch():
            return method(self, *args, **kwargs)
    return wrapper
