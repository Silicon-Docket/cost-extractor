"""File discovery + recursive, hardened zip extraction into a temp workspace."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from cost_extractor.extractors.base import Status

SUPPORTED_SUFFIXES = {".docx", ".pdf"}
ARCHIVE_SUFFIX = ".zip"

MAX_ZIP_DEPTH = 5
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024**3  # 2 GB
MAX_ARCHIVE_MEMBER_COUNT = 50_000


@dataclass
class DiscoveredFile:
    display_name: str
    path: Optional[Path] = None
    suffix: str = ""
    status: Optional[Status] = None  # None = needs extraction; else already resolved
    message: Optional[str] = None


@contextmanager
def temp_workspace() -> Iterator[Path]:
    d = tempfile.mkdtemp(prefix="cx_")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _iter_input_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.is_file()))
        else:
            files.append(p)
    return files


def _check_archive_caps(zf: zipfile.ZipFile, display_name: str) -> Optional[str]:
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBER_COUNT:
        return (
            f"archive exceeded member count limit "
            f"({len(infos)} > {MAX_ARCHIVE_MEMBER_COUNT}): {display_name}"
        )
    total = sum(info.file_size for info in infos)
    if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        return (
            f"archive exceeded uncompressed size limit "
            f"({total} > {MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes): {display_name}"
        )
    return None


def _extract_zip(
    zip_path: Path,
    display_prefix: str,
    workspace: Path,
    depth: int,
    out: list[DiscoveredFile],
) -> None:
    if depth > MAX_ZIP_DEPTH:
        out.append(
            DiscoveredFile(
                display_name=display_prefix,
                status=Status.ERROR,
                message=f"archive exceeded max recursion depth of {MAX_ZIP_DEPTH}",
            )
        )
        return

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        out.append(
            DiscoveredFile(
                display_name=display_prefix,
                status=Status.ERROR,
                message=f"corrupt or unreadable archive: {e}",
            )
        )
        return

    with zf:
        cap_error = _check_archive_caps(zf, display_prefix)
        if cap_error:
            out.append(
                DiscoveredFile(
                    display_name=display_prefix, status=Status.ERROR, message=cap_error
                )
            )
            return

        extract_dir = Path(tempfile.mkdtemp(dir=workspace))
        for i, info in enumerate(zf.infolist()):
            if info.is_dir():
                continue
            member_name = Path(info.filename).name
            display_name = f"{display_prefix} > {member_name}"
            suffix = Path(member_name).suffix.lower()

            if suffix not in SUPPORTED_SUFFIXES and suffix != ARCHIVE_SUFFIX:
                out.append(
                    DiscoveredFile(display_name=display_name, status=Status.SKIPPED)
                )
                continue

            dest = extract_dir / f"{i}{suffix}"
            with zf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)

            if suffix == ARCHIVE_SUFFIX:
                _extract_zip(dest, display_name, workspace, depth + 1, out)
            else:
                out.append(
                    DiscoveredFile(
                        display_name=display_name, path=dest, suffix=suffix
                    )
                )


def discover_files(paths: list[Path], workspace: Path) -> list[DiscoveredFile]:
    found: list[DiscoveredFile] = []
    for path in _iter_input_files(paths):
        suffix = path.suffix.lower()
        if suffix in SUPPORTED_SUFFIXES:
            found.append(
                DiscoveredFile(display_name=path.name, path=path, suffix=suffix)
            )
        elif suffix == ARCHIVE_SUFFIX:
            _extract_zip(path, path.name, workspace, depth=1, out=found)
    return found
