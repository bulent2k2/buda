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

"""Marks `tools/` as a REGULAR package so the repo's own modules win the name.

This file carries no code.  It exists because `tools` is a plausible name for a
third-party distribution, and the repo does not own it on PyPI.

Without an `__init__.py`, `tools` is a NAMESPACE package, and CPython's import
scan treats the two kinds asymmetrically: walking `sys.path`, it returns
IMMEDIATELY on the first *regular* package it finds, while namespace portions
are merely accumulated and only assembled if the scan finishes without one.  So
a regular `tools` package anywhere in site-packages beats the repo's namespace
`tools` **even when the repo root comes first on `sys.path`** — precedence does
not help, and neither does PYTHONPATH.

That is not hypothetical: a `tools` distribution appeared transitively in CI's
environment and every `from tools import <module>` in the suite broke at once —
17 failures and 6 collection errors on `main`, all reading

    ImportError: cannot import name 'bdb2buda' from 'tools'
                 (…/site-packages/tools/__init__.py)

An empty `__init__.py` makes this a regular package too, at which point ordinary
`sys.path` precedence applies and the repo root (first under pytest) wins.

`test_tools_package_identity.py` pins the behaviour by constructing the
shadowing situation directly, so a regression is caught without waiting for a
dependency to reintroduce it.
"""
