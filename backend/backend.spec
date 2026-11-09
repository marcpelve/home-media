# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the home-media backend.

Builds a single windowed .exe (no console) that runs the FastAPI app under
uvicorn on 127.0.0.1:8790. The vendored cocoscrapers package is bundled as a
top-level module (it is imported via a runtime sys.path tweak in coco_driver).
"""

import os

block_cipher = None

a = Analysis(
    ["server.py"],
    pathex=[os.path.join("."), os.path.join(".", "app", "coco")],
    binaries=[],
    datas=[],
    hiddenimports=[
        "cocoscrapers",
        "app.coco.kodi_stub",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="home-media",
    debug=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_hooks=[],
    console=False,
    disable_windowed_traceback=False,
    onefile=True,
)
