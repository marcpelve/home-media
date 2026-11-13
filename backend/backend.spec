# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the home-media backend.

Builds a single windowed .exe (no console) that runs the FastAPI app under
uvicorn on 127.0.0.1:8790.

The vendored cocoscrapers package discovers its source modules at RUNTIME via
walk_packages() — a FILESYSTEM walk of the sources_cocoscrapers folder.
PyInstaller's PYZ stores modules compressed in the archive (not on disk), so
if the package were bundled as PYZ modules, walk_packages would find none and
every source would come back "disabled" (0 results). The coco tree is
therefore bundled as ON-DISK data files (not PYZ modules) so walk_packages can
see the source modules. kodi_stub is a single module with no dynamic
discovery, so it is bundled as a normal hiddenimport.
"""

import os

block_cipher = None

a = Analysis(
    ["server.py"],
    pathex=[os.path.join(".")],
    binaries=[],
    datas=[(os.path.join(".", "app", "coco", "cocoscrapers"), "cocoscrapers")],
    hiddenimports=[
        "app.coco.kodi_stub",
        # The coco tree is bundled as on-disk data (not statically analyzed),
        # so its stdlib imports must be listed here explicitly.
        "xml.dom.minidom",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["cocoscrapers"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="home-media",
    debug=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_hooks=[],
    console=False,
    disable_windowed_traceback=False,
    onefile=False,
    onedir=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="home-media",
)
