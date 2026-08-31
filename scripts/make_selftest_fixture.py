"""Generates a scanned-image-style PDF (no text layer) for CI to run the
packaged app's --selftest flag against, proving the OCR fallback works
inside the genuinely frozen/packaged build. Usage:

    python scripts/make_selftest_fixture.py <output.pdf>
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def make_scanned_pdf(pdf_path: Path) -> None:
    img = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        try:
            font = ImageFont.truetype(
                "/System/Library/Fonts/Supplemental/Arial.ttf", 48
            )
        except OSError:
            font = ImageFont.load_default()
    draw.text((100, 100), "Reimbursement due: $2,345.00", fill="black", font=font)

    img_path = pdf_path.with_suffix(".png")
    img.save(img_path)

    c = canvas.Canvas(str(pdf_path), pagesize=letter)
    c.drawImage(str(img_path), 0, 0, width=letter[0], height=letter[1])
    c.save()


if __name__ == "__main__":
    make_scanned_pdf(Path(sys.argv[1]))
