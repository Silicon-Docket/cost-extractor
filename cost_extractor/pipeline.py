"""Orchestrates ingestion -> extraction -> money parsing -> aggregation."""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from cost_extractor.extractors import docx_extractor, image_extractor, pdf_extractor
from cost_extractor.extractors.base import (
    BoundingBox,
    ExtractionResult,
    Status,
    evidence_for_span,
)
from cost_extractor.ingestion import (
    IMAGE_SUFFIXES,
    DiscoveredFile,
    discover_files,
    temp_workspace,
)
from cost_extractor.money_parser import MoneyFormatRule, find_money_matches


# Tesseract's confidence scale is 0-100. 60 is a starting point, not a
# validated cutoff: it wants tuning against a real corpus of the documents
# this gets pointed at, which is exactly what a handwriting corpus would
# provide. Printed text routinely scores 90+; genuinely doubtful readings
# sit well below this.
LOW_CONFIDENCE_THRESHOLD = 60.0


@dataclass
class MatchRecord:
    display_name: str
    location: str
    raw_text: str
    rule_id: str
    value: Decimal
    provenance: str = "text"  # "text" or "ocr"
    # None means nothing was guessed (a text layer was read directly), which
    # is different from a confidence of zero.
    confidence: Optional[float] = None
    # Where the amount sat, in the pixel space of the bitmap OCR read, at
    # `render_scale`. Re-cropping it means re-rendering the source at that
    # same scale.
    #
    # Do not re-crop from this after a run has finished: a zip member's
    # source file is deleted with the temp workspace when `run_pipeline`
    # returns. The crop is taken during the run instead, while the file is
    # still there, and kept in `crop_png`.
    bbox: Optional[BoundingBox] = None
    render_scale: Optional[float] = None
    # The pixels this amount was read from, as PNG bytes. Bytes rather than
    # a live image so the record outlives its source file. Present only for
    # OCR-derived amounts; a text-layer read has nothing to look at.
    crop_png: Optional[bytes] = field(default=None, repr=False)
    # What a human decided this amount really is, after looking at the crop.
    # Set even when they agree with the reading: confirming is a judgement,
    # and an unset value has to keep meaning "nobody has looked".
    corrected_value: Optional[Decimal] = None

    @property
    def reviewed(self) -> bool:
        return self.corrected_value is not None

    @property
    def effective_value(self) -> Decimal:
        """What this amount is worth, preferring a human's reading."""
        return self.value if self.corrected_value is None else self.corrected_value

    @property
    def needs_review(self) -> bool:
        # Confidence is a weak signal — Tesseract read $940.00 as $440.00 at
        # 84% — so this flags the obviously-doubtful, and a human's decision
        # always outranks it.
        if self.reviewed or self.confidence is None:
            return False
        return self.confidence < LOW_CONFIDENCE_THRESHOLD


@dataclass
class DocumentResult:
    display_name: str
    status: Status
    message: Optional[str] = None
    matches: list[MatchRecord] = field(default_factory=list)
    subtotal: Decimal = Decimal("0")

    @property
    def needs_review(self) -> bool:
        return any(m.needs_review for m in self.matches)

    @property
    def effective_subtotal(self) -> Decimal:
        """The subtotal including any human corrections.

        Computed rather than stored: corrections arrive after the run, and
        a stored `subtotal` would quietly go stale the moment one is made.
        """
        return sum((m.effective_value for m in self.matches), Decimal("0"))


