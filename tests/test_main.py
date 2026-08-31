from decimal import Decimal
from pathlib import Path

from cost_extractor.main import _run_selftest


def test_selftest_writes_output_file_with_grand_total(simple_docx, tmp_path):
    out_path = tmp_path / "out.txt"

    result_path = _run_selftest(simple_docx, out_path)

    assert result_path == out_path
    content = out_path.read_text()
    assert "grand_total=1734.56" in content
    assert "simple.docx" in content
    assert "status=OK" in content


def test_selftest_defaults_output_next_to_target(simple_docx):
    result_path = _run_selftest(simple_docx)

    assert result_path == simple_docx.parent / "selftest_output.txt"
    assert result_path.exists()


def test_selftest_reports_tesseract_dir_and_frozen_state(simple_docx, tmp_path):
    out_path = tmp_path / "out.txt"

    _run_selftest(simple_docx, out_path)

    content = out_path.read_text()
    assert "frozen=" in content
    assert "tesseract_dir=" in content
    assert "tesseract_exe_exists=" in content
