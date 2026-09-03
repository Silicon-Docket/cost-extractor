# Cost Extractor

A portable desktop app (Windows and macOS) that scans `.docx`, `.pdf`,
image scans (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`), and `.zip`
files (including nested zips) for USD dollar amounts and produces an Excel
report with per-document subtotals and a grand total.

Money formats are configurable in the app: three built-in rules (standard
`$1,234.56` amounts, `$1.5M`/`$250K` shorthand, and `($1,200.00)` accounting
negatives) can each be toggled on/off, and you can add your own custom regex
pattern as an additional rule. Scanned/image-only PDF pages and standalone
image files fall back to OCR automatically.

## Using the app

Add files or a folder (or drag-and-drop them onto the file list), tick the
money formats you want to search for, click **Run**, then **Save Report...**
to export the `.xlsx`. The preview table shows every match before you export.

A file you add that the app can't read is listed with a `SKIPPED` status and
the reason, rather than being dropped silently.

### How much to trust each amount

Amounts read from a document's text layer are exact. Amounts recovered by
OCR are guesses, and the app says so rather than blending them into the
total unannounced. Both the preview and the report carry three extra
columns per match:

- **Read As** — `text` (read directly) or `ocr` (recognised from pixels).
- **Confidence** — OCR's own 0-100 score. Blank for `text` matches, because
  nothing was guessed; that is different from a score of zero.
- **Review** — `REVIEW` when the score falls below 60, meaning the digits
  are doubtful and the amount is worth checking by eye.

The Summary sheet keeps **Grand Total** meaning the whole batch, and adds
**Of which needs review** and **Confidently read** beneath it, so you can
see how much of the headline figure rests on a doubtful reading.

**Do not treat a high confidence score as proof.** On a handwritten test
page, OCR read `$940.00` as `$440.00` and scored it 82% — comfortably above
the cutoff. The score catches obviously-doubtful readings; it does not catch
confidently-wrong ones. That is what the review pane is for.

### Checking guessed amounts by eye

Click **Review Amounts...** after a run. For each amount OCR guessed, the
pane shows a crop of the pixels it was read from, next to the reading and
its score:

- **Looks right** confirms the reading.
- Typing a different figure and clicking **Save correction** replaces it.
  `940.00`, `$1,240.50` and `($200.00)` are all understood, the same way
  they would be inside a document.
- **Note (optional)** records why, in your own words — `fixed typo`, say.
  Left blank, confirming an amount is noted as `confirmed`. Taking the
  handwriting model's reading always notes that the value came from the
  model, and a note you type is added to that rather than replacing it.

Corrections flow into the preview total and the exported report. The
original reading is kept in the report's **Read As Text** column, so a
correction reads as a correction rather than a silent rewrite, and the
Summary counts **Guessed amounts not yet checked** so you can see how much
of the total nobody has verified.

Nothing is overwritten: correcting the same amount twice keeps both
corrections. The report's **Revisions** sheet has one row per review
decision — *Source File, Location, Matched Text, Rule, Revised From,
Revised To, Timestamp, Note* — so the Details sheet says what an amount is
worth now and the Revisions sheet says how it got there. Confirming an
amount is a decision too, and gets a row of its own with **Revised From**
and **Revised To** the same.

**Revised From** is the value immediately before *that* row's decision: the
original reading on a match's first row, the previous row's **Revised To**
on every row after. An amount corrected from `$440` to `$900`, then from
`$900` to `$940`, is two rows — `440.00 → 900.00` and `900.00 → 940.00` —
not two rows both starting from `440.00`.

Every OCR-derived amount is offered for review, not just low-scoring ones —
for the reason above. The most doubtful are queued first.

> **Handwriting is never read automatically.** The bundled Tesseract model
> reads printed text. It reliably *locates* handwritten amounts but misreads
> the digits, so handwritten figures must be confirmed through the review
> pane before the total means anything.

### Optional: a second opinion on handwriting

A handwriting model (TrOCR, via ONNX Runtime) can be installed to give a
*second reading* of each crop, shown beside Tesseract's in the review pane.
It never produces a value on its own — taking its reading is a click that
goes through the same path as typing a correction yourself.

**Read this before installing it.** Measured against handwriting-style
renderings of dollar amounts, it got **5 of 20** right, and its errors
multiply rather than nudge: it reads the leading `$` as a digit, turning
`$340` into `8340`. It is not a handwriting reader you can trust. Its value
is *disagreement*: on a test page where Tesseract read `$940.00` as
`$440.00` at 82% confidence — which no threshold would catch — the two
engines disagreed, and the pane said so. Expect false alarms too; on the
same page it flagged one amount Tesseract had read correctly.

To install:

1. `pip install -r requirements-handwriting.txt` (onnxruntime + tokenizers,
   deliberately kept out of the main requirements).
2. Create `vendor/trocr/` and put three files in it, from
   [Xenova/trocr-small-handwritten](https://huggingface.co/Xenova/trocr-small-handwritten):
   `onnx/encoder_model_quantized.onnx`, `onnx/decoder_model_quantized.onnx`
   (both renamed to drop the `onnx/` prefix), and `tokenizer.json`. About
   68 MB total.

The app detects it at startup and needs no configuration. With the folder
absent — the state every released build ships in — nothing changes and the
review pane simply shows no second reading. To remove it, delete
`vendor/trocr/`.

Packaged builds do not include the model. To bundle it, add
`vendor/trocr` to the PyInstaller spec's `datas` as `trocr`, the same way
`vendor/tesseract` is handled.

### Adding a custom money-format pattern

The three built-in formats won't cover every document. In the **Money
Formats** panel, use the **Custom pattern** row to add your own. The **?**
button next to **Add** opens this same guide inside the app, with a
**Use example** button that fills in the example below so you can try it
with one click:

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
