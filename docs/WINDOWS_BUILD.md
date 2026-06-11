# Building BUDA On Windows

This guide shows how to configure, build, and test BUDA in a native Windows
environment with MSVC. See `docs/WINDOWS_REQ.md` for software requirements and
known portability notes.

The commands below assume PowerShell and a 64-bit Python installation.

## 1. Open A Developer Shell

Use one of these shells:

- `x64 Native Tools Command Prompt for VS 2022`
- `Developer PowerShell for VS 2022`
- PowerShell after loading the Visual Studio developer environment

The shell must be able to find the MSVC compiler and Windows SDK.

Verify the tools:

```powershell
cl
cmake --version
python --version
```

If `python` is not available but the Python launcher is installed, use `py`
where appropriate.

## 2. Create A Python Environment

From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install pybind11 pytest pytest-bdd matplotlib
```

If Python 3.13 is not installed, replace `py -3.13` with the installed 64-bit
Python version, for example `py -3.12` or `python`.

Confirm tkinter is available if you plan to use the GUI tools:

```powershell
python -c "import tkinter; import matplotlib; matplotlib.use('TkAgg')"
```

## 3. Build With Ninja

Ninja is the recommended Windows path for a simple single-configuration build.

Install Ninja if needed:

```powershell
python -m pip install ninja
```

Configure and build:

```powershell
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Set `PYTHONPATH` so Python can find the compiled extension and BUDA scripts:

```powershell
$env:PYTHONPATH = "$PWD\build;$PWD\src;$PWD\tools;$env:PYTHONPATH"
```

Run tests:

```powershell
pytest -q
```

Run a BUDA script:

```powershell
python src\buda_cli.py flow\four_blocks.buda
```

## 4. Build With Visual Studio Generator

Use this path if you prefer MSBuild or a Visual Studio solution.

Configure and build:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
```

Visual Studio generators are multi-configuration, so the compiled extension is
usually under `build\Release`. Set `PYTHONPATH` accordingly:

```powershell
$env:PYTHONPATH = "$PWD\build\Release;$PWD\build;$PWD\src;$PWD\tools;$env:PYTHONPATH"
```

Run tests:

```powershell
pytest -q
```

Run a BUDA script:

```powershell
python src\buda_cli.py flow\four_blocks.buda
```

## 5. Troubleshooting

### CMake Cannot Find `python3`

The current CMake configuration locates pybind11 by invoking `python3`. Native
Windows Python installs often expose `python` or `py`, not `python3`.

Workarounds:

```powershell
python -m pybind11 --cmakedir
```

Then pass the reported directory to CMake:

```powershell
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -Dpybind11_DIR="C:\path\to\pybind11\share\cmake\pybind11"
```

A future CMake update should replace the hardcoded `python3` lookup with
CMake's discovered Python interpreter.

### CMake Cannot Find pybind11

Make sure pybind11 is installed in the active Python environment:

```powershell
python -m pip show pybind11
python -m pybind11 --cmakedir
```

If needed, pass `-Dpybind11_DIR=...` using the directory printed by
`python -m pybind11 --cmakedir`.

### `ModuleNotFoundError: No module named 'buda'`

Python cannot find the compiled extension. Check which generator you used:

- Ninja: include `build` in `PYTHONPATH`.
- Visual Studio: include `build\Release` and `build` in `PYTHONPATH`.

Examples:

```powershell
$env:PYTHONPATH = "$PWD\build;$PWD\src;$PWD\tools;$env:PYTHONPATH"
```

```powershell
$env:PYTHONPATH = "$PWD\build\Release;$PWD\build;$PWD\src;$PWD\tools;$env:PYTHONPATH"
```

### tkinter Or TkAgg Fails

The GUI tools require tkinter and Matplotlib's `TkAgg` backend.

Check the active Python environment:

```powershell
python -c "import tkinter"
python -c "import matplotlib; matplotlib.use('TkAgg')"
```

If either command fails, install or repair Tcl/Tk support for the selected
Python distribution. With Conda, this may require installing the `tk` package in
the active environment.

### Architecture Mismatch

Use x64 consistently:

- x64 Visual Studio developer shell.
- `-A x64` for Visual Studio generator.
- 64-bit Python.

Mixing x86 and x64 tools can produce configure, link, or import failures.

### `bb` Or `buda` Does Not Run

The top-level `bb` and `buda` wrappers are Bash scripts. They are not native
PowerShell scripts.

On Windows, use the explicit CMake, pytest, and Python commands in this guide
until native wrappers are added.

