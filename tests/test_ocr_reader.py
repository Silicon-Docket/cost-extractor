"""Turning Tesseract's word-level output into positioned tokens.

`image_to_data` reports one row per word with a box and a confidence. These
tests pin how those rows become a single text string plus tokens whose
character offsets index into it, using synthetic data so they run without a
vendored Tesseract.
"""

from cost_extractor.extractors.base import BoundingBox
from cost_extractor.ocr_reader import tokens_from_ocr_data


def _data(rows: list[dict]) -> dict:
    """Builds an image_to_data-shaped dict of parallel lists from rows."""
    keys = [
        "text",
        "conf",
        "left",
        "top",
        "width",
        "height",
        "block_num",
        "par_num",
        "line_num",
    ]
    return {k: [r.get(k, 0) for r in rows] for k in keys}


def _word(text, conf=90, left=0, top=0, width=10, height=10, line=1, block=1):
    return {
        "text": text,
        "conf": conf,
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "block_num": block,
        "par_num": 1,
        "line_num": line,
    }


def test_words_on_one_line_join_with_spaces():
    text, _ = tokens_from_ocr_data(
        _data([_word("Total", left=10), _word("$940.00", left=70)])
    )

    assert text == "Total $940.00"


def test_token_offsets_index_into_the_joined_text():
    text, tokens = tokens_from_ocr_data(
        _data([_word("Total", left=10), _word("$940.00", left=70)])
    )

    amount = tokens[1]
    assert text[amount.start : amount.end] == "$940.00"


def test_token_carries_its_box_and_confidence():
    _, tokens = tokens_from_ocr_data(
        _data([_word("$940.00", conf=54, left=70, top=20, width=80, height=12)])
    )

    assert tokens[0].bbox == BoundingBox(70, 20, 80, 12)
    assert tokens[0].confidence == 54.0


def test_a_new_line_becomes_a_newline_not_a_space():
    text, _ = tokens_from_ocr_data(
        _data([_word("Invoice", line=1), _word("Total", line=2)])
    )

    assert text == "Invoice\nTotal"


def test_a_new_block_also_breaks_the_line():
    text, _ = tokens_from_ocr_data(
        _data([_word("Header", block=1), _word("Body", block=2)])
    )

    assert text == "Header\nBody"


def test_layout_rows_without_text_are_dropped():
    # image_to_data emits page/block/paragraph/line rows too; they carry
    # conf -1 and empty text and must not become tokens or stray spaces.
    text, tokens = tokens_from_ocr_data(
        _data([_word("", conf=-1), _word("Total"), _word("   ", conf=-1)])
    )

    assert text == "Total"
    assert len(tokens) == 1


def test_unrecognised_words_are_dropped():
    # conf -1 on a row that does have text means Tesseract found ink it
    # could not read; it has no reading to contribute.
    text, tokens = tokens_from_ocr_data(_data([_word("Total"), _word("~~", conf=-1)]))

    assert text == "Total"
    assert len(tokens) == 1


def test_confidence_arrives_as_a_float_even_when_reported_as_a_string():
    # Tesseract's conf column comes back as strings in some pytesseract
    # versions; comparing those to a numeric threshold would raise.
    _, tokens = tokens_from_ocr_data(_data([_word("Total", conf="87")]))

    assert tokens[0].confidence == 87.0


def test_empty_data_yields_empty_text_and_no_tokens():
    text, tokens = tokens_from_ocr_data(_data([]))

    assert text == ""
    assert tokens == []


def test_reading_recovers_when_something_else_repointed_pytesseract(
    scan_image, skip_if_no_tesseract
):
    # `tesseract_cmd` is a module global on pytesseract that any other code
    # (or test) can overwrite. Caching an "already configured" flag once
    # turned a stale value into a permanently broken OCR path, and the
    # breakage only appeared once an unrelated test file changed the order
    # things ran in.
    import pytesseract
    from PIL import Image

    from cost_extractor.ocr_reader import read_image

    pytesseract.pytesseract.tesseract_cmd = r"C:\nonexistent\tesseract.exe"

    with Image.open(scan_image) as image:
        text, tokens = read_image(image)

    assert "2,345.00" in text
    assert tokens
