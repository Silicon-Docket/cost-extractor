# Cost Extractor

A portable desktop app (Windows and macOS) that scans `.docx`, `.pdf`, and
`.zip` files (including nested zips) for USD dollar amounts and produces an
Excel report with per-document subtotals and a grand total.

Money formats are configurable in the app: three built-in rules (standard
`$1,234.56` amounts, `$1.5M`/`$250K` shorthand, and `($1,200.00)` accounting
negatives) can each be toggled on/off, and you can add your own custom regex
pattern as an additional rule. Scanned/image-only PDF pages fall back to
OCR automatically.

## Using the app

Add files or a folder (or drag-and-drop them onto the file list), tick the
money formats you want to search for, click **Run**, then **Save Report...**
to export the `.xlsx`. The preview table shows every match before you export.

### Adding a custom money-format pattern

The three built-in formats won't cover every document. In the **Money
Formats** panel, use the **"Add custom pattern…"** row to add your own:

1. Enter a regular expression in the pattern field. It **must** contain a
   named capture group called `amount`, e.g. `(?P<amount>\d+(?:\.\d{2})?)`.
   Patterns without this group are rejected with an inline error rather
   than being added.
2. Optionally enter a label to show next to its checkbox (defaults to
   "Custom pattern N" if left blank).
3. Click **Add**. On success, the pattern appears as its own checkbox
   (enabled by default) with a small **×** button to remove it. On
   failure — invalid regex syntax, a missing `amount` group, or a pattern
   flagged as too slow to run safely — an error message appears inline
   under the field instead of crashing the app; nothing is added.

Two more capture groups are recognized if you include them:

- `(?P<mult>...)` — matches a magnitude word/letter (`K`, `M`, `B`,
  `thousand`, `million`, `billion`, case-insensitive) and multiplies the
  amount accordingly, the same way the built-in shorthand rule does.
- `(?P<sign>...)` — if this group matches anything (or if the overall
  matched text simply contains a literal `(`), the amount is counted as
  negative — the same convention the built-in accounting-negatives rule
  uses for `($1,200.00)`.

**Example** — to also catch amounts written as `45.00 EUR` or `1.5M EUR`:

```
Pattern: (?P<amount>\d+(?:\.\d+)?)\s?(?P<mult>K|M|B)?\s?EUR
Label:   Euro
```

Custom patterns contribute their matched value as-is to the totals — there's
no currency conversion, so a pattern like the one above sums EUR figures
into the same USD-labeled report. That's intentional: custom rules are an
escape hatch for whatever your documents actually contain, not a currency
converter.

## Dev setup

```
python -m venv .venv
# Windows
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
# macOS
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

### Vendoring Tesseract OCR (one-time, not checked into git)

The OCR fallback needs a portable Tesseract-OCR install vendored locally
(gitignored — a real install is ~150-240MB, unsuitable for source control).
The setup differs by platform since there's no single portable Tesseract
distribution that works both ways.

**Windows** (`vendor/tesseract/`):

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

**macOS** (`vendor/tesseract-macos/`) — Homebrew's `tesseract` is dynamically
linked against its own dylibs, so it needs `dylibbundler` to become
redistributable outside your machine:

```bash
brew install tesseract dylibbundler
TESS_PREFIX="$(brew --prefix tesseract)"
mkdir -p vendor/tesseract-macos/tessdata
cp "$TESS_PREFIX/bin/tesseract" vendor/tesseract-macos/tesseract
cp "$TESS_PREFIX/share/tessdata/eng.traineddata" vendor/tesseract-macos/tessdata/
chmod +w vendor/tesseract-macos/tesseract
dylibbundler -od -b \
  -x vendor/tesseract-macos/tesseract \
  -d vendor/tesseract-macos/lib \
  -p "@executable_path/lib/"
```

`dylibbundler` copies every dylib the binary depends on into `lib/` and
rewrites the binary's load commands to reference them relatively, so the
result runs without Homebrew installed. Verify with
`cd /tmp && /path/to/vendor/tesseract-macos/tesseract --version` (running
from an unrelated directory rules out anything still resolving via cwd).

Tests that need OCR (`tests/test_pdf_extractor.py`'s scanned-PDF test) skip
automatically if the platform-appropriate vendored `tesseract` binary isn't
present.

## Running the tests

```
.venv\Scripts\python -m pytest   # Windows
.venv/bin/python -m pytest       # macOS
```

## Running from source

```
.venv\Scripts\python -m cost_extractor.main   # Windows
.venv/bin/python -m cost_extractor.main       # macOS
```

## Building the portable app

```bash
# Windows
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python -m PyInstaller build\cost_extractor.spec --noconfirm --clean

# macOS
.venv/bin/pip install pyinstaller
.venv/bin/python -m PyInstaller build/cost_extractor_macos.spec --noconfirm --clean
```

Windows output: `dist\CostExtractor\CostExtractor.exe` — the whole
`dist\CostExtractor` folder is portable, copy it anywhere (including a USB
drive) and run the exe directly; no installer needed. Onedir (not onefile)
is used deliberately: bundling Tesseract in a onefile build would force a
slow extract-to-temp on every launch.

macOS output: `dist/CostExtractor.app` — copy it anywhere (e.g. `/Applications`)
and double-click to run. Since it isn't code-signed or notarized (no Apple
Developer account involved in building this), Gatekeeper will refuse to
open it the first time with "cannot be opened because the developer cannot
be verified." Right-click (or Control-click) the app → **Open** → **Open**
in the confirmation dialog — this is a one-time step per machine. If that
still doesn't work, clear the quarantine flag directly:
`xattr -cr /path/to/CostExtractor.app`.

## Downloading a release

Prebuilt releases are published automatically via GitHub Actions
(`.github/workflows/release.yml`). To cut one:

```
git tag v1.0.0
git push origin v1.0.0
```

That builds the app on clean Windows and macOS runners in parallel (each
fetching and vendoring Tesseract itself, so nothing from your machine leaks
into either build), runs the full test suite on both, and publishes one
GitHub Release with two assets: `CostExtractor-v1.0.0-win64.zip` and
`CostExtractor-v1.0.0-macos.zip`. Download the one for your platform,
extract it anywhere, and run the app from inside the extracted folder (see
the Gatekeeper note above for macOS). You can also trigger a build manually
from the Actions tab ("Run workflow") without pushing a tag.
