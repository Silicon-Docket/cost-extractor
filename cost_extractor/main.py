"""Entrypoint: resolves the bundled Tesseract path and launches the GUI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from cost_extractor import ocr_setup
from cost_extractor.gui import App, create_root


def _run_selftest(target: Path, out_path: Optional[Path] = None) -> Path:
    """Runs the real pipeline against `target` and writes a plain-text
    summary. Used as a post-build CI smoke test on both platforms: it can
    be invoked against the genuinely frozen/packaged app (real
    sys.frozen/_MEIPASS, real vendored Tesseract) rather than only under
    pytest with monkeypatched attributes, which is how the Windows
    tessdata-path and quoting bugs were actually caught during packaging.
    """
    from cost_extractor.money_parser import default_rules
    from cost_extractor.pipeline import run_pipeline

    tess_dir = ocr_setup.get_tesseract_dir()
    tess_exe = tess_dir / ocr_setup.get_tesseract_executable_name()

    # The review pane degrades to "(no image available)" on every crop if
    # Pillow's Tk bridge didn't make it into the bundle. That failure is
    # silent and only visible in the GUI, which CI never opens — so report
    # it here, where a packaged build is actually exercised.
    try:
        from PIL import ImageTk  # noqa: F401

        crops_displayable = True
    except Exception:  # noqa: BLE001
        crops_displayable = False

    lines = [
        f"frozen={getattr(sys, 'frozen', False)}",
        f"meipass={getattr(sys, '_MEIPASS', None)}",
        f"tesseract_dir={tess_dir}",
        f"tesseract_exe_exists={tess_exe.exists()}",
        f"crops_displayable={crops_displayable}",
    ]
    result = run_pipeline([target], default_rules())
    for doc in result.documents:
        lines.append(
            f"{doc.display_name}: status={doc.status.value} "
            f"subtotal={doc.subtotal} matches={len(doc.matches)} "
            f"message={doc.message}"
        )
        for m in doc.matches:
            lines.append(f"  match: rule={m.rule_id} value={m.value} raw={m.raw_text!r}")
    lines.append(f"grand_total={result.grand_total}")

    output = out_path or (target.parent / "selftest_output.txt")
    output.write_text("\n".join(lines))
    return output


def main() -> None:
    ocr_setup.configure_pytesseract()
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        _run_selftest(Path(sys.argv[2]))
        return
    root = create_root()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
