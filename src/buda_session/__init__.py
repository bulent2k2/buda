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

"""BudaSession mixin package (the CLI helper-method split).

Each submodule owns one functional cluster of BudaSession's helper
methods as a mixin class; buda_cli composes them into BudaSession.
Method sets are disjoint by construction — the guard below makes a
collision a hard import error, so two mixins can never silently
shadow one another.
"""
from .persist import PersistMixin
from .hier import HierMixin
from .nutsflow import NutsFlowMixin
from .edit import EditMixin
from .reports import ReportsMixin
from .ripup import RipupMixin
from .rr_state import RRStateMixin

MIXINS = (PersistMixin, HierMixin, NutsFlowMixin, EditMixin,
          ReportsMixin, RipupMixin, RRStateMixin)

_owner = {}
for _m in MIXINS:
    for _name in vars(_m):
        if _name.startswith('__'):
            continue
        if _name in _owner:
            raise RuntimeError(f"duplicate mixin member: {_name!r} in "
                               f"{_m.__name__} and {_owner[_name].__name__}")
        _owner[_name] = _m
