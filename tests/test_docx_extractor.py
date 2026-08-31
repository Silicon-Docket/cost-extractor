from cost_extractor.extractors.base import Status
from cost_extractor.extractors.docx_extractor import extract


def test_extract_returns_ok_status_for_valid_docx(simple_docx):
    result = extract(simple_docx)

    assert result.status == Status.OK


def test_extract_captures_paragraph_text(simple_docx):
    result = extract(simple_docx)

    texts = [seg.text for seg in result.segments]
    assert any("$1,234.56" in t for t in texts)


def test_extract_captures_table_cell_text(simple_docx):
    result = extract(simple_docx)

    texts = [seg.text for seg in result.segments]
    assert any("$500" in t for t in texts)


def test_extract_segment_locations_are_descriptive(simple_docx):
    result = extract(simple_docx)

    money_segment = next(seg for seg in result.segments if "$1,234.56" in seg.text)
    assert "paragraph" in money_segment.location.lower()

    table_segment = next(seg for seg in result.segments if "$500" in seg.text)
    assert "table" in table_segment.location.lower()


def test_extract_corrupt_docx_returns_error_status(corrupt_docx):
    result = extract(corrupt_docx)

    assert result.status == Status.ERROR
    assert result.error_message is not None
    assert "corrupt" in result.error_message.lower()


def test_extract_password_protected_docx_returns_distinct_error(password_protected_docx):
    result = extract(password_protected_docx)

    assert result.status == Status.ERROR
    assert "password" in result.error_message.lower()
