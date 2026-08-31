# PyInstaller spec for a portable onedir build. Build from the repo root:
#   pyinstaller build/cost_extractor.spec --noconfirm --clean

import os

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))

datas = [(os.path.join(PROJECT_ROOT, "vendor", "tesseract"), "tesseract")]
datas += collect_data_files("tkinterdnd2")
datas += collect_data_files("pypdfium2")

# python311.dll depends on VCRUNTIME140_1.dll and (transitively, via other
# bundled extensions) MSVCP140.dll, but PyInstaller's dependency walker
# doesn't always pick these up automatically. Bundle them explicitly from
# the build machine's System32 if present there, so a target machine
# without the VC++ 2015-2022 Redistributable installed still has them.
binaries = []
system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
for _dll_name in ("VCRUNTIME140_1.dll", "MSVCP140.dll"):
    _dll_path = os.path.join(system32, _dll_name)
    if os.path.exists(_dll_path):
        binaries.append((_dll_path, "."))

a = Analysis(
    [os.path.join(PROJECT_ROOT, "cost_extractor", "main.py")],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
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
