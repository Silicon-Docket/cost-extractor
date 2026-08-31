import threading
import zipfile
from decimal import Decimal

from cost_extractor.extractors.base import Status
from cost_extractor.money_parser import default_rules
from cost_extractor.pipeline import run_pipeline


def test_run_pipeline_computes_subtotal_for_single_docx(simple_docx):
    result = run_pipeline([simple_docx], default_rules())

    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.status == Status.OK
    assert doc.subtotal == Decimal("1734.56")  # $1,234.56 + $500
    assert result.grand_total == Decimal("1734.56")


def test_run_pipeline_sums_grand_total_across_multiple_files(simple_docx, text_pdf):
    result = run_pipeline([simple_docx, text_pdf], default_rules())

    assert len(result.documents) == 2
    expected_grand_total = Decimal("1734.56") + Decimal("1234.56")
    assert result.grand_total == expected_grand_total


def test_run_pipeline_records_error_and_continues_batch(corrupt_docx, simple_docx):
    result = run_pipeline([corrupt_docx, simple_docx], default_rules())

    by_status = {doc.status for doc in result.documents}
    assert Status.ERROR in by_status
    assert Status.OK in by_status

    error_doc = next(d for d in result.documents if d.status == Status.ERROR)
    assert error_doc.subtotal == Decimal("0")
    assert error_doc.message is not None

    # The batch must still process the valid file and count it correctly.
    assert result.grand_total == Decimal("1734.56")


def test_run_pipeline_surfaces_skipped_entries_from_zip(tmp_path):
    zip_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("notes.txt", "hello")

    result = run_pipeline([zip_path], default_rules())

    assert len(result.documents) == 1
    assert result.documents[0].status == Status.SKIPPED


def test_run_pipeline_respects_disabled_rules(simple_docx):
    rules = [r for r in default_rules() if r.id != "standard"]

    result = run_pipeline([simple_docx], rules)

    # With `standard` disabled, neither $1,234.56 nor $500 match anything.
    assert result.grand_total == Decimal("0")


def test_run_pipeline_match_records_carry_source_and_location(simple_docx):
    result = run_pipeline([simple_docx], default_rules())

    doc = result.documents[0]
    assert all(m.display_name == "simple.docx" for m in doc.matches)
    assert any("paragraph" in m.location.lower() for m in doc.matches)
    assert any("table" in m.location.lower() for m in doc.matches)


def test_run_pipeline_stops_early_when_cancelled(simple_docx, text_pdf):
    cancel_flag = threading.Event()
    cancel_flag.set()

    result = run_pipeline([simple_docx, text_pdf], default_rules(), cancel_flag=cancel_flag)

    assert len(result.documents) == 0


def test_run_pipeline_invokes_progress_callback_per_file(simple_docx, text_pdf):
    seen = []

    run_pipeline([simple_docx, text_pdf], default_rules(), progress_cb=seen.append)

    assert sorted(seen) == ["simple.docx", "text.pdf"]
