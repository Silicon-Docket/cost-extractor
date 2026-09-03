"""The optional handwriting model, as a second opinion only.

Measured at 5/20 on handwriting-style amounts, with errors that multiply
rather than nudge ($340 read as 8340), so its output must never become a
value on its own. It earns its place by disagreeing with Tesseract, which
is a far better signal that something needs a human than either engine's
confidence score.

These run without the model present — which is the default, and the state
every packaged build ships in today.
"""

from pathlib import Path

import pytest
from PIL import Image

from cost_extractor import handwriting


def test_no_model_directory_means_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(handwriting, "get_model_dir", lambda: tmp_path / "absent")

    assert handwriting.is_available() is False


def test_a_directory_missing_its_weights_is_not_available(tmp_path, monkeypatch):
    # A half-populated folder must not read as ready; failing at inference
    # time inside the review pane would be far more confusing.
    model_dir = tmp_path / "trocr"
    model_dir.mkdir()
    (model_dir / "tokenizer.json").write_text("{}")
    monkeypatch.setattr(handwriting, "get_model_dir", lambda: model_dir)

    assert handwriting.is_available() is False


def test_a_complete_model_directory_reads_as_available(tmp_path, monkeypatch):
    model_dir = tmp_path / "trocr"
    model_dir.mkdir()
    for name in handwriting.REQUIRED_FILES:
        (model_dir / name).write_bytes(b"stub")
    monkeypatch.setattr(handwriting, "get_model_dir", lambda: model_dir)

    assert handwriting.is_available() is True


def test_reading_without_a_model_returns_nothing_rather_than_raising(
    tmp_path, monkeypatch
):
    # The review pane calls this for every crop; an exception there would
    # take out the one workflow that makes guessed amounts trustworthy.
    monkeypatch.setattr(handwriting, "get_model_dir", lambda: tmp_path / "absent")

    assert handwriting.read_line(Image.new("RGB", (60, 20), "white")) is None


def test_a_backend_that_fails_to_load_degrades_to_nothing(tmp_path, monkeypatch):
    model_dir = tmp_path / "trocr"
    model_dir.mkdir()
    for name in handwriting.REQUIRED_FILES:
        (model_dir / name).write_bytes(b"not a real onnx graph")
    monkeypatch.setattr(handwriting, "get_model_dir", lambda: model_dir)
    handwriting.reset_cache()

    assert handwriting.read_line(Image.new("RGB", (60, 20), "white")) is None


def test_the_model_directory_sits_beside_the_vendored_tesseract():
    # Same resolution rule as ocr_setup, so a frozen build finds it the
    # same way and a dev checkout keeps it gitignored next to the other
    # vendored payloads.
    assert handwriting.get_model_dir().name == "trocr"


def test_a_second_opinion_that_matches_is_not_a_disagreement():
    assert handwriting.disagrees("$940.00", "$940.00") is False


def test_differences_in_spacing_and_symbols_are_not_a_disagreement():
    # The engines format differently; only the number matters.
    assert handwriting.disagrees("$940.00", "S 940. 00") is False
    assert handwriting.disagrees("$1,245.00", "1245.00") is False


def test_a_different_number_is_a_disagreement():
    assert handwriting.disagrees("$440.00", "$940.00") is True


def test_an_absent_second_opinion_is_not_a_disagreement():
    assert handwriting.disagrees("$440.00", None) is False
    assert handwriting.disagrees("$440.00", "") is False


def test_a_spurious_leading_digit_is_not_a_disagreement():
    # The model's signature failure: it reads the leading "$" as a digit,
    # so "$340.00" comes back as "5340. 00". Treating that as disagreement
    # would fire the flag on nearly every amount and make it worthless.
    assert handwriting.disagrees("$340.00", "5340. 00") is False
    assert handwriting.disagrees("$1,245.00", "81,245.00") is False


def test_a_real_difference_behind_a_spurious_digit_still_disagrees():
    # "$440.00" vs "5940 00": drop the bogus leading 5 and it is still 940
    # against 440 — the misread this whole mechanism exists to catch.
    assert handwriting.disagrees("$440.00", "5940 00") is True


def test_a_spurious_digit_is_only_forgiven_at_the_front():
    # A trailing extra digit is an order-of-magnitude error, never noise.
    assert handwriting.disagrees("$340.00", "340.005") is True


def test_only_one_leading_digit_is_forgiven():
    assert handwriting.disagrees("$340.00", "5534000") is True
