"""Resolves the bundled portable Tesseract-OCR install, dev vs. frozen."""

from __future__ import annotations

import sys
from pathlib import Path


def _tesseract_vendor_subdir() -> str:
    # Windows keeps the original "tesseract" name (existing installs/CI
    # reference it); macOS gets its own subdir since the vendored binary
    # and its bundled dylibs are entirely different artifacts and must
    # never be mixed up if both happen to exist in the same checkout.
    return "tesseract-macos" if sys.platform == "darwin" else "tesseract"


def get_tesseract_executable_name() -> str:
    return "tesseract.exe" if sys.platform == "win32" else "tesseract"


def get_tesseract_dir() -> Path:
    subdir = _tesseract_vendor_subdir()
    if getattr(sys, "frozen", False):
        # PyInstaller's standard resolution for bundled `datas=` content:
        # sys._MEIPASS points at the onedir build's _internal folder (or the
        # onefile temp extraction dir), which is where
        # datas=[('vendor/tesseract', 'tesseract')] actually lands — NOT
        # necessarily next to the .exe. Verified against a real Windows
        # build in the packaging phase, not assumed; PyInstaller sets
        # _MEIPASS the same way on macOS, so the same resolution applies.
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) if meipass else Path(sys.executable).resolve().parent
        return base / subdir
    # Dev mode: the folder is vendored (gitignored) at <project root>/vendor/<subdir>.
    return Path(__file__).resolve().parent.parent / "vendor" / subdir


def get_tessdata_config() -> str:
    """Builds the `--tessdata-dir <path>` config string passed to pytesseract.

    Quoting is platform-conditional because pytesseract splits this string
    via `shlex.split(config, posix=(sys.platform != "win32"))`:
    - On Windows, non-POSIX shlex does NOT strip quote characters — a
      quoted path arrives at tesseract.exe with the literal quote marks
      still embedded, corrupting the path (verified: "Error opening data
      file ...tessdata"/eng.traineddata"). So it must stay unquoted there,
      which means a Windows project path containing spaces can't be
      passed safely through this config string — known limitation.
    - On macOS/Linux, POSIX-mode shlex correctly strips quotes, so the
      path is quoted for safety against spaces.
    """
    tessdata_dir = get_tesseract_dir() / "tessdata"
    if sys.platform == "win32":
        return f"--tessdata-dir {tessdata_dir}"
    return f'--tessdata-dir "{tessdata_dir}"'


def configure_pytesseract() -> None:
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = str(
        get_tesseract_dir() / get_tesseract_executable_name()
    )
