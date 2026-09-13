"""Retrieval-view composition: metadata prefix + original text."""

from src.engine.retrieval_view import (
    OVERVIEW_PREFIX_CHARS,
    retrieval_view,
    retrieval_view_prefix,
)


def test_prefix_composes_title_filename_overview():
    assert retrieval_view_prefix("T", "f.pdf", "clean summary") == "T | f.pdf | clean summary\n"


def test_prefix_bounds_overview_and_dedupes_parts():
    overview = "o" * 500
    prefix = retrieval_view_prefix("T", "T", overview)
    assert prefix == f"T | {overview[:OVERVIEW_PREFIX_CHARS]}\n"


def test_prefix_omits_empty_parts_and_is_empty_without_them():
    assert retrieval_view_prefix("T", None, "") == "T\n"
    assert retrieval_view_prefix(None, None, None) == ""
    assert retrieval_view_prefix("", "  ", "") == ""


def test_retrieval_view_prepends_prefix_to_text():
    assert retrieval_view("body", "T", "f.pdf", "ov") == "T | f.pdf | ov\nbody"
    assert retrieval_view("body", None) == "body"
