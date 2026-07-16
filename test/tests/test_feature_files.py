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

"""Guard the Gherkin feature suite (test/tests/features/) against silent rot.

Every `.feature` file must (a) parse as valid Gherkin — so it stays bind-ready
and the narrative layer never drifts into unparseable prose — and (b) carry
exactly one status tag from the vocabulary in features/README.md, so a reader can
always tell landed from aspirational. See docs/internal/feature_coverage_plan.md.
"""
import glob
import os

import pytest
from pytest_bdd.parser import FeatureParser

_FEAT_DIR = os.path.join(os.path.dirname(__file__), "features")
_FEATURES = sorted(glob.glob(os.path.join(_FEAT_DIR, "*.feature")))

# The status tags declared in features/README.md.
_STATUS_TAGS = {"landed", "future", "doc", "orphaned"}


def _rel(path):
    return os.path.relpath(path, _FEAT_DIR)


def test_features_exist():
    assert _FEATURES, "no .feature files found — did the features dir move?"


@pytest.mark.parametrize("path", _FEATURES, ids=_rel)
def test_feature_parses_as_gherkin(path):
    """Every feature file is valid Gherkin (bind-ready, no wrapped steps / prose)."""
    FeatureParser(_FEAT_DIR, _rel(path)).parse()


@pytest.mark.parametrize("path", _FEATURES, ids=_rel)
def test_feature_carries_exactly_one_status_tag(path):
    """Every feature declares its status: @landed / @future / @doc / @orphaned."""
    feat = FeatureParser(_FEAT_DIR, _rel(path)).parse()
    status = feat.tags & _STATUS_TAGS
    assert status, (
        f"{_rel(path)} has no status tag; add one of "
        f"{sorted(_STATUS_TAGS)} (see features/README.md)")
    assert len(status) == 1, (
        f"{_rel(path)} has conflicting status tags {sorted(status)}; keep exactly one")
