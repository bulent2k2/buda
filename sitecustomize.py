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

"""Process-start defaults for local test runs.

Pytest can import plugins and worker hooks before test/tests/conftest.py runs.
If those hooks import matplotlib first, conftest is too late to keep the test
process headless.  Python imports sitecustomize during interpreter startup, so
set the backend environment default here without importing matplotlib.
"""
import os
import sys


def _looks_like_pytest_startup():
    argv0 = os.path.basename(sys.argv[0])
    orig_argv = getattr(sys, "orig_argv", ())
    via_module = (
        len(orig_argv) >= 3
        and os.path.basename(orig_argv[0]).startswith("python")
        and orig_argv[1:3] == ["-m", "pytest"]
    )
    return "pytest" in argv0 or via_module


if _looks_like_pytest_startup():
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault(
        "MPLCONFIGDIR",
        os.path.join(os.getcwd(), "log", "matplotlib"))
    os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)
