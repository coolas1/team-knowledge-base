from __future__ import annotations

import json
from pathlib import Path


REPORT = (
    Path(__file__).parents[3]
    / "openspec/changes/separate-conversation-memory-from-knowledge-evidence/release-acceptance.json"
)


def test_release_acceptance_has_all_mandatory_evidence_and_keeps_reads_off():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert len(report["build_sha"]) == 40
    assert all(len(value) == 64 for value in report["corpus_hashes"].values())
    assert all(report["mandatory_gates"].values())
    assert report["quality"]["conversation_to_document_leakage"] == 0
    assert report["quality"]["fabricated_citation_rate"] == 0
    assert report["provenance"]["assistant_derived_active_memories"] == 0
    assert report["phase_outcomes"]["task_leaks"] == 0
    assert report["resource_bounds"]["candidate_max"] <= report["resource_bounds"][
        "candidate_limit"
    ]
    assert report["resource_bounds"]["payload_max_bytes"] <= report[
        "resource_bounds"
    ]["payload_limit_bytes"]
    assert report["rollback"]["verified"] is True
    assert report["rollback"]["read_enabled_after_rollback"] is False
    assert report["production_reads_enabled"] is False
    assert report["status"] == "passed_for_bounded_rollout"
