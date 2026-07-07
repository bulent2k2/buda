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

"""Structural guards for the BudaSession mixin split (buda_session package).

The helper methods moved verbatim out of buda_cli.BudaSession into six mixin
classes; these tests keep the composition honest:

  * BudaSession's bases are exactly the package's MIXINS tuple, so a mixin
    cannot be silently dropped from the class;
  * mixin member sets are pairwise disjoint (also enforced at import time by
    the package's duplicate guard) AND disjoint from BudaSession's own body,
    so no definition shadows another through the MRO;
  * every mixin member resolves on BudaSession to that mixin's definition.
"""
import buda_cli  # noqa: E402
import buda_session


def _members(cls):
    return {n for n in vars(cls) if not n.startswith('__')}


def test_bases_are_the_mixin_tuple():
    assert buda_cli.BudaSession.__bases__ == buda_session.MIXINS


def test_mixin_members_pairwise_disjoint():
    owner = {}
    for m in buda_session.MIXINS:
        for name in _members(m):
            assert name not in owner, (
                f"{name!r} defined in both {owner[name].__name__} "
                f"and {m.__name__}")
            owner[name] = m
    assert owner, "mixins are empty?"


def test_core_does_not_shadow_mixins():
    core = _members(buda_cli.BudaSession)
    for m in buda_session.MIXINS:
        overlap = core & _members(m)
        assert not overlap, (
            f"BudaSession body shadows {m.__name__}: {sorted(overlap)}")


def test_every_mixin_member_resolves_through_the_class():
    for m in buda_session.MIXINS:
        for name in _members(m):
            # getattr on both sides so descriptors (staticmethod, ...)
            # unwrap the same way.
            assert getattr(buda_cli.BudaSession, name) is getattr(m, name), (
                f"{name!r} on BudaSession is not {m.__name__}'s definition")
