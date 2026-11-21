# PyInstaller spec for ds4catia.
#
# LIMITATION (see the project plan / docs/manual_verification_checklist.md):
# this file is authored and reviewed here, but it CANNOT be built into a
# working .exe in this Linux development environment -- PyInstaller does
# not meaningfully cross-compile Windows executables from Linux, and the
# Windows-only runtime deps (hid, pywin32, pycatia) aren't installed here
# either. Building and smoke-running the .exe from this spec is the
# user's job, on their Windows machine, with:
#
#   pip install .[dev] pyinstaller
#   pyinstaller ds4catia.spec
#
# which produces dist/ds4catia/ds4catia.exe (one-folder build; switch to
# a single-file build by setting ONEFILE = True below once the one-folder
# build has been smoke-tested -- onefile is slower to start and harder to
# debug, so it is not the default here).

import sys
from pathlib import Path

block_cipher = None
ONEFILE = False

project_root = Path(SPECPATH)

# Default profiles + the published JSON Schema must ship alongside the
# exe so a fresh install has something to load; they are *data*, not
# importable Python, so PyInstaller won't pick them up automatically.
datas = [
    (str(project_root / "ds4catia" / "mapping" / "profiles"), "ds4catia/mapping/profiles"),
    (str(project_root / "ds4catia" / "mapping" / "profile.schema.json"), "ds4catia/mapping"),
]

# win32com/pywin32 and PySide6 both use plugin/COM-registration machinery
# that static analysis can miss; list the modules PyInstaller's import
# scanner is most likely to drop so a frozen build doesn't fail at
# runtime with an ImportError that only shows up on the user's machine.
hiddenimports = [
    "win32com",
    "win32com.client",
    "win32com.client.gencache",
    "win32timezone",  # commonly needed transitively by pywin32 COM code
    "pythoncom",
    "pywintypes",
    "hid",
]

a = Analysis(
    ["ds4catia/main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="ds4catia",
        console=False,
        icon=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="ds4catia",
        console=False,
        icon=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        name="ds4catia",
    )
