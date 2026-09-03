"""OCR of a single bitmap into positioned, confidence-scored tokens.

Shared by the PDF and image extractors. Uses `image_to_data` rather than
`image_to_string`: the same recognition pass, but it also reports where each
word sat and how sure Tesseract was, which is what lets a money match be
traced back to a crop of the page for review.
"""

from __future__ import annotations

from typing import Any

import pytesseract
from pytesseract import Output

from cost_extractor import ocr_setup
from cost_extractor.extractors.base import BoundingBox, PositionedToken

def _as_float(value: Any) -> float:
    # pytesseract reports `conf` as ints in some versions and strings in
    # others; a string would blow up the numeric comparison below.
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def tokens_from_ocr_data(data: dict) -> tuple[str, list[PositionedToken]]:
    """Flattens `image_to_data` output into one string plus its tokens.

    Rows with no readable text are dropped: `image_to_data` emits a row per
    layout level (page, block, paragraph, line) as well as per word, and
    those carry conf -1 with empty text. A conf of -1 on a row that *does*
    have text means Tesseract located ink it could not read — real
    information for a detector, but it contributes no reading here.

    Token `start`/`end` are offsets into the returned string, so a regex
    match over that string maps directly onto the tokens it consumed.
    """
    parts: list[str] = []
    tokens: list[PositionedToken] = []
    cursor = 0
    previous_line: tuple[int, int, int] | None = None

    for i, raw_text in enumerate(data["text"]):
        word = (raw_text or "").strip()
        if not word:
            continue
        confidence = _as_float(data["conf"][i])
        if confidence < 0:
            continue

        line = (
            int(data["block_num"][i]),
            int(data["par_num"][i]),
            int(data["line_num"][i]),
        )
        if previous_line is not None:
            separator = " " if line == previous_line else "\n"
            parts.append(separator)
            cursor += len(separator)
        previous_line = line

        start = cursor
        parts.append(word)
        cursor += len(word)

        tokens.append(
            PositionedToken(
                text=word,
                start=start,
                end=cursor,
                bbox=BoundingBox(
                    left=int(data["left"][i]),
                    top=int(data["top"][i]),
                    width=int(data["width"][i]),
                    height=int(data["height"][i]),
                ),
                confidence=confidence,
            )
        )

    return "".join(parts), tokens


def read_image(image) -> tuple[str, list[PositionedToken]]:
    """OCRs a PIL image into text plus positioned tokens.

    Reconfigures pytesseract on every call rather than caching a "already
    configured" flag. `tesseract_cmd` is a global on the pytesseract module
    that anything else can overwrite, so a cache turns a stale value into a
    permanently broken OCR path; the call it saves is one string assignment.
    """
    ocr_setup.configure_pytesseract()
    data = pytesseract.image_to_data(
        image,
        config=ocr_setup.get_tessdata_config(),
        output_type=Output.DICT,
    )
    return tokens_from_ocr_data(data)
