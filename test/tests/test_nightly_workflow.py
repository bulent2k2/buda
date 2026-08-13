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

"""Guards on `.github/workflows/nightly-qor.yml`.

Two properties of that workflow are load-bearing and were BOTH broken at some
point without anything noticing, because a workflow is only exercised by
running it — at 03:17 UTC, once a night, against `main`:

  * the compare's verdict must reach the JOB LOG.  It used to pipe
    `tee compare.txt` INTO `$GITHUB_STEP_SUMMARY`, so a red run's log held
    nothing but `::error::QoR regression` — the one output needed to diagnose
    the failure was the one output not in it.
  * every status the compare step can emit must be handled by the baseline
    steps.  Adding a status and forgetting the promote gate does not fail
    anything; it silently stops the baseline from ever advancing again.

Deliberately parsed as TEXT rather than with PyYAML: this suite's dependency
list is the CI gate's, PyYAML is not on it, and a skipped guard is the failure
mode the workflow's own skip-check exists to prevent.
"""

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nightly-qor.yml"

# The statuses the promote/save steps deliberately do NOT act on.  A regressed
# sweep must not become the baseline — that is the whole promote-on-clean
# design (docs/internal/ci.md).
NOT_PROMOTED = {"regressed"}


@pytest.fixture(scope="module")
def text():
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    return WORKFLOW.read_text()


def test_compare_output_reaches_the_job_log(text):
    """`tee compare.txt` must not be redirected into the step summary."""
    offenders = [ln.strip() for ln in text.splitlines()
                 if re.search(r"tee\s+compare\.txt\s*>>", ln)]
    assert not offenders, (
        "the compare's output is redirected away from the job log — a red "
        f"nightly would print only the ::error:: line: {offenders}")
    assert re.search(r"\|\s*tee compare\.txt\b(?!\s*>)", text), \
        "the compare no longer tees its output to stdout at all"
    assert "cat compare.txt >>" in text, \
        "the compare's output no longer reaches $GITHUB_STEP_SUMMARY"


def test_every_emitted_status_is_handled(text):
    """No status may be emitted that the baseline steps silently ignore."""
    emitted = set(re.findall(r'status=(\w+)"?\s*>>\s*"\$GITHUB_OUTPUT"', text))
    assert emitted, "no `status=` writes found — has the compare step moved?"

    handled = set()
    for cond in re.findall(r"^\s*if:\s*(.*outputs\.status.*)$", text, re.M):
        handled |= set(re.findall(r'"(\w+)"', cond))
        handled |= set(re.findall(r"'(\w+)'", cond))

    assert emitted - handled == NOT_PROMOTED, (
        f"statuses emitted but not handled by a baseline step: "
        f"{sorted(emitted - handled - NOT_PROMOTED)}; "
        f"expected exactly {sorted(NOT_PROMOTED)} to be left unpromoted")
    assert not handled - emitted - {"status"}, \
        f"baseline steps gate on statuses nothing emits: {sorted(handled - emitted)}"


def _input_block(text, name):
    """The lines of one workflow_dispatch input, by indentation.

    Line-scanned rather than regexed: the obvious pattern for "a key, then its
    indented body" is `(?:\\s+.*\\n)*?`, whose two quantifiers overlap and
    backtrack catastrophically when the match FAILS.  Caught by mutating the
    default to `true` and watching this file hang instead of fail — a guard
    that hangs is worse than no guard, since a timeout reads as infrastructure
    rather than as the property being broken.
    """
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == f"{name}:":
            indent = len(ln) - len(ln.lstrip())
            body = []
            for nxt in lines[i + 1:]:
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                body.append(nxt.strip())
            return body
    return None


def test_the_accept_path_is_dispatch_only_and_off_by_default(text):
    """`promote_baseline` must be an opt-in input, never a schedule default."""
    block = _input_block(text, "promote_baseline")
    assert block is not None, "the accept path input is gone"
    assert "default: false" in block, \
        f"promote_baseline no longer defaults to false: {block}"
    assert "type: boolean" in block, "promote_baseline is no longer a boolean"
    # Read through the environment, so the flag cannot be spelled into the
    # script body by expression interpolation.
    assert re.search(r"PROMOTE_BASELINE:\s*\$\{\{\s*inputs\.promote_baseline\s*\}\}", text), \
        "promote_baseline is no longer passed via the environment"
    assert '"$PROMOTE_BASELINE" = "true"' in text, \
        "the accept path no longer tests the flag exactly"


def test_the_accept_path_cannot_bank_an_errored_sweep(text):
    """The err-row rejection must stay AHEAD of the compare that can force."""
    assert "qor_corpus.py --check" in text, "the errored-sweep rejection is gone"
    assert "qor_corpus.py --compare" in text, "the compare is gone"
    reject = text.index("qor_corpus.py --check")
    compare = text.index("qor_corpus.py --compare")
    assert reject < compare, (
        "the errored-sweep rejection moved after the compare — a forced "
        "promotion could then bank a sweep with err rows")
