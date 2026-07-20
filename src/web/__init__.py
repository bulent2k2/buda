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

"""BUDA web backend — a thin FastAPI layer over the existing engines.

The web layer adds ONLY transport (`server.py`), command capture (`runner.py`),
and struct→JSON serialization (`serialize.py`).  It contributes no routing
logic: every route builds a `.buda` command string and drives the same
`BudaSession.do_command` path the CLI uses, then reads the routed result off the
pybind C++ structs.  See docs/internal/web_frontend.md.
"""
