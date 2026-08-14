# `pip install .` — the prerequisite, and only the prerequisite

BUDA is installable with the standard Python tooling:

```bash
pip install .                 # from a checkout
pip install -e .              # editable: the Python layer stays live
```

That is the whole scope of this document. There is **no wheel matrix, no
cibuildwheel, no release cadence and nothing published** — those are separate
decisions, and this is the thing they would all have to be built on. Doing it
now, while it is cheap, is also what flushes out the real work: everything in
§3 was found by installing rather than by reasoning about installing.

`bin/bb` and `bin/activate` remain the developer path and are untouched. The
CMake rules pip drives are guarded by `BUDA_WHEEL`, which is `OFF` for every
build that does not come through pip, and `bin/bb` is a 1-second no-op after
this change.

---

## 1. The problem the layout solves

BUDA's import contract has never been a package. It is a set of directories:

| stated in | contract |
|---|---|
| `bin/activate`, `bin/buda`, `bin/fp` | `PYTHONPATH=build:tools` |
| `pytest.ini` | `pythonpath = build src` |
| the repo root, under pytest | `from tools import bdb2buda` |

So `buda`, `buda_db`, `buda_cli`, `buda_viz`, `buda_cmds`, `bdb_serialize` and
about forty others are all **top-level names**, and every `import` in the repo
is written that way.

A wheel has to reproduce that contract, and it cannot reproduce it by keeping
the directory names. `build/` is a build product; `src/` and `tools/` are
names no distribution may claim in `site-packages` — and `tools` in particular
is a name the project already got burned by (`tools/__init__.py` records how a
third-party `tools` distribution appeared in CI and broke every
`from tools import …` at once).

So the wheel folds the layer into **one** directory:

```
site-packages/
├── buda.cpython-311-….so        the extension modules stay at the ROOT, so
├── buda_db.cpython-311-….so     `import buda` works with nothing on PYTHONPATH
├── libbuda_core.so              adjacent because their RPATH is $ORIGIN
└── buda_runtime/
    ├── __init__.py              the layout rule (below)
    ├── entry.py                 the console scripts
    ├── buda_cli.py  buda_viz.py  buda_cmds/  buda_session/  viz_main/ …   ← src/
    ├── web/static/  buda_icon.png  buda_fp_icon.png
    └── tools/
        ├── __init__.py          so `from tools import bdb2buda` still resolves
        ├── bdb_serialize.py …   and `import bdb_serialize` does too
        └── buda.tcl             beside buda_server.py — see §2
```

`buda_runtime/__init__.py` is the only thing that knows which layout it is in.
`paths()` is the table; `install()` puts it on `sys.path`. Both entry points
bootstrap through it rather than each carrying a copy of the rule — a rule
that is only ever exercised in one layout at a time is exactly the kind that
drifts unnoticed in the other.

### The import contract after installing — two tiers, on purpose

```python
import buda, buda_db                      # works bare

import buda_runtime; buda_runtime.install()
import buda_cli, bdb_serialize            # everything else, after that
from tools import bdb2buda                # one line, and only in a wheel
```

The second tier is a decision, not an oversight, and it is the one thing an
installed BUDA does *not* reproduce from the checkout. `buda` and `buda_db`
are the project's public names, so they land at the wheel root and cost
nothing. The rest is about forty **generic** top-level names — `ui_state`,
`viz_common`, `slot_groups`, `render`, `web`, `qor_corpus`, `tools` — and a
distribution that claimed them in `site-packages` would shadow other people's
code. `tools` is not hypothetical: one appeared transitively in CI and broke
every `from tools import …` in the suite at once (`tools/__init__.py` records
it). Two ways to avoid the bootstrap line were considered and rejected for
the same reason: installing those names at the root *is* the collision, and a
`.pth` only makes it lazy and harder to see.

Inside a checkout nothing changes — `bin/activate`, `pytest.ini` and the
wrappers already put the directories on `sys.path`, so the bootstrap is a
no-op there and every existing import keeps working.

`import buda_runtime` deliberately has **no** side effect; `install()` is the
ask. Pinned by `test_importing_buda_runtime_has_no_side_effect`.

