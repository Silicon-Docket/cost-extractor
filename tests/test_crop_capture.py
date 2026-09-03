"""Every guessed amount keeps a picture of what it was guessed from.

OCR confidence does not reliably separate good readings from bad (Tesseract
read $940.00 as $440.00 at 84%), so the only trustworthy check is a human
looking at the pixels. That requires the crop to survive the run.
"""

import io

from PIL import Image

from cost_extractor.money_parser import default_rules
from cost_extractor.pipeline import run_pipeline


def _matches(result):
    return [m for doc in result.documents for m in doc.matches]


def test_an_ocr_match_keeps_a_crop_of_where_it_was_read(
    scan_image, skip_if_no_tesseract
):
    result = run_pipeline([scan_image], default_rules())

    match = _matches(result)[0]
    assert match.crop_png, "an OCR-derived amount must keep its evidence"
    crop = Image.open(io.BytesIO(match.crop_png))
    assert crop.width > 0 and crop.height > 0


def test_the_crop_is_the_region_the_amount_was_read_from(
    scan_image, skip_if_no_tesseract
):
    # Not the whole page: the point is to show the amount, closely.
    result = run_pipeline([scan_image], default_rules())

    match = _matches(result)[0]
    crop = Image.open(io.BytesIO(match.crop_png))
    full = Image.open(scan_image)
    assert crop.width < full.width
    # Roughly the matched box, plus the margin that makes it legible.
    assert crop.width >= match.bbox.width


def test_a_text_layer_match_keeps_no_crop(simple_docx):
    # Nothing was guessed, so there is nothing to check by eye.
    result = run_pipeline([simple_docx], default_rules())

    assert all(m.crop_png is None for m in _matches(result))


def test_amounts_inside_a_zip_still_keep_their_crop(tmp_path, scan_image):
    # The extracted member lives in a temp workspace that is deleted when
    # the run ends, so the crop has to be taken while it is still alive.
    import zipfile

    zip_path = tmp_path / "scans.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(scan_image, "receipt.png")

    result = run_pipeline([zip_path], default_rules())

    matches = _matches(result)
    assert matches, "expected the zipped scan to be read"
    assert all(m.crop_png for m in matches)
