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
Centralized group management for def_viz tools.

A group is a named, colored tree node whose members can be any mix of:
  - 'inst'  — instance name
  - 'net'   — net name
  - 'group' — id of another group (sub-group / child)

GroupTree maintains the full forest, enforces tree invariant (no cycles,
each group has at most one parent), and handles JSON persistence (version 2).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Optional

_PALETTE = [
    '#E6194B', '#3CB44B', '#4363D8', '#F58231', '#911EB4',
    '#42D4F4', '#F032E6', '#9A6324', '#469990', '#000075',
]


def lighten_color(hex_color: str, factor: float = 0.55) -> str:
    """Mix hex_color toward white by factor (0 = original, 1 = white)."""
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return '#{:02x}{:02x}{:02x}'.format(
        int(r + (255 - r) * factor),
        int(g + (255 - g) * factor),
        int(b + (255 - b) * factor),
    )


@dataclass
class GroupMember:
    kind: str   # 'inst' | 'net' | 'group'
    ref:  str   # inst_name, net_name, or group_id


@dataclass
class Group:
    id:      str
    name:    str
    color:   str
    members: list = field(default_factory=list)   # list[GroupMember]


class GroupTree:
    """Forest of Groups with arbitrary inst/net/group members."""

    def __init__(self):
        self._groups:    dict[str, Group]         = {}
        self._parent:    dict[str, Optional[str]] = {}   # gid → parent gid or None
        self._color_idx: int                      = 0

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def new_group(self, name: str, color: str = None,
                  parent_id: str = None) -> Group:
        gid = uuid.uuid4().hex[:8]
        if color is None:
            color = _PALETTE[self._color_idx % len(_PALETTE)]
            self._color_idx += 1
        g = Group(id=gid, name=name, color=color)
        self._groups[gid] = g
        self._parent[gid] = None
        if parent_id and parent_id in self._groups:
            self._groups[parent_id].members.append(GroupMember('group', gid))
            self._parent[gid] = parent_id
        return g

    def delete_group(self, gid: str):
        g = self._groups.get(gid)
        if not g:
            return
        # Detach from parent
        pid = self._parent.get(gid)
        if pid and pid in self._groups:
            self._groups[pid].members = [
                m for m in self._groups[pid].members
                if not (m.kind == 'group' and m.ref == gid)
            ]
        # Recursively delete child groups
        for m in list(g.members):
            if m.kind == 'group':
                self._delete_subtree(m.ref)
        self._groups.pop(gid, None)
        self._parent.pop(gid, None)

    def _delete_subtree(self, gid: str):
        g = self._groups.get(gid)
        if not g:
            return
        for m in list(g.members):
            if m.kind == 'group':
                self._delete_subtree(m.ref)
        self._groups.pop(gid, None)
        self._parent.pop(gid, None)

    def rename_group(self, gid: str, name: str):
        if gid in self._groups:
            self._groups[gid].name = name

    def recolor_group(self, gid: str, color: str):
        if gid in self._groups:
            self._groups[gid].color = color

    # ── Membership ────────────────────────────────────────────────────────────

    def add_member(self, gid: str, kind: str, ref: str):
        g = self._groups.get(gid)
        if g is None:
            return
        if any(m.kind == kind and m.ref == ref for m in g.members):
            return   # already present
        if kind == 'group':
            if ref not in self._groups:
                return
            if self._would_cycle(gid, ref):
                return
            # Re-parent: remove from current parent first
            old_pid = self._parent.get(ref)
            if old_pid and old_pid in self._groups:
                self._groups[old_pid].members = [
                    m for m in self._groups[old_pid].members
                    if not (m.kind == 'group' and m.ref == ref)
                ]
            self._parent[ref] = gid
        g.members.append(GroupMember(kind, ref))

    def remove_member(self, gid: str, kind: str, ref: str):
        g = self._groups.get(gid)
        if g is None:
            return
        g.members = [m for m in g.members if not (m.kind == kind and m.ref == ref)]
        if kind == 'group' and self._parent.get(ref) == gid:
            self._parent[ref] = None

    # ── Queries ───────────────────────────────────────────────────────────────

    def get(self, gid: str) -> Optional[Group]:
        return self._groups.get(gid)

    def roots(self) -> list[Group]:
        return [g for gid, g in self._groups.items()
                if self._parent.get(gid) is None]

    def parent_of(self, gid: str) -> Optional[str]:
        return self._parent.get(gid)

    def children(self, gid: str) -> list[Group]:
        """Direct child Group objects of gid."""
        g = self._groups.get(gid)
        if not g:
            return []
        return [self._groups[m.ref] for m in g.members
                if m.kind == 'group' and m.ref in self._groups]

    def all_insts(self, gid: str, _seen: set = None) -> set[str]:
        """All inst refs in this group and all sub-groups (transitive)."""
        if _seen is None:
            _seen = set()
        if gid in _seen:
            return set()
        _seen.add(gid)
        g = self._groups.get(gid)
        if not g:
            return set()
        result = {m.ref for m in g.members if m.kind == 'inst'}
        for m in g.members:
            if m.kind == 'group':
                result |= self.all_insts(m.ref, _seen)
        return result

    def all_nets(self, gid: str, _seen: set = None) -> set[str]:
        """All net refs in this group and all sub-groups (transitive)."""
        if _seen is None:
            _seen = set()
        if gid in _seen:
            return set()
        _seen.add(gid)
        g = self._groups.get(gid)
        if not g:
            return set()
        result = {m.ref for m in g.members if m.kind == 'net'}
        for m in g.members:
            if m.kind == 'group':
                result |= self.all_nets(m.ref, _seen)
        return result

    def groups_containing_inst(self, inst_name: str) -> list[Group]:
        return [g for g in self._groups.values()
                if any(m.kind == 'inst' and m.ref == inst_name for m in g.members)]

    def groups_containing_net(self, net_name: str) -> list[Group]:
        return [g for g in self._groups.values()
                if any(m.kind == 'net' and m.ref == net_name for m in g.members)]

    def walk(self, gid: str = None):
        """Pre-order DFS. Yields (Group, depth). From gid or all roots."""
        def _walk(g, depth):
            yield g, depth
            for m in g.members:
                if m.kind == 'group':
                    child = self._groups.get(m.ref)
                    if child:
                        yield from _walk(child, depth + 1)
        if gid:
            g = self._groups.get(gid)
            if g:
                yield from _walk(g, 0)
        else:
            for root_g in self.roots():
                yield from _walk(root_g, 0)

    def __len__(self):
        return len(self._groups)

    def __iter__(self):
        return iter(self._groups.values())

    def __bool__(self):
        return bool(self._groups)

    # ── Persistence ───────────────────────────────────────────────────────────

    @staticmethod
    def sidecar_path(def_path: str) -> str:
        return os.path.splitext(def_path)[0] + '_groups.json'

    def save(self, path: str):
        data = {
            'version': 2,
            'groups': [
                {
                    'id':      g.id,
                    'name':    g.name,
                    'color':   g.color,
                    'members': [{'kind': m.kind, 'ref': m.ref} for m in g.members],
                }
                for g in self._groups.values()
            ],
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def load(self, path: str):
        with open(path) as f:
            data = json.load(f)
        if data.get('version', 1) < 2:
            raise ValueError(
                f'Group file version {data.get("version")} is not supported; '
                'please delete the .groups.json file and recreate your groups.')
        self._groups = {}
        self._parent = {}
        for gd in data.get('groups', []):
            g = Group(
                id=gd['id'], name=gd['name'], color=gd['color'],
                members=[GroupMember(m['kind'], m['ref'])
                         for m in gd.get('members', [])],
            )
            self._groups[g.id] = g
            self._parent[g.id] = None
        # Rebuild parent map from member references
        for g in self._groups.values():
            for m in g.members:
                if m.kind == 'group' and m.ref in self._groups:
                    self._parent[m.ref] = g.id
        # Advance color index past colors already in use
        used = {g.color for g in self._groups.values()}
        self._color_idx = sum(1 for c in _PALETTE if c in used)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _would_cycle(self, parent_gid: str, child_gid: str) -> bool:
        """True if making child_gid a child of parent_gid would create a cycle."""
        cur = parent_gid
        seen: set[str] = set()
        while cur is not None:
            if cur == child_gid:
                return True
            if cur in seen:
                break
            seen.add(cur)
            cur = self._parent.get(cur)
        return False
