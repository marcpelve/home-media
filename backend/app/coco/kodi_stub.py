"""Kodi API stub for running Cocoscrapers standalone (no Kodi installed).

Installs fake xbmc, xbmcgui, xbmcaddon, xbmcvfs modules into sys.modules
before cocoscrapers is imported. Call install() once at startup.

The stub provides:
- In-memory window properties (pre-seeded with Cocoscrapers settings)
- No-op dialogs, progress, and monitor objects
- os-based file ops via xbmcvfs
- stdlib logging via xbmc.log
- A data directory under <project>/data/coco/ for runtime state
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import types
from pathlib import Path

_COCO_DIR = Path(__file__).resolve().parent
_DATA_DIR = _COCO_DIR.parent.parent.parent / "data" / "coco"

_LOGGER = logging.getLogger("cocoscrapers")

# All providers enabled; debug off; filters on.
# control.setting() reads these from the window property on first call.
_DEFAULT_SETTINGS: dict[str, str] = {
    "debug.enabled": "false",
    "debug.location": "0",
    "debug.reversed": "false",
    "filter.undesirables": "true",
    "filter.foreign.single.audio": "true",
    "provider.1337x": "true",
    "provider.bitcq": "true",
    "provider.bitlord": "true",
    "provider.bitsearch": "true",
    "provider.comet": "true",
    "provider.eztv": "true",
    "provider.isohunt2": "true",
    "provider.kickass2": "true",
    "provider.knaben": "true",
    "provider.mediafusion": "true",
    "provider.nyaa": "true",
    "provider.piratebay": "true",
    "provider.torrentdownload": "true",
    "provider.torrentfunk": "true",
    "provider.torrentgalaxy": "true",
    "provider.torrentio": "true",
    "provider.torrentproject2": "true",
    "provider.torrentquest": "true",
    "provider.yourbittorrent": "true",
    "provider.ytsmx": "true",
}


class _Window:
    """In-memory xbmcgui.Window with property storage."""

    def __init__(self) -> None:
        self._props: dict[str, str] = {
            "cocoscrapers_settings": json.dumps(_DEFAULT_SETTINGS),
        }

    def getProperty(self, key: str) -> str:
        return self._props.get(key, "")

    def setProperty(self, key: str, value: str) -> None:
        self._props[key] = value

    def clearProperty(self, key: str) -> None:
        self._props.pop(key, None)


class _Dialog:
    def notification(self, *a, **kw): pass
    def yesno(self, *a, **kw): return True
    def select(self, *a, **kw): return 0
    def multiselect(self, *a, **kw): return [0]
    def ok(self, *a, **kw): pass


class _DialogProgress:
    def create(self, *a, **kw): pass
    def update(self, *a, **kw): pass
    def close(self): pass
    def isCanceled(self): return False


class _Monitor:
    def abortRequested(self): return False


class _ListItem:
    def __init__(self, *a, **kw): pass
    def setLabel(self, *a): pass
    def setArt(self, *a, **kw): pass
    def setInfoLabels(self, *a, **kw): pass


class _ControlProgress:
    def __init__(self, *a, **kw): pass


class _WindowXMLDialog:
    def __init__(self, *a, **kw): pass
    def run(self): pass


class _Addon:
    def __init__(self, addon_id: str = "script.module.cocoscrapers") -> None:
        self._id = addon_id

    def getAddonInfo(self, key: str) -> str:
        info = {
            "id": "script.module.cocoscrapers",
            "name": "CocoScrapers",
            "version": "1.0.32",
            "path": str(_COCO_DIR / "cocoscrapers"),
            "profile": str(_DATA_DIR),
            "icon": "",
        }
        return info.get(key, "")

    def getLocalizedString(self, id: int) -> str:
        return str(id)

    def getSetting(self, key: str) -> str:
        return _DEFAULT_SETTINGS.get(key, "")

    def setSetting(self, key: str, value: str) -> None:
        _DEFAULT_SETTINGS[key] = value


class _XbmcFile:
    """Wraps a stdlib file object to mimic xbmcvfs.File."""

    def __init__(self, path: str, mode: str = "r") -> None:
        self._f = open(path, mode)

    def read(self, *a):
        return self._f.read(*a)

    def write(self, data):
        return self._f.write(data)

    def close(self):
        return self._f.close()

    def seek(self, *a):
        return self._f.seek(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._f.close()
        return False


def _translate_path(path: str) -> str:
    """Resolve Kodi special:// paths to local directories."""
    if path.startswith("special://logpath/"):
        log_dir = _DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return str(log_dir)
    if path.startswith("special://profile/"):
        rest = path[len("special://profile/"):]
        return str(_DATA_DIR / rest)
    return path


def install() -> None:
    """Install fake Kodi modules into sys.modules. Idempotent."""
    if "xbmc" in sys.modules and getattr(sys.modules["xbmc"], "_is_stub", False):
        return

    # cfscrape (bundled under cocoscrapers/modules) imports DEFAULT_CIPHERS,
    # which urllib3 >= 2.x (Python 3.14) removed. Restore it before import.
    import urllib3.util.ssl_ as _ssl
    if not hasattr(_ssl, "DEFAULT_CIPHERS"):
        _ssl.DEFAULT_CIPHERS = "DEFAULT"

    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure cocoscrapers is importable as a top-level package.
    coco_dir = str(_COCO_DIR)
    if coco_dir not in sys.path:
        sys.path.insert(0, coco_dir)

    # --- xbmc ---
    xbmc = types.ModuleType("xbmc")
    xbmc._is_stub = True
    xbmc.log = lambda msg, level=1: _LOGGER.debug(msg)
    xbmc.sleep = lambda ms: time.sleep(ms / 1000)
    xbmc.getCondVisibility = lambda expr: False
    xbmc.getInfoLabel = lambda label: ""
    xbmc.executebuiltin = lambda cmd: None
    xbmc.executeJSONRPC = lambda cmd: None
    xbmc.Monitor = _Monitor

    # --- xbmcgui ---
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui._is_stub = True

    _shared_window = _Window()
    xbmcgui.Window = lambda id=10000: _shared_window
    xbmcgui.Dialog = _Dialog
    xbmcgui.DialogProgress = _DialogProgress
    xbmcgui.ListItem = _ListItem
    xbmcgui.ControlProgress = _ControlProgress
    xbmcgui.WindowXMLDialog = _WindowXMLDialog
    xbmcgui.NOTIFICATION_INFO = 0
    xbmcgui.NOTIFICATION_WARNING = 1
    xbmcgui.NOTIFICATION_ERROR = 2

    # --- xbmcaddon ---
    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon._is_stub = True
    xbmcaddon.Addon = _Addon

    # --- xbmcvfs ---
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs._is_stub = True
    xbmcvfs.translatePath = _translate_path
    xbmcvfs.exists = os.path.exists
    xbmcvfs.delete = lambda p: os.remove(p) if os.path.exists(p) else None
    xbmcvfs.mkdir = lambda p: os.mkdir(p) if not os.path.exists(p) else True
    xbmcvfs.mkdirs = lambda p: os.makedirs(p, exist_ok=True)
    xbmcvfs.rename = os.rename
    xbmcvfs.File = _XbmcFile

    sys.modules["xbmc"] = xbmc
    sys.modules["xbmcgui"] = xbmcgui
    sys.modules["xbmcaddon"] = xbmcaddon
    sys.modules["xbmcvfs"] = xbmcvfs
