import zipfile
from pathlib import Path

import pytest

from cost_extractor.extractors.base import Status
from cost_extractor.ingestion import discover_files, temp_workspace


def _make_docx_stub(path: Path) -> None:
    # Content doesn't matter for ingestion-level tests (discovery vs.
    # extraction is a separate concern) — just needs to exist with the
    # right suffix.
    path.write_bytes(b"stub docx bytes")


def _make_zip(zip_path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def test_temp_workspace_cleans_up_on_normal_exit():
    with temp_workspace() as workspace:
        (workspace / "leftover.txt").write_text("data")
        assert workspace.exists()

    assert not workspace.exists()


def test_temp_workspace_cleans_up_even_on_exception():
    captured_path = None
    with pytest.raises(RuntimeError):
        with temp_workspace() as workspace:
            captured_path = workspace
            raise RuntimeError("boom")

    assert not captured_path.exists()


def test_discover_files_finds_direct_docx_and_pdf(tmp_path):
    a = tmp_path / "a.docx"
    b = tmp_path / "b.pdf"
    _make_docx_stub(a)
    _make_docx_stub(b)

    with temp_workspace() as workspace:
        found = discover_files([a, b], workspace)

    names = sorted(f.display_name for f in found)
    assert names == ["a.docx", "b.pdf"]
    assert all(f.status is None for f in found)


def test_discover_files_expands_a_folder_recursively(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _make_docx_stub(tmp_path / "top.docx")
    _make_docx_stub(sub / "nested.pdf")

    with temp_workspace() as workspace:
        found = discover_files([tmp_path], workspace)

    names = sorted(f.display_name for f in found)
    assert names == ["nested.pdf", "top.docx"]


def test_discover_files_reports_an_explicitly_listed_unsupported_file_as_skipped(
    tmp_path,
):
    # A file the user named (picked or dragged in) is visible in the GUI's
    # file list, so it must never vanish from the run without explanation.
    unreadable = tmp_path / "scan_notes.heic"
    unreadable.write_bytes(b"stub heic bytes")
    supported = tmp_path / "invoice.pdf"
    _make_docx_stub(supported)

    with temp_workspace() as workspace:
        found = discover_files([unreadable, supported], workspace)

    skipped = [f for f in found if f.status == Status.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].display_name == "scan_notes.heic"
    # The row has to say why, or it is still unexplained disappearance.
    assert ".heic" in skipped[0].message

    ok = [f for f in found if f.status is None]
    assert [f.display_name for f in ok] == ["invoice.pdf"]


def test_discover_files_stays_quiet_about_unsupported_files_swept_from_a_folder(
    tmp_path,
):
    # Boundary guard, not a bug report: folder expansion is understood as a
    # filter and its members were never individually listed in the GUI, so
    # sweeping a directory must not emit a row per unrelated file.
    _make_docx_stub(tmp_path / "a.docx")
    (tmp_path / "notes.txt").write_bytes(b"hello")
    (tmp_path / "clip.mov").write_bytes(b"stub movie bytes")

    with temp_workspace() as workspace:
        found = discover_files([tmp_path], workspace)

    assert [f.display_name for f in found] == ["a.docx"]


def test_discover_files_extracts_simple_zip(tmp_path):
    zip_path = tmp_path / "simple.zip"
    _make_zip(zip_path, {"a.docx": b"stub", "b.pdf": b"stub"})

    with temp_workspace() as workspace:
        found = discover_files([zip_path], workspace)

        names = sorted(f.display_name for f in found)
        assert names == ["simple.zip > a.docx", "simple.zip > b.pdf"]
        assert all(f.status is None for f in found)
        assert all(f.path.exists() for f in found)


def test_discover_files_extracts_nested_zip_with_lineage(tmp_path):
    inner_zip_bytes_holder = tmp_path / "_inner.zip"
    _make_zip(inner_zip_bytes_holder, {"report.pdf": b"stub"})
    outer_zip = tmp_path / "outer.zip"
    _make_zip(outer_zip, {"inner.zip": inner_zip_bytes_holder.read_bytes()})

    with temp_workspace() as workspace:
        found = discover_files([outer_zip], workspace)

    assert len(found) == 1
    assert found[0].display_name == "outer.zip > inner.zip > report.pdf"
    assert found[0].status is None


def test_discover_files_skips_unsupported_type_inside_zip(tmp_path):
    zip_path = tmp_path / "mixed.zip"
    _make_zip(zip_path, {"a.docx": b"stub", "notes.txt": b"hello"})

    with temp_workspace() as workspace:
        found = discover_files([zip_path], workspace)

    skipped = [f for f in found if f.status == Status.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].display_name == "mixed.zip > notes.txt"
    # Same explanation an unsupported top-level file gets: a blank reason
    # in the report's Message column is the very gap this closes.
    assert ".txt" in skipped[0].message

    ok = [f for f in found if f.status is None]
    assert len(ok) == 1
    assert ok[0].display_name == "mixed.zip > a.docx"


def test_discover_files_rejects_recursion_past_max_depth(tmp_path):
    # Build a chain of 7 nested zips (deeper than MAX_ZIP_DEPTH=5).
    current = tmp_path / "level6.zip"
    _make_zip(current, {"payload.pdf": b"stub"})
    for level in range(5, -1, -1):
        parent = tmp_path / f"level{level}.zip"
        _make_zip(parent, {f"level{level + 1}.zip": current.read_bytes()})
        current = parent

    with temp_workspace() as workspace:
        found = discover_files([current], workspace)

    errors = [f for f in found if f.status == Status.ERROR]
    assert len(errors) == 1
    assert "depth" in errors[0].message.lower()

    # The real payload past the depth limit must never be surfaced as OK.
    assert not any(f.status is None for f in found)


def test_discover_files_rejects_oversized_archive(tmp_path):
    # A real (not spoofed) zip-bomb-style entry: highly compressible zero
    # bytes streamed in chunks so the on-disk .zip stays tiny while the
    # declared uncompressed size genuinely exceeds the 2 GB cap.
    zip_path = tmp_path / "huge.zip"
    chunk = b"\x00" * (10 * 1024 * 1024)
    target_bytes = 2 * 1024**3 + 100 * 1024 * 1024  # 2 GB cap + 100 MB over
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        with zf.open("huge.pdf", "w", force_zip64=True) as member:
            written = 0
            while written < target_bytes:
                member.write(chunk)
                written += len(chunk)

    with temp_workspace() as workspace:
        found = discover_files([zip_path], workspace)

    errors = [f for f in found if f.status == Status.ERROR]
    assert len(errors) == 1
    assert "size" in errors[0].message.lower()


def test_discover_files_rejects_archive_with_too_many_members(tmp_path):
    zip_path = tmp_path / "manyfiles.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(5):
            zf.writestr(f"file{i}.pdf", b"x")

    with temp_workspace() as workspace, pytest.MonkeyPatch.context() as mp:
        mp.setattr("cost_extractor.ingestion.MAX_ARCHIVE_MEMBER_COUNT", 3)
        found = discover_files([zip_path], workspace)

    errors = [f for f in found if f.status == Status.ERROR]
    assert len(errors) == 1
    assert "member" in errors[0].message.lower() or "count" in errors[0].message.lower()
