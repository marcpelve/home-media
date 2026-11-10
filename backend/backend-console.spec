# -*- mode: python ; coding: utf-8 -*-
"""Console variant of the PyInstaller spec - for diagnosing startup output.

Identical to backend.spec except console=True and a distinct name/distpath so
it does not clobber the shipping home-media.exe.
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
    name="home-media-console",
    debug=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_hooks=[],
    console=True,
    disable_windowed_traceback=False,
    onefile=True,
)
