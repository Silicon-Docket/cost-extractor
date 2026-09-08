# PyInstaller spec for a portable macOS .app bundle. Build from the repo root:
#   pyinstaller build/cost_extractor_macos.spec --noconfirm --clean

import os
import re

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))

# The git tag is this project's only source of version truth -- nothing in
# the source tree carries a version string -- so the release workflow
# normalizes the tag once and passes it in here (e.g. "1.3.1"), then re-reads
# the built Info.plist and fails the release if the two disagree.
#
# Unset means a local dev build, which reports 0.0.0. Set-but-unparseable
# means the wiring is broken, so fail loudly instead: stamping the dev marker
# on what might be a release is the failure this whole path exists to stop.
# CFBundleShortVersionString must be one to three period-separated integers --
# macOS refuses a bundle whose entry is malformed or not a string.
_raw_version = os.environ.get("COST_EXTRACTOR_VERSION", "").strip()
if not _raw_version:
    BUNDLE_VERSION = "0.0.0"
else:
    _version_match = re.match(r"v?(\d+(?:\.\d+){0,2})", _raw_version)
    if _version_match is None:
        raise SystemExit(
            "COST_EXTRACTOR_VERSION=%r has no leading N[.N[.N]] version to "
            "stamp into the app bundle" % _raw_version
        )
    BUNDLE_VERSION = _version_match.group(1)

datas = [(os.path.join(PROJECT_ROOT, "vendor", "tesseract-macos"), "tesseract-macos")]
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

app = BUNDLE(
    coll,
    name="CostExtractor.app",
    icon=None,
    bundle_identifier="com.costextractor.app",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleShortVersionString": BUNDLE_VERSION,
    },
)
