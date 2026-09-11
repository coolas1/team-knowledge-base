"""Ensure the fixed parity baseline remains complete and reproducible."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "benchmark" / "memory-parity"
spec = importlib.util.spec_from_file_location("parity_validate", ROOT / "validate.py")
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def test_fixed_corpus_matches_baseline():
    first = validator.validate(ROOT / "cases.json")
    assert first == validator.validate(ROOT / "cases.json")
    baseline = json.loads((ROOT / "baseline.json").read_text(encoding="utf-8"))
    assert first["sha256"] == baseline["corpus_sha256"]
    assert first["cases"] >= 40
    assert all(count >= 3 for count in first["categories"].values())


def test_duplicate_case_rejected(tmp_path):
    corpus = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    corpus["cases"][1]["id"] = corpus["cases"][0]["id"]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        validator.validate(path)


def test_fingerprint_survives_windows_checkout(tmp_path):
    original = (ROOT / "cases.json").read_bytes().replace(b"\r\n", b"\n")
    path = tmp_path / "cases.json"
    path.write_bytes(original.replace(b"\n", b"\r\n"))
    assert validator.validate(path) == validator.validate(ROOT / "cases.json")
