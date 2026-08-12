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

`src/buda_cli.py` and `tools/buda_server.py` were the only two files that
hardcoded the checkout layout, both reaching for `../build`.

* `buda_cli.py` now **finds `buda_runtime`** — a sibling of its directory in a
  checkout, its own containing directory in a wheel — and asks it. It does not
  restate the layout.
* `buda_server.py` does less still: it only has to reach `buda_cli`, whose
  bootstrap then installs the rest. In a checkout that is the sibling `src/`;
  in a wheel it is one directory up.
* `buda_viz.py`'s `viz_ipc` fallback inserted `<…>/../tools` at `sys.path[0]`
  unconditionally. Installed, `..` is site-packages — and `tools` is a real
  distribution name there. Now existence-checked.

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
| every import path (`buda`, `buda_cli`, `bdb_serialize`, `from tools import …`, `buda_cmds`) | 102 commands registered |
| `buda.BDB is buda_db.BDB` | `True` — one `std::type_info`, as in a `bb` build |
| Tcl bridge sourced from the installed copy | 1 bundle |
| `pip install -e .`, then edit a source file | change visible immediately |

## 7. What this does NOT do

* No wheels are built for other platforms or Python versions, and none are
  published. `auditwheel`/`delocate` have not been run — the three artifacts
  are already adjacent with an `$ORIGIN`/`@loader_path` RPATH, so those tools
  would mostly verify rather than repair, but that is a claim to test, not to
  assert.
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
