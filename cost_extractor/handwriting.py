"""Optional offline handwriting recognition, used only as a second opinion.

Tesseract cannot read handwriting. A TrOCR model exported to ONNX can, but
badly: measured against handwriting-style renderings of dollar amounts it
got 5 of 20 right, and its mistakes multiply rather than nudge — it reads
the leading `$` as a digit, turning `$340` into `8340`. Summing that would
be far worse than not reading it at all.

So this never produces a value. It produces a *second reading* shown beside
Tesseract's in the review pane, where a human decides. Two engines
disagreeing is a stronger signal that something needs attention than either
engine's confidence score — Tesseract read `$940.00` as `$440.00` at 82%
confidence, which no threshold would have caught.

The model is not bundled. `is_available()` is False in every packaged build
unless someone deliberately vendors one; see the README for how, and for
the accuracy numbers above.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

# The subset of a TrOCR ONNX export that is actually needed: the encoder,
# the plain (non-merged) decoder, and the tokenizer. The merged decoder and
# its cache branches are deliberately not used — greedy decoding over the
# plain graph is slower per token but has no cache-state to get wrong, and
# these are short amounts, not paragraphs.
REQUIRED_FILES = (
    "encoder_model_quantized.onnx",
    "decoder_model_quantized.onnx",
    "tokenizer.json",
)

# TrOCR's DeiT preprocessing, from the model's own preprocessor_config.json.
_INPUT_SIZE = 384
_IMAGE_MEAN = 0.5
_IMAGE_STD = 0.5

# decoder_start_token_id and eos_token_id are both 2 for these checkpoints.
_START_TOKEN = 2
_EOS_TOKEN = 2
_MAX_TOKENS = 24

_session_cache: Optional[tuple] = None
_load_failed = False


def get_model_dir() -> Path:
    """Where a vendored handwriting model lives.

    Mirrors `ocr_setup.get_tesseract_dir` exactly so a frozen build resolves
    it the same way PyInstaller places other vendored payloads.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) if meipass else Path(sys.executable).resolve().parent
        return base / "trocr"
    return Path(__file__).resolve().parent.parent / "vendor" / "trocr"


def is_available() -> bool:
    """True only when a complete model is present.

    Every required file is checked, not just the directory: a half-vendored
    folder that failed at inference time would break the review pane, which
    is the one workflow making guessed amounts trustworthy.
    """
    model_dir = get_model_dir()
    return all((model_dir / name).is_file() for name in REQUIRED_FILES)


def reset_cache() -> None:
    """Drops the loaded sessions. For tests, and for re-checking after a
    model is added without restarting."""
    global _session_cache, _load_failed
    _session_cache = None
    _load_failed = False


def _load():
    """Loads the ONNX sessions once, or gives up permanently.

    onnxruntime is an optional dependency: it is not in requirements.txt and
    not in any packaged build, so the import is done here rather than at
    module scope. A failure is remembered so a broken model does not cost a
    load attempt on every single crop.
    """
    global _session_cache, _load_failed
    if _session_cache is not None:
        return _session_cache
    if _load_failed or not is_available():
        return None

    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_dir = get_model_dir()
        providers = ["CPUExecutionProvider"]
        encoder = ort.InferenceSession(
            str(model_dir / "encoder_model_quantized.onnx"), providers=providers
        )
        decoder = ort.InferenceSession(
            str(model_dir / "decoder_model_quantized.onnx"), providers=providers
        )
        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    except Exception:  # noqa: BLE001 - a missing//broken model is not an error
        _load_failed = True
        return None

    _session_cache = (encoder, decoder, tokenizer)
    return _session_cache


def _preprocess(image):
    import numpy as np

    resized = image.convert("RGB").resize((_INPUT_SIZE, _INPUT_SIZE))
    array = np.asarray(resized, dtype=np.float32) / 255.0
    array = (array - _IMAGE_MEAN) / _IMAGE_STD
    return array.transpose(2, 0, 1)[None]


def read_line(image) -> Optional[str]:
    """Reads one cropped line of handwriting, or None if it can't.

    Returns None rather than raising for every failure — no model, no
    onnxruntime, a corrupt graph, a bad image. The caller is the review
    pane, and losing the pane is worse than losing a second opinion.
    """
    loaded = _load()
    if loaded is None:
        return None
    encoder, decoder, tokenizer = loaded

    try:
        import numpy as np

        hidden = encoder.run(None, {"pixel_values": _preprocess(image)})[0]
        ids = [_START_TOKEN]
        for _ in range(_MAX_TOKENS):
            logits = decoder.run(
                ["logits"],
                {
                    "input_ids": np.array([ids], dtype=np.int64),
                    "encoder_hidden_states": hidden,
                },
            )[0]
            next_token = int(logits[0, -1].argmax())
            if next_token == _EOS_TOKEN and len(ids) > 1:
                break
            ids.append(next_token)
        return tokenizer.decode(ids[1:], skip_special_tokens=True).strip() or None
    except Exception:  # noqa: BLE001 - a second opinion is never worth a crash
        return None


_DIGITS = re.compile(r"\D")


def disagrees(primary: str, second_opinion: Optional[str]) -> bool:
    """Whether two readings differ in the only way that matters: the number.

    Compares digits alone. The engines format differently — Tesseract keeps
    the `$`, TrOCR often turns it into an `S` and sprinkles spaces — and
    flagging that as disagreement would make the signal useless.

    One extra *leading* digit on the second opinion is forgiven, because
    this model's signature failure is reading the leading `$` as a digit:
    `$340.00` comes back as `5340. 00`. Without that allowance the flag
    fires on nearly every amount and stops meaning anything. The allowance
    is deliberately narrow — only one digit, only at the front — since a
    trailing extra digit is an order-of-magnitude error, never noise.
    """
    if not second_opinion:
        return False

    expected = _DIGITS.sub("", primary)
    actual = _DIGITS.sub("", second_opinion)
    if expected == actual:
        return False
    return actual[1:] != expected
