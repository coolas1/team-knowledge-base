from __future__ import annotations

import pytest

from src.engine.hindsight_components.file_chunk_recall import fielded_lexical_scores
from src.engine.hindsight_components.repository import PostgresMemoryRepository


@pytest.mark.parametrize(
    ("query", "matching_title"),
    [
        ("autonomous driving", "Autonomous Driving Control"),
        ("自动驾驶", "自动驾驶控制理论"),
        ("自動運転", "自動運転アルゴリズム"),
        ("RFC-9457", "RFC-9457 API Errors"),
    ],
)
def test_title_and_identifier_matches_beat_long_body_dilution(query, matching_title):
    fields = {
        "title": [matching_title, "Unrelated notes"],
        "filename": ["paper.pdf", "notes.txt"],
        "overview": ["", ""],
        "tags": ["", ""],
        "body": ["ocr noise " * 5000, f"misc {query} " * 2],
    }
    scores, contributions = fielded_lexical_scores(
        query,
        fields,
        PostgresMemoryRepository._bm25,
        {"title": 3.0, "filename": 2.5, "overview": 1.8, "tags": 2.0, "body": 1.0},
    )
    assert scores[0] > scores[1]
    assert contributions["title"][0] > 0
