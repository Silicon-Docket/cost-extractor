"""Shared contract every extractor (docx, pdf, image, ...) produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class Status(Enum):
    OK = "OK"
    OK_WITH_WARNINGS = "OK_WITH_WARNINGS"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class BoundingBox:
    """A rectangle in rendered-bitmap pixel space.

    Coordinates are only meaningful alongside the `render_scale` of the
    segment they came from — see TextSegment.render_scale.
    """

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def union(self, other: "BoundingBox") -> "BoundingBox":
        left = min(self.left, other.left)
        top = min(self.top, other.top)
        return BoundingBox(
            left=left,
            top=top,
            width=max(self.right, other.right) - left,
            height=max(self.bottom, other.bottom) - top,
        )


@dataclass(frozen=True)
class PositionedToken:
    """One OCR-recognised word, tied to where it sat and how sure OCR was.

    `start`/`end` are character offsets into the owning segment's text, so a
    regex match's offsets map straight onto the tokens it consumed.
    """

    text: str
    start: int
    end: int
    bbox: BoundingBox
    confidence: float  # Tesseract's 0-100 scale


@dataclass(frozen=True)
class SpanEvidence:
    """Where a span of text physically came from, and how much to trust it."""

    bbox: BoundingBox
    confidence: float


@dataclass
class TextSegment:
    text: str
    location: str
    provenance: str = "text"  # "text" or "ocr"
    tokens: list[PositionedToken] = field(default_factory=list)
    # Bitmap pixels per PDF point at the scale the page was rendered for OCR.
    # Token boxes are in that bitmap's pixel space, so a consumer re-rendering
    # the page to crop a region must use the same scale or the box lands
    # somewhere else entirely. None for segments not derived from a raster.
    render_scale: Optional[float] = None
    # Reproduces the exact bitmap the token boxes index into, on demand.
    #
    # A thunk rather than the image itself: a 300-DPI page is ~25 MB, and a
    # 100-page scan would otherwise hold every page in memory at once just
    # in case something wanted to crop one. Callers invoke it only when
    # there is actually something to crop, and let it go straight after.
    #
    # It closes over the source file, so it is only valid while that file
    # exists — for a zip member, that means during the pipeline run.
    page_image: Optional[Callable[[], "object"]] = field(default=None, repr=False)


def evidence_for_span(
    segment: TextSegment, start: int, end: int
) -> Optional[SpanEvidence]:
    """Maps a character span onto the OCR tokens that produced it.

    Returns the union of their boxes and the *worst* confidence among them —
    an amount is only as trustworthy as its worst-read digit, so averaging
    would hide exactly the token that needs review. Returns None when the
    segment carries no tokens (text-layer extraction, where there is no
    bitmap to crop) or when the span touches none, which is meaningfully
    different from a confidence of zero.
    """
    overlapping = [
        t for t in segment.tokens if t.start < end and start < t.end
    ]
    if not overlapping:
        return None

    bbox = overlapping[0].bbox
    for token in overlapping[1:]:
        bbox = bbox.union(token.bbox)

    return SpanEvidence(
        bbox=bbox, confidence=min(t.confidence for t in overlapping)
    )


@dataclass
class ExtractionResult:
    status: Status
    segments: list[TextSegment] = field(default_factory=list)
    error_message: str | None = None
