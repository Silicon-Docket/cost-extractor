import sys
from pathlib import Path

from cost_extractor import ocr_setup


def test_get_tesseract_dir_in_dev_mode_resolves_to_project_root_vendor(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    project_root = Path(ocr_setup.__file__).resolve().parent.parent

    tess_dir = ocr_setup.get_tesseract_dir()

    assert tess_dir == project_root / "vendor" / "tesseract"


def test_get_tesseract_dir_when_frozen_uses_meipass(monkeypatch, tmp_path):
    """PyInstaller onedir puts the exe at <dist>/App.exe but everything
    from `datas=` under <dist>/_internal/ (verified against a real
    `pyinstaller build/cost_extractor.spec` build: sys._MEIPASS resolved
    to .../dist/CostExtractor/_internal, and tesseract/ landed there, not
    next to the exe). sys._MEIPASS is what PyInstaller sets to point at
    that actual data location, so it must take priority."""
    internal_dir = tmp_path / "_internal"
    internal_dir.mkdir()
    exe_dir = tmp_path  # exe sits one level up from _internal
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "CostExtractor.exe"), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir), raising=False)

    tess_dir = ocr_setup.get_tesseract_dir()

    assert tess_dir == internal_dir / "tesseract"


def test_get_tesseract_dir_when_frozen_without_meipass_falls_back_to_exe_dir(
    monkeypatch, tmp_path
):
    fake_exe = tmp_path / "CostExtractor.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    tess_dir = ocr_setup.get_tesseract_dir()

    assert tess_dir == tmp_path / "tesseract"


def test_configure_tesseract_sets_pytesseract_command(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    vendor_dir = Path(ocr_setup.__file__).resolve().parent.parent / "vendor" / "tesseract"

    config = ocr_setup.get_tessdata_config()

    assert str(vendor_dir / "tessdata") in config
    assert "--tessdata-dir" in config
