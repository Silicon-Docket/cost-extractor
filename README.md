# Cost Extractor

A portable Windows desktop app that scans `.docx`, `.pdf`, and `.zip` files
(including nested zips) for USD dollar amounts and produces an Excel report
with per-document subtotals and a grand total.

Money formats are configurable in the app: three built-in rules (standard
`$1,234.56` amounts, `$1.5M`/`$250K` shorthand, and `($1,200.00)` accounting
negatives) can each be toggled on/off, and you can add your own custom regex
pattern as an additional rule. Scanned/image-only PDF pages fall back to
OCR automatically.

## Dev setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
```

### Vendoring Tesseract OCR (one-time, not checked into git)

The OCR fallback needs a portable Tesseract-OCR install at
`vendor/tesseract/` (gitignored — a real install is ~150-240MB before/after
pruning, unsuitable for source control). To set it up:

1. Install the exact build the app expects:
   `winget install --id UB-Mannheim.TesseractOCR` (or download
   `tesseract-ocr-w64-setup-*.exe` from
   https://github.com/UB-Mannheim/tesseract/releases and extract it with
   `7z x tesseract-ocr-w64-setup-*.exe` instead of running the installer).
2. Copy the resulting folder (normally `C:\Program Files\Tesseract-OCR`)
   to `vendor/tesseract/` in this repo.
3. If you installed via winget, you can uninstall the system copy
   afterward (`winget uninstall --id UB-Mannheim.TesseractOCR`) — only the
   vendored copy is used at runtime.
4. Optional: prune training tools, `.html` docs, `.jar` files, and
   `tessdata/osd.traineddata` (not needed for text OCR) to shrink the
   vendored folder — `tesseract.exe`, its DLLs, and `tessdata/eng.traineddata`
   are what's actually used. Do this only after confirming a build works
   with the full install; verify again afterward.

Tests that need OCR (`tests/test_pdf_extractor.py`'s scanned-PDF test) skip
automatically if `vendor/tesseract/tesseract.exe` isn't present.

## Running the tests

```
.venv\Scripts\python -m pytest
```

## Running from source

```
.venv\Scripts\python -m cost_extractor.main
```

## Building the portable app

```
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python -m PyInstaller build\cost_extractor.spec --noconfirm --clean
```

Output: `dist\CostExtractor\CostExtractor.exe`. The whole `dist\CostExtractor`
folder is portable — copy it anywhere (including a USB drive) and run the
exe directly; no installer is needed. Onedir (not onefile) is used
deliberately: bundling Tesseract in a onefile build would force a slow
extract-to-temp on every launch.

## Downloading a release

Prebuilt releases are published automatically via GitHub Actions
(`.github/workflows/release.yml`). To cut one:

```
git tag v1.0.0
git push origin v1.0.0
```

That builds the app on a clean Windows runner (fetching and vendoring
Tesseract itself, so nothing from your machine leaks into the build),
runs the full test suite, zips the portable folder, and publishes it as
a GitHub Release asset named `CostExtractor-v1.0.0-win64.zip`. Download
that zip, extract it anywhere, and run `CostExtractor.exe` from inside
the extracted folder. You can also trigger a build manually from the
Actions tab ("Run workflow") without pushing a tag.
