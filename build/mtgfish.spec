# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the MTG Commander Goldfisher.

One-folder, not one-file. QtWebEngine does not survive a one-file build
reliably: it launches a *separate* helper process for the renderer, and that
helper has to find Qt's resource and locale files on disk. In a one-file build
those live in a temporary directory that the helper may not be able to see, and
the symptom is a blank white window with nothing in any log.

Three things have to be bundled by hand:

* ``mtgfish/ui/web`` - the front end. PyInstaller follows imports, and nothing
  imports an HTML file.
* ``cards.sqlite`` - 99 MB, shipped read-only and copied to the user's data
  directory on first launch (see ``mtgfish.bootstrap``).
* Qt's WebEngine payload, which the PySide6 hook mostly handles; the collect
  call below makes the translations and resources explicit rather than hoping.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "mtgfish" / "ui" / "web"), "mtgfish/ui/web"),
]

# The card database, if it has been built. Without it the executable still
# runs and says so, rather than silently simulating with an empty card pool.
card_db = ROOT / "cache" / "cards.sqlite"
if card_db.exists():
    datas.append((str(card_db), "."))

rules_text = ROOT / "cache" / "comprehensive_rules.txt"
if rules_text.exists():
    datas.append((str(rules_text), "."))

# QtWebEngine's own resources, locales and the helper process.
datas += collect_data_files("PySide6", subdir="Qt/resources")
datas += collect_data_files("PySide6", subdir="Qt/translations")
binaries = collect_dynamic_libs("PySide6")

a = Analysis(
    # Not mtgfish/ui/__main__.py: PyInstaller runs the entry script as
    # __main__ with no package around it, so its relative imports fail before
    # anything else happens.
    [str(ROOT / "build" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # Reached only from inside functions, which PyInstaller does follow -
        # but these are the modules the app is useless without, so they are
        # named rather than relied upon.
        "mtgfish.sim.lab",
        "mtgfish.tools.swap",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "PySide6.QtNetwork",
        # The simulator spawns workers; multiprocessing's spawn start method
        # imports these by name rather than by an import statement anything
        # can follow.
        "multiprocessing.pool",
        "multiprocessing.spawn",
    ],
    excludes=[
        # Dev-only, and each drags in a large dependency tree.
        "pytest",
        "PyInstaller",
        "torch",
        "matplotlib",
        "PySide6.QtQuick3D",
        "PySide6.QtMultimedia",
        "PySide6.Qt3DCore",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="MTG Goldfisher",
    console=False,
    disable_windowed_traceback=False,
    # A crash before the window opens has nowhere to print to in a windowed
    # build, so the launcher writes one to the user data directory instead.
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # UPX corrupts Qt's DLLs often enough not to be worth it.
    name="MTG Goldfisher",
)
