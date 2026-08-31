import sys
from pathlib import Path

import pytest

from cost_extractor import ocr_setup


@pytest.mark.parametrize(
    "platform,subdir",
    [("win32", "tesseract"), ("darwin", "tesseract-macos")],
)
def test_get_tesseract_dir_in_dev_mode_resolves_to_project_root_vendor(
    monkeypatch, platform, subdir
):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    project_root = Path(ocr_setup.__file__).resolve().parent.parent

    tess_dir = ocr_setup.get_tesseract_dir()

    assert tess_dir == project_root / "vendor" / subdir


@pytest.mark.parametrize(
    "platform,subdir",
    [("win32", "tesseract"), ("darwin", "tesseract-macos")],
)
def test_get_tesseract_dir_when_frozen_uses_meipass(
    monkeypatch, tmp_path, platform, subdir
):
    """PyInstaller onedir puts the exe at <dist>/App(.exe) but everything
    from `datas=` under <dist>/_internal/ on every platform (verified on
    Windows against a real `pyinstaller build/cost_extractor.spec` build:
    sys._MEIPASS resolved to .../dist/CostExtractor/_internal, and
    tesseract/ landed there, not next to the exe). sys._MEIPASS is what
    PyInstaller sets to point at that actual data location, so it must
    take priority regardless of OS."""
    internal_dir = tmp_path / "_internal"
    internal_dir.mkdir()
    exe_dir = tmp_path  # exe sits one level up from _internal
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "CostExtractor"), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir), raising=False)

    tess_dir = ocr_setup.get_tesseract_dir()

    assert tess_dir == internal_dir / subdir


def test_get_tesseract_dir_when_frozen_without_meipass_falls_back_to_exe_dir(
    monkeypatch, tmp_path
):
    fake_exe = tmp_path / "CostExtractor.exe"
    fake_exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    tess_dir = ocr_setup.get_tesseract_dir()

    assert tess_dir == tmp_path / "tesseract"


@pytest.mark.parametrize(
    "platform,expected_name",
    [("win32", "tesseract.exe"), ("darwin", "tesseract"), ("linux", "tesseract")],
)
def test_get_tesseract_executable_name_is_platform_specific(
    monkeypatch, platform, expected_name
):
    monkeypatch.setattr(sys, "platform", platform)

    assert ocr_setup.get_tesseract_executable_name() == expected_name


@pytest.mark.parametrize(
    "platform,subdir",
    [("win32", "tesseract"), ("darwin", "tesseract-macos")],
)
def test_get_tessdata_config_uses_platform_specific_vendor_dir(
    monkeypatch, platform, subdir
):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    vendor_dir = Path(ocr_setup.__file__).resolve().parent.parent / "vendor" / subdir

    config = ocr_setup.get_tessdata_config()

    assert str(vendor_dir / "tessdata") in config
    assert "--tessdata-dir" in config


def test_get_tessdata_config_quotes_path_on_posix_but_not_windows(monkeypatch):
    """pytesseract splits this config string via
    shlex.split(config, posix=(platform != 'win32')). POSIX-mode shlex
    correctly strips quotes, so macOS/Linux should get a quoted path
    (safe if it ever contains a space); Windows's non-POSIX mode does
    NOT strip quotes, so it must stay unquoted (verified against a real
    tesseract run — a quoted path there breaks with a literal quote
    character stuck in the filename)."""
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    monkeypatch.setattr(sys, "platform", "darwin")
    macos_config = ocr_setup.get_tessdata_config()
    assert macos_config.count('"') == 2 or macos_config.count("'") == 2

    monkeypatch.setattr(sys, "platform", "win32")
    windows_config = ocr_setup.get_tessdata_config()
    assert '"' not in windows_config
    assert "'" not in windows_config


def test_configure_pytesseract_uses_platform_specific_executable_name(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        ocr_setup,
        "get_tesseract_dir",
        lambda: tmp_path,
    )
    import pytesseract

    ocr_setup.configure_pytesseract()

    assert pytesseract.pytesseract.tesseract_cmd == str(tmp_path / "tesseract")
