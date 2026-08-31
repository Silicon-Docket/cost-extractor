"""Orchestrates ingestion -> extraction -> money parsing -> aggregation."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from cost_extractor.extractors import docx_extractor, pdf_extractor
from cost_extractor.extractors.base import ExtractionResult, Status
from cost_extractor.ingestion import DiscoveredFile, discover_files, temp_workspace
from cost_extractor.money_parser import MoneyFormatRule, find_money_matches


@dataclass
class MatchRecord:
    display_name: str
    location: str
    raw_text: str
    rule_id: str
    value: Decimal


@dataclass
class DocumentResult:
    display_name: str
    status: Status
    message: Optional[str] = None
    matches: list[MatchRecord] = field(default_factory=list)
    subtotal: Decimal = Decimal("0")


@dataclass
class PipelineResult:
    documents: list[DocumentResult]
    grand_total: Decimal


def _extract(discovered: DiscoveredFile, ocr_enabled: bool) -> ExtractionResult:
    if discovered.suffix == ".docx":
        return docx_extractor.extract(discovered.path)
    if discovered.suffix == ".pdf":
        return pdf_extractor.extract(discovered.path, ocr_enabled=ocr_enabled)
    return ExtractionResult(
        status=Status.ERROR, error_message=f"unsupported file type: {discovered.suffix}"
    )


def _process_single_file(
    discovered: DiscoveredFile, rules: list[MoneyFormatRule], ocr_enabled: bool
) -> DocumentResult:
    if discovered.status is not None:
        # Already resolved by ingestion (SKIPPED or ERROR) — nothing to extract.
        return DocumentResult(
            display_name=discovered.display_name,
            status=discovered.status,
            message=discovered.message,
        )

    try:
        extraction = _extract(discovered, ocr_enabled)
    except Exception as e:  # noqa: BLE001 - one bad file must not fail the batch
        return DocumentResult(
            display_name=discovered.display_name,
            status=Status.ERROR,
            message=str(e),
        )

    if extraction.status == Status.ERROR:
        return DocumentResult(
            display_name=discovered.display_name,
            status=Status.ERROR,
            message=extraction.error_message,
        )

    matches: list[MatchRecord] = []
    for segment in extraction.segments:
        for m in find_money_matches(segment.text, rules):
            matches.append(
                MatchRecord(
                    display_name=discovered.display_name,
                    location=segment.location,
                    raw_text=m.raw_text,
                    rule_id=m.rule_id,
                    value=m.value,
                )
            )

    subtotal = sum((m.value for m in matches), Decimal("0"))
    return DocumentResult(
        display_name=discovered.display_name,
        status=extraction.status,
        message=extraction.error_message,
        matches=matches,
        subtotal=subtotal,
    )


def run_pipeline(
    paths: list[Path],
    rules: list[MoneyFormatRule],
    ocr_enabled: bool = True,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_flag: Optional[threading.Event] = None,
) -> PipelineResult:
    documents: list[DocumentResult] = []

    with temp_workspace() as workspace:
        discovered_files = discover_files(paths, workspace)

        for discovered in discovered_files:
            if cancel_flag is not None and cancel_flag.is_set():
                break
            if progress_cb is not None:
                progress_cb(discovered.display_name)
            documents.append(_process_single_file(discovered, rules, ocr_enabled))

    grand_total = sum((doc.subtotal for doc in documents), Decimal("0"))
    return PipelineResult(documents=documents, grand_total=grand_total)
