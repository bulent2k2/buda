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

"""Shared option-validation for the CLI command handlers.

Many commands read a FIXED vocabulary of optional keyword/enum tokens by a
membership test (`"signal_tracks" in args`, `args[0] == "hier"`, …) and used to
SILENTLY IGNORE anything they didn't recognize — so a typo like
`run_planner singnal_tracks` or a stray `generate_topologies foo` ran with the
feature the user thought they enabled quietly off.  `reject_unknown_options`
turns that into a hard error that stops the flow, exactly like `do_command`'s
unknown-COMMAND guard.  Validate BEFORE any state/prerequisite check so a
malformed command is rejected regardless of session state.
"""
import difflib
import sys


def reject_unknown_options(cmd, tokens, valid, *, also_accepted=()):
    """Stop the flow (non-zero exit) if any of `tokens` is not a known option.

    `cmd`           the command name, for the message.
    `tokens`        the argument tokens that are SUPPOSED to be options — the
                    caller removes required positionals / numeric values / free-
                    form names (block/net/file/…) first, so only genuine option
                    keywords remain.
    `valid`         the user-facing option vocabulary (ordered; printed verbatim).
    `also_accepted` tokens honored but not advertised (legacy no-ops); accepted
                    silently, kept out of the printed list and the suggestions.

    On an unknown token it prints the offending token(s), the valid options, and
    a close-match "Did you mean …?" hint, then `sys.exit(1)`.  `run_command`
    re-raises the SystemExit, so the script halts (matching the unknown-command
    behavior).  No-op when every token is recognized.
    """
    allowed = set(valid) | set(also_accepted)
    unknown = [t for t in tokens if t not in allowed]
    if not unknown:
        return
    hint = ""
    for u in unknown:
        m = difflib.get_close_matches(u, list(valid), n=1)
        if m:
            hint = f" Did you mean '{m[0]}'?"
            break
    plural = "s" if len(unknown) > 1 else ""
    valid_str = " ".join(valid) if valid else "(none)"
    print(f"Error: {cmd}: unknown option{plural} "
          f"{', '.join(repr(u) for u in unknown)}. "
          f"Valid options: {valid_str}.{hint}")
    sys.exit(1)


def looks_numeric(tok):
    """True if `tok` parses as an int or float — i.e. a numeric VALUE argument
    (an iteration count, threshold, pitch), not an option keyword.  Used to
    exclude numeric args before option validation."""
    try:
        float(tok)
        return True
    except (TypeError, ValueError):
        return False
