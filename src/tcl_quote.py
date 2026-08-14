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

"""Quoting a value so Tcl reads it back as ONE word.

Standalone and dependency-free ON PURPOSE, for the same reason as
`slot_groups.py`: the translator (`tools/buda2tcl.py`) is not the only writer
of Tcl.  Every test that drives the front end writes a `source <path>` /
`buda::start -python <path>` preamble, and each had its own interpolation --
which is to say, no quoting at all.  On POSIX that is invisible, because the
paths a POSIX checkout produces contain no character Tcl acts on.  On Windows
BOTH of Tcl's word-parsing stages fire on an ordinary path:

    substitution   D:\\a\\buda\\buda\\tools\\buda.tcl  ->  D:uda\\x08uda<TAB>oolsuda.tcl
                   (`\\a` is BEL, `\\b` backspace, `\\t` tab -- the interpreter
                    consumed the separators before anything saw a filename)
    word splitting  C:/Program Files/Python/python.exe  ->  three words, and
                   `buda::start` reads `-python C:/Program` then chokes on the
                   rest

They are separate stages and need separate mitigations, which is why
`tcl_path` does two things and not one: respell the separators (kills the
escape reading, and Tcl accepts `/` on Windows), then quote (kills the
split).  The respelling happens only where the backslash IS a separator --
see `tcl_path`; on POSIX it is an ordinary filename character and the
escaping carries it instead.

`tcl_word` quotes a whitespace-free command TOKEN -- it is what the corpus
translation is built on, so its output is byte-load-bearing and its default
behaviour must not move.  `tcl_path` is the whitespace-tolerant form.
"""

import os

__all__ = ["tcl_word", "tcl_path"]


def tcl_word(tok, escape_ws=False):
    """Quote ONE token as a single Tcl word.

    Braces are the right default: inside them Tcl performs no substitution at
    all, so `bus[4]`, `$x`, `(SIGNAL` and `0.5)x4` all survive verbatim -- and
    those are not hypotheticals, they are what the corpus actually contains.
    A token carrying an unbalanced brace or a backslash cannot be brace-quoted
    (Tcl still counts braces in there), so it falls back to escaping every
    character Tcl would otherwise act on.

    `escape_ws` extends that fallback to whitespace.  It is off by default
    because the translator's tokens are whitespace-split already, so the
    question never arises there and the corpus must stay byte-identical; a
    PATH is the case where it does arise (`C:\\Program Files\\...` reaches the
    fallback via its backslashes and would then split on the space).
    """
    if tok and tok.count("{") == tok.count("}") and "\\" not in tok:
        # A balanced-brace token is brace-safe... unless a closing brace comes
        # first (`}x{` is balanced by count but ends the word early).
        depth = 0
        ok = True
        for ch in tok:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    ok = False
                    break
        if ok:
            return "{" + tok + "}"
    out = []
    for ch in tok:
        # `;` belongs in this set: Tcl ends a command at a bare semicolon, so
        # an unescaped one would not corrupt the argument -- it would START A
        # SECOND COMMAND out of the rest of the line (Codex P2 on #686).
        if ch in '\\$[]{}";':
            out.append("\\")
        elif escape_ws and ch.isspace():
            out.append("\\")
        out.append(ch)
    return "".join(out) or '""'


def tcl_path(path):
    """Quote a filesystem path as one Tcl word, on any platform.

    Forward-slashing is not cosmetic and not Windows-only politeness: it is
    what stops the interpreter from reading `\\a`/`\\b`/`\\t` in a native path
    as escapes.  Tcl takes `/` on Windows, so the respelled path opens the
    same file.  Quoting is the second, independent fix -- a path with a space
    is otherwise several words however its separators are spelled.

    The respelling is guarded on the PLATFORM because it is only sound where
    the backslash IS a separator.  On POSIX a backslash is an ordinary
    filename character, so a checkout under `back\\slash` would be rewritten
    to a different -- and almost certainly nonexistent -- path (Codex P2 on
    #734).  There it stays literal and rides `tcl_word`'s escaping instead,
    which already handles it: escaped, the interpreter hands the backslash
    back rather than reading the next character as an escape.  `os.sep`
    rather than `sys.platform` because that is the actual question, and it
    answers Cygwin correctly too (POSIX separators on a Windows kernel).
    """
    text = str(path)
    if os.sep == "\\":
        text = text.replace("\\", "/")
    return tcl_word(text, escape_ws=True)
