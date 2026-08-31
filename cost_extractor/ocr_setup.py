"""Resolves the bundled portable Tesseract-OCR install, dev vs. frozen."""

from __future__ import annotations

import sys
from pathlib import Path


def get_tesseract_dir() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller's standard resolution for bundled `datas=` content:
        # sys._MEIPASS points at the onedir build's _internal folder (or the
        # onefile temp extraction dir), which is where
        # datas=[('vendor/tesseract', 'tesseract')] actually lands — NOT
        # necessarily next to the .exe. Verified against a real build in
        # the packaging phase, not assumed.
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) if meipass else Path(sys.executable).resolve().parent
        return base / "tesseract"
    # Dev mode: the folder is vendored (gitignored) at <project root>/vendor/tesseract.
    return Path(__file__).resolve().parent.parent / "vendor" / "tesseract"


def get_tessdata_config() -> str:
    """Builds the `--tessdata-dir <path>` config string passed to pytesseract.

    Deliberately NOT quoted: pytesseract splits this string via
    `shlex.split(config, posix=False)` on Windows, and non-POSIX shlex
    does not strip quote characters — a quoted path arrives at
    tesseract.exe with the literal quote marks still embedded, corrupting
    the path (verified: "Error opening data file ...tessdata"/eng.traineddata").
    This means a project path containing spaces cannot be passed safely
    through this config string on Windows; see `configure_pytesseract`
    for the TESSDATA_PREFIX fallback that covers that case.
    """
    tessdata_dir = get_tesseract_dir() / "tessdata"
    return f"--tessdata-dir {tessdata_dir}"


def configure_pytesseract() -> None:
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = str(get_tesseract_dir() / "tesseract.exe")
