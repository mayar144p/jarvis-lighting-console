"""The Stream Deck plugin, packed for download.

The plugin itself is web/streamdeck/com.jarvis.desk.sdPlugin (a Node script
the Stream Deck app runs; it sends OSC to the desk).  A .streamDeckPlugin
file is a zip with that folder at its root: double-clicking it installs it.
"""
from __future__ import annotations

import io
import zipfile

from . import config

FOLDER = config.WEB / "streamdeck" / "com.jarvis.desk.sdPlugin"
FILE_NAME = "com.jarvis.desk.streamDeckPlugin"


def plugin_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(FOLDER.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                z.write(p, f"{FOLDER.name}/{p.relative_to(FOLDER).as_posix()}")
    return buf.getvalue()
