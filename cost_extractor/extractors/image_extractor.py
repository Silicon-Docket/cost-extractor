"""Extracts text from a standalone image (a photographed or scanned page).

There is no text layer to try first: OCR is the only path, so an image with
OCR disabled simply yields nothing rather than erroring.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from cost_extractor import ocr_reader
from cost_extractor.extractors.base import ExtractionResult, Status, TextSegment

# The file already IS the bitmap OCR reads, so boxes are in its native
# pixels and a crop means reopening it at full size.
_NATIVE_SCALE = 1.0


def _reopen(path: Path):
    with Image.open(path) as img:
        return img.convert("RGB")


def extract(path: Path, ocr_enabled: bool = True) -> ExtractionResult:
    if not ocr_enabled:
        return ExtractionResult(status=Status.OK)

    try:
        with Image.open(path) as image:
            image.load()
            text, tokens = ocr_reader.read_image(image)
    except (UnidentifiedImageError, OSError, ValueError) as e:
        return ExtractionResult(
            status=Status.ERROR, error_message=f"corrupt or unreadable image: {e}"
        )
    except Exception as e:  # noqa: BLE001 - OCR failure is the document's error
        return ExtractionResult(
            status=Status.ERROR, error_message=f"OCR failed: {e}"
        )

    if not text:
        return ExtractionResult(status=Status.OK)

    return ExtractionResult(
        status=Status.OK,
        segments=[
            TextSegment(
                text=text,
                location="image",
                provenance="ocr",
                tokens=tokens,
                render_scale=_NATIVE_SCALE,
                # Reopened on demand rather than held: the file is the
                # bitmap, so there is nothing to re-render.
                page_image=partial(_reopen, Path(path)),
            )
        ],
    )
