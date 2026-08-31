"""Shared contract every extractor (docx, pdf, ...) produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(Enum):
    OK = "OK"
    OK_WITH_WARNINGS = "OK_WITH_WARNINGS"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


@dataclass
class TextSegment:
    text: str
    location: str
    provenance: str = "text"  # "text" or "ocr"


@dataclass
class ExtractionResult:
    status: Status
    segments: list[TextSegment] = field(default_factory=list)
    error_message: str | None = None
