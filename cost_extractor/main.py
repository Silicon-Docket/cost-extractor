"""Entrypoint: resolves the bundled Tesseract path and launches the GUI."""

from __future__ import annotations

from cost_extractor import ocr_setup
from cost_extractor.gui import App, create_root


def main() -> None:
    ocr_setup.configure_pytesseract()
    root = create_root()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
