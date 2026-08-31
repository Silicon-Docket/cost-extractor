# PyInstaller spec for a portable onedir build. Build from the repo root:
#   pyinstaller build/cost_extractor.spec --noconfirm --clean

import os

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))

datas = [(os.path.join(PROJECT_ROOT, "vendor", "tesseract"), "tesseract")]
datas += collect_data_files("tkinterdnd2")
datas += collect_data_files("pypdfium2")

a = Analysis(
    [os.path.join(PROJECT_ROOT, "cost_extractor", "main.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CostExtractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="CostExtractor",
)
