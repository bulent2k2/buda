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

"""The protocol channel is not fd 1 (opens item 9, residual 3).

The one documented way to break the pipe protocol was a library writing
straight to file descriptor 1 — landing inside a frame and desynchronising
the channel.  "Nothing in BUDA does this today" was true, but it was a
promise about other people's code.  `claim_protocol_channel` replaces the
promise: fd 1 is duplicated to a private descriptor the protocol alone
writes, and fd 1 itself is repointed at stderr.

No tclsh needed: the test IS the client — it speaks the protocol over the
subprocess pipe, and the driver script patches a ROGUE command into the
registry that does exactly the forbidden thing.
"""
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_DRIVER = """
import os
import sys

sys.path.insert(0, {tools!r})
import buda_server
import buda_cmds


def _rogue(session, cmd, args, cmd_line):
    # The forbidden thing itself: a raw write to fd 1, bypassing
    # sys.stdout and std::cout — what a careless native library might do.
    os.write(1, b"RAW-JUNK-ON-FD1\\n")
    print("normal captured output")


buda_cmds.COMMANDS["rogue"] = _rogue
out = buda_server.claim_protocol_channel()
sys.exit(buda_server.Server(out=out).serve())
"""


def _talk(requests):
    p = subprocess.Popen([sys.executable, "-c",
                          _DRIVER.format(tools=str(_ROOT / "tools"))],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, encoding="utf-8")
    stdout, stderr = p.communicate("".join(r + "\n" for r in requests),
                                   timeout=120)
    return stdout, stderr


def _frames(stdout):
    """Parse the protocol stream strictly: any stray byte breaks it."""
    frames, i = [], 0
    while i < len(stdout):
        nl = stdout.index("\n", i)
        header = stdout[i:nl]
        status, n = header.split()
        n = int(n)
        payload = stdout[nl + 1:nl + 1 + n]
        assert len(payload) == n, f"truncated frame after {header!r}"
        frames.append((status, payload))
        i = nl + 1 + n
    return frames


def test_a_raw_fd1_write_cannot_corrupt_the_channel():
    stdout, stderr = _talk(["rogue", "__query blocks", "__exit"])
    # The protocol stream parses STRICTLY — junk inside it would break the
    # framing arithmetic — and the conversation stayed in sync: the query
    # after the rogue command got its own well-formed answer.
    frames = _frames(stdout)
    assert [s for s, _ in frames] == ["OK", "OK", "BYE"], frames
    assert "normal captured output" in frames[0][1]
    assert "RAW-JUNK-ON-FD1" not in stdout
    # ...and the junk is not swallowed either: it landed on stderr,
    # visible and outside every frame.
    assert "RAW-JUNK-ON-FD1" in stderr


def test_the_ordinary_conversation_is_unchanged():
    stdout, _stderr = _talk(["__commands", "__query blocks", "__exit"])
    frames = _frames(stdout)
    assert [s for s, _ in frames] == ["OK", "OK", "BYE"]
    assert "add_block" in frames[0][1]          # the registry came through
    assert frames[1][1] == "0"


def test_the_claim_precedes_the_engine_imports():
    """Run AS the server, the claim happens at module load, BEFORE `import
    buda`: the compiled extension and its native dependencies initialize at
    import, and module-init output written straight to fd 1 would already
    be queued in the pipe by the time main() ran — parsed by the client as
    the response header to its first request.  The property IS an ordering
    of module-level statements, so it is pinned as one."""
    src = (_ROOT / "tools" / "buda_server.py").read_text()
    claim = src.index("_PROTO_OUT = claim_protocol_channel()")
    engine = src.index("\nimport buda ")
    assert claim < engine, "the protocol channel is claimed after the " \
                           "engine imports — startup output can desync"