@dataclass
class PipelineResult:
    documents: list[DocumentResult]
    # Everything, exactly as before. The split below is additive on purpose:
    # consumers (including the packaged --selftest smoke test) read
    # grand_total as the whole batch, and redefining it in place would
    # silently change what they assert.
    grand_total: Decimal

    @classmethod
    def from_documents(cls, documents: list[DocumentResult]) -> "PipelineResult":
        return cls(
            documents=documents,
            grand_total=sum((doc.subtotal for doc in documents), Decimal("0")),
        )

    @property
    def review_total(self) -> Decimal:
        return sum(
            (
                m.effective_value
                for doc in self.documents
                for m in doc.matches
                if m.needs_review
            ),
            Decimal("0"),
        )

    @property
    def confident_total(self) -> Decimal:
        """The part of the total nobody has flagged as doubtful.

        Derived from `effective_grand_total`, not `grand_total`: the Summary
        sheet prints all three, so basing this on the as-read figure would
        make them stop adding up the moment a correction is entered.
        """
        return self.effective_grand_total - self.review_total

    @property
    def effective_grand_total(self) -> Decimal:
        """The total after human corrections.

        `grand_total` deliberately keeps reporting what was read, so a
        correction shows up as a correction instead of silently rewriting
        what the machine saw.
        """
        return sum(
            (doc.effective_subtotal for doc in self.documents), Decimal("0")
        )

    @property
    def unreviewed_ocr_count(self) -> int:
        """OCR-derived amounts nobody has looked at yet.

        Counts every guessed amount, not just low-confidence ones: the
        spike found a confidently-wrong reading ($940.00 read as $440.00 at
        84%), so confidence alone cannot decide what is safe to skip.
        """
        return sum(
            1
            for doc in self.documents
            for m in doc.matches
            if m.provenance == "ocr" and not m.reviewed
        )


# Pixels of surrounding page kept around a crop. Digits are far easier to
# judge with a little of the line either side than tight to the ink.
_CROP_MARGIN = 14


def _crop_png(page, bbox: Optional[BoundingBox]) -> Optional[bytes]:
    """Cuts the matched region out of the page as PNG bytes.

    Stored as bytes rather than a PIL image so a result can outlive the
    file it came from — which for a zip member is deleted the moment the
    run ends.
    """
    if page is None or bbox is None:
        return None
    try:
        box = (
            max(0, bbox.left - _CROP_MARGIN),
            max(0, bbox.top - _CROP_MARGIN),
            min(page.width, bbox.right + _CROP_MARGIN),
            min(page.height, bbox.bottom + _CROP_MARGIN),
        )
        buffer = io.BytesIO()
        page.crop(box).save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - the amount matters more than its picture
        return None


def _extract(discovered: DiscoveredFile, ocr_enabled: bool) -> ExtractionResult:
    if discovered.suffix == ".docx":
        return docx_extractor.extract(discovered.path)
    if discovered.suffix == ".pdf":
        return pdf_extractor.extract(discovered.path, ocr_enabled=ocr_enabled)
    if discovered.suffix in IMAGE_SUFFIXES:
        return image_extractor.extract(discovered.path, ocr_enabled=ocr_enabled)
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
        found = find_money_matches(segment.text, rules)
        # The match's own character offsets locate it on the page, so no
        # second pass over the text is needed to find it again.
        evidences = [evidence_for_span(segment, m.start, m.end) for m in found]

        # Render the page at most once per segment, and only if something
        # on it actually needs a picture. The image is released with the
        # segment; nothing holds a page beyond this loop.
        page = None
        if segment.page_image is not None and any(e is not None for e in evidences):
            try:
                page = segment.page_image()
            except Exception:  # noqa: BLE001 - a missing crop must not lose the amount
                page = None

        for m, evidence in zip(found, evidences):
            matches.append(
                MatchRecord(
                    display_name=discovered.display_name,
                    location=segment.location,
                    raw_text=m.raw_text,
                    rule_id=m.rule_id,
                    value=m.value,
                    provenance=segment.provenance,
                    confidence=evidence.confidence if evidence else None,
                    bbox=evidence.bbox if evidence else None,
                    render_scale=segment.render_scale,
                    crop_png=_crop_png(page, evidence.bbox) if evidence else None,
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

    return PipelineResult.from_documents(documents)