**And `install()` is not free, which is the other half of the same argument.**
The names are not claimed *globally* — but `install()` claims them for your
whole **process**, at `sys.path[0]`, which is a *stronger* claim than
site-packages would have been: site-packages sits near the end of `sys.path`,
so your own module beats it, whereas index 0 beats even your script's own
directory. Measured on a real install, run from a directory holding the
caller's own `render.py`:

```
their own render : user
after install()  : …/site-packages/buda_runtime/tools/render.py
their own directory is now at sys.path index 4
```

43 modules in `tools/` alone go in front. That is right for the console
scripts (a dedicated process, where BUDA should win) and a no-op in a checkout
(the directories are the repo's anyway) — it is only wrong for a **library**
consumer, so:

```python
buda_runtime.install(front=False)     # append; your modules keep winning
```

`conftest.py` made the same choice for `tools`, for the same reason. Appending
moves the block, never the order inside it, so `build` stays ahead of `src`
and a stale `.so` beside the sources still cannot win.

`in_checkout()` asks for **the CMakeLists that builds us**, next to a
checkout's `buda_runtime/` and never installed. Probing for `build/` or `src/`
instead would be a guess and a wrong one: `build` is a real distribution (the
PEP 517 frontend) and installs as `site-packages/build/`, so any machine with
it would read as a checkout. Pinned by
`test_a_build_distribution_is_not_mistaken_for_a_checkout`.

## 2. What forces `tools/` to stay whole

`tools/buda.tcl` resolves its engine child from its own directory:

```tcl
variable dir [file dirname [file normalize [info script]]]
set server [file join $dir buda_server.py]
```

That is a path Tcl computes on a real filesystem, so no console script and no
import can stand in for it: `buda.tcl` and `buda_server.py` must install into
the same directory. Which is also why there is deliberately **no
`buda-server` console script** — and why there could not be one anyway: the
server distinguishes being run from being imported (`__name__ == "__main__"`
decides whether it claims the protocol channel), so an entry point calling its
`main()` would leave it speaking the protocol on a channel it never took.

The Tcl front end was verified against an installed copy, not just reasoned
about:

```tcl
source …/site-packages/buda_runtime/tools/buda.tcl
buda::start -viz 0 -python …/venv/bin/python
…
# installed tcl bridge ok: 1 bundle(s)
```

## 3. What the two entry files had to change

`src/buda_cli.py` and `tools/buda_server.py` are the two files on the **engine
import path** that hardcoded the checkout layout, both reaching for
`../build`. They are not the only files in the repo that do so — about 15
more join a computed repo root with `build` and 19 with `src` — but the rest
are developer harnesses (`qor_corpus.py`, `render.py`, `regen_goldens.py`, …)
that need a checkout to do anything at all, and converting them is not this
change's business. **Three were on the installed entry-point paths and are
converted here**, since a user's `buda-fp` really does execute them:
`tools/bdb_floorplanner.py`, `tools/def_viz_o3.py` and
`tools/floorplanner_commands.py`. Measured before the fix, on a real install:

```
>>> import buda_runtime; buda_runtime.install()
>>> import floorplanner_commands        # what buda-fp pulls in
added to sys.path:
   exists=False  …/site-packages/buda_runtime/build
   exists=False  …/site-packages/buda_runtime/src
```

Nothing broke — `entry._main` calls `install()` first, so the real paths were
already ahead, and Python tolerates a missing directory. But claiming
`sys.path[0]` from a module that cannot see which layout it is in is the exact
habit `buda_runtime` exists to end, and it was only *accidentally* safe: a
future layout with a real `buda_runtime/src` would make those inserts win.
All three are now content- or existence-checked, so they are the checkout
fallback they were always meant to be and a no-op once installed.

* `buda_cli.py` now **finds `buda_runtime`** — a sibling of its directory in a
  checkout, its own containing directory in a wheel — and asks it. It does not
  restate the layout.
* `buda_server.py` does less still: it only has to reach `buda_cli`, whose
  bootstrap then installs the rest. In a checkout that is the sibling `src/`;
  in a wheel it is one directory up.
* `buda_viz.py`'s `viz_ipc` fallback inserted `<…>/../tools` at `sys.path[0]`
  unconditionally. Installed, `..` is site-packages — and `tools` is a real
  distribution name there. The first attempt at a fix guarded it with
  `isdir`, which **guards nothing**: that is true exactly when the stranger
  exists, and false only in the case that was never the hazard. It now asks
  the question it means — *is this `tools` mine?* — by looking for the file it
  is about to import (`viz_ipc.py`), the same content-addressed form
  `buda_server.py` uses. The fallback is kept rather than deleted: `pytest.ini`
  sets `pythonpath = build src` and conftest only *appends* `tools`, so a test
  importing `buda_viz` without going through `buda_cli` relies on it.

`entry.py` also answers `-h`/`--help` for the two GUI commands **before**
importing anything, which is exactly why `bin/fp` and `bin/viz` answer it in
bash before they exec. A GUI entry point imports tkinter and picks a
matplotlib backend at module scope, so "what does this command do" would
otherwise need a display to answer — measured on the installed copy,
`buda-fp --help` reported `cannot open BDB — file not found: --help` and
`buda-viz --help` died in `import tkinter`. `buda --help` is deliberately
*not* intercepted: argparse inside `buda_cli` generates it from the real
option list, and a copy here would go stale.

## 4. `pip install -e .` — measured, then fixed

The first working version of this was **wrong in a way that reported success**:
with the whole layer installed by CMake, scikit-build-core *copied* `src/*.py`
into site-packages for an editable install. Editing the checkout changed
nothing; the install looked fine and served a snapshot taken at install time.

scikit-build-core redirects a **declared package** and copies everything else,
so the fix is one line — `wheel.packages = ["buda_runtime"]` — plus a
`SKBUILD_STATE` guard so CMake does not also drop a copy beside the redirect.
The editable case then falls out of the existing rule rather than needing one
of its own: the redirect resolves `buda_runtime` to the checkout,
`in_checkout()` is true there, and `paths()` hands back the live `src/` and
`tools/`.

Verified by editing a source file and observing the effect through the
editable venv, not by reading the config.

**One residual, stated because it will otherwise be discovered:** inside an
editable venv the *extension modules* are pip's build, not `bin/bb`'s.
scikit-build-core installs a meta-path finder, and a meta-path finder runs
ahead of `sys.path`, so `import buda` resolves to
`site-packages/buda.…so` even after `buda_runtime.install()` has put
`<repo>/build` first. Consequences:

* the venv is self-consistent — always pip's engine, never a mixture;
* a **C++** edit needs `pip install -e .` again (a Python edit does not);
* that engine is built with `BUDA_ARCH=none` (§5), so its exact counts may
  differ from your `bin/bb` build.

For C++ work, use `bin/activate`. The editable install is for *"I want the
console scripts on my PATH while I work on the Python layer."*

## 5. `BUDA_ARCH=none`, and why it matters here

A pip build is a **distributable** build, so it must not bake in the build
machine's instruction set. `pyproject.toml` pins `BUDA_ARCH=none` for exactly
the reason CI pins it (see the comment at `BUDA_ARCH` in `CMakeLists.txt`):
exact overlap/opens counts drift across microarchitectures, and the placement
goldens are what that perturbs.

The consequence is worth stating rather than discovering: **a pip-installed
BUDA and a `bin/bb` BUDA are two different builds and may report different
exact counts on the same design.** Override deliberately if you want the
developer default:

```bash
pip install . -C cmake.define.BUDA_ARCH=native
```

Relatedly, `build-dir = ".pipbuild/{wheel_tag}"` — deliberately *not* `build/`.
Sharing it would mean a `pip install` silently re-configures the tree the next
`bb` builds in, with a different `BUDA_ARCH`.

## 6. What is checked, and how

`test/tests/test_packaging.py` pins the pairs that can drift silently — the
two version declarations, the three console scripts against the functions and
`main`s they name, the installed branch of `paths()` that a checkout never
executes, the `buda.tcl`/`buda_server.py` adjacency, and the wholesale-install
rule. It does **not** build a wheel: that compiles the engine and takes
minutes.

The build itself is a by-hand step, last run on this tree:

| check | result |
|---|---|
| `pip install .` into a clean venv | wheel built, 3.5 MB, `buda-3.0.0-cp311-cp311-linux_x86_64` |
| `buda --no-viz demo/quickstart.buda`, run from `/tmp` | 9 commands, clean |
| `open_bdb` on a `.bdb.sql` (reaches `bdb_serialize`) | 8 busterms |
| `import buda`, `import buda_db` — bare, nothing on PYTHONPATH | resolve at the wheel root |
| `buda_cli`, `bdb_serialize`, `buda_cmds`, `from tools import …` **after `buda_runtime.install()`** | 102 commands registered — and they fail *without* it, which is the contract above, not a defect |
| `buda-fp --help`, `buda-viz --help` | usage printed without touching tkinter or a display |
| `buda.BDB is buda_db.BDB` | `True` — one `std::type_info`, as in a `bb` build |
| Tcl bridge sourced from the installed copy | 1 bundle |
| `pip install -e .`, then edit a source file | change visible immediately |

## 6a. macOS — measured, not assumed

`.github/workflows/macos-wheel.yml` is a **prototype**, not a gate and not a
matrix: `workflow_dispatch`, one Python, artifact only, nothing published. It
exists because §7 used to say delocate "would mostly verify rather than repair,
but that is a claim to test, not to assert" — and because before it, no
workflow in this repo had ever run on macOS at all, on the platform carrying
the most platform-specific code in the tree.

Two jobs, because one runner cannot do both halves:

| job | what it is |
|---|---|
| `native` | arm64 on `macos-latest`, built plainly, **fully smoke-tested**. The evidence. |
| `intel` | x86_64 **cross-built** with cibuildwheel, **never executed there**. The deliverable for an Intel Mac; the target machine is the only execution test, and the job's name says so. |

`macos-13` — the obvious choice for a native Intel build — is **retired**, and
the first run found out the slow way: 15 minutes queued with `runner_id: 0`,
no runner ever assigned. Worth knowing the failure mode, because an unserved
label does not error, it *waits*: "still queued" reads like a busy fleet.

**The delocate answer, and it is half of what was expected.**

```
before repair:  /usr/lib/libSystem.B.dylib  /usr/lib/libc++.1.dylib  libbuda_core.dylib
after  repair:  /usr/lib/libSystem.B.dylib  /usr/lib/libc++.1.dylib  libbuda_core.dylib
```

delocate vendored **nothing** — no third-party dylib to bring in, so the
"verify rather than repair" half is confirmed. But it is **not** a no-op: it
rewrote both extensions' install names from `@rpath/libbuda_core.dylib` to
`@loader_path/libbuda_core.dylib` and deleted the now-redundant `@loader_path`
rpath entry, collapsing one level of indirection. A normalization, not a
repair. And the wheel that passed the smoke test is the **raw** one, so
`@rpath` + `LC_RPATH=@loader_path` resolves on macOS as designed.

**What the native job proved** (the project's first macOS execution):

| check | result |
|---|---|
| `import buda`, `import buda_db` bare | resolve at the wheel root |
| `buda.BDB is buda_db.BDB` | `True` — one `std::type_info`, so `@loader_path` really shares `libbuda_core` |
| `import buda_cli` before `install()` | `ModuleNotFoundError`, as the two-tier contract requires |
| `in_checkout()` | `False` |
| after `install()` | 102 commands — identical to Linux |
| `buda --no-viz demo/quickstart.buda` from outside the checkout | 9 commands, clean |
| `buda-fp --help`, `buda-viz --help` | usage — the headless-help fix, verified on a host that HAS tkinter (the opposite of where it was written) |
| Tcl bridge sourced from the installed copy | 1 bundle |

**`MACOSX_DEPLOYMENT_TARGET` must be set explicitly, and the first run proved
it by getting it wrong.** Unset, a wheel inherits the *runner's* macOS version
as its floor: the arm64 wheel came out `macosx_26_0_arm64` — installable on
macOS 26 and nothing older — from a job whose green tick said nothing about it.
The Intel job was already right because cibuildwheel had been handed `13.0`
explicitly; the asymmetry was the bug. Both are now pinned at 13.0, and the
`Host facts` step prints the value so an unset one is visible rather than
inferred from a filename.

Installed and run on a real Intel Mac (macOS 26, Anaconda Python 3.13.5):
`buda-3.0.0-cp313-cp313-macosx_13_0_x86_64.whl` installs and routes
`demo/quickstart.buda` to the same numbers as Linux and as the arm64 job —
5 bundles, 16 segments, 0 overlaps, 61 net segments, 0 unplaced.

One gotcha worth pre-empting: a `.whl` downloaded through a browser carries
`com.apple.quarantine`, which can stop the extracted dylib from loading.
`xattr -dr com.apple.quarantine .` before installing.

## 6b. Windows — the first deliverable wheel

`.github/workflows/windows-wheel.yml` is the Windows twin of §6a's prototype:
`workflow_dispatch`, one Python (**cp313**, the current stable), `windows-2022`,
artifact only, nothing published. It exists because a real Windows box wanted a
real install, and opens_interchange item 8 said the packaged wheel was the one
interchange item still owed code. One bootstrap quirk worth recording: a
workflow that exists only on a topic branch is **not dispatchable by name**
(the workflows index reads the default branch — the dispatch 404s, measured
twice), so the file carries a push trigger scoped to its own branch and path;
the first push ran it, the run registered it, and the by-name dispatch works
from then on. On `main` the branch filter matches nothing.

What run 1 proved, in one 3m38s job (first attempt, no iteration needed —
which is the windows-validate arc paying out: the build system and the
subprocess/path/encoding traps were all already fixed):

| check | result |
|---|---|
| `pip wheel . --no-deps` on bare MSVC (no developer shell) | `buda-3.0.0-cp313-cp313-win_amd64.whl`, 3.2 MB — scikit-build-core locates VS itself |
| fresh venv, cwd `$env:TEMP` (outside the checkout) | `import buda, buda_db, buda_runtime` from site-packages |
| `buda_runtime/tools/buda.tcl` | shipped, found by the smoke assert |
| `flow/four_blocks.buda` via the installed `buda.exe` | full pipeline, 0 overlaps / 0 unplaced, all three `check_design` stages clean, BUDA-1903 voicing the suppressed viz |
| GUI deps | `tkinter` + `matplotlib.use('TkAgg')` select cleanly (setup-python ships Tcl/Tk) |
| web deps | `src/web/requirements.txt` installs; fastapi/uvicorn/httpx import |
| entry points | `buda`, `floorplanner`, `viz` importable headless |

Install on a real box: `py -3.13 -m pip install <wheel>` (the python.org
installer with Tcl/Tk checked matches everything the smoke exercised);
`PYTHONUTF8=1` is the standing recommendation from `WINDOWS_REQ.md`. The Tcl
front end additionally needs a `tclsh` (e.g. Magicsplat Tcl/Tk) — the bridge
itself ships in the wheel. The web backend runs from an installed copy as
`import buda_runtime; buda_runtime.install()` then `uvicorn.run(web.server.app)`
— `install()` puts `buda_runtime/` itself on `sys.path`, which is what makes
`server.py`'s top-level `from web import …` resolve in both layouts.

## 7. What this does NOT do

* No wheel **matrix**: one macOS prototype (§6a), one Windows deliverable
  (§6b), nothing for Linux, one Python version each, and nothing published.
  `auditwheel` has still not been run — the Linux side of the "verify rather
  than repair" claim remains untested, even though the macOS side now has an
  answer.
* No version-bump process. The version is declared twice (`pyproject.toml`,
  `CMakeLists.txt`) and pinned equal by a test; nothing automates changing it.
* `src/web`'s served files ship, but the Scala.js front-end bundle is a build
  product (`bb web`) and is not tracked, so a pip install does not carry it.
* The dev harnesses under `tools/` (`qor_corpus.py`, `runtime_ab.py`,
  `regen_goldens.py`, …) ship because the rule is "every `.py`" — a selective
  list is a judgement call that fails silently, and these are small text
  files. They still need a checkout to do anything.

See also: [opens_interchange.md](opens_interchange.md) item 8,
[build_test_dependencies.md](../build_test_dependencies.md),
[ci.md](ci.md).
