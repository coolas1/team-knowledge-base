from __future__ import annotations

import json
from pathlib import Path


def test_rollout_rehearsal_preserves_counts_checksums_and_rollback():
    report = json.loads(
        Path(__file__).with_name("rollout_rehearsal.json").read_text(encoding="utf-8")
    )
    stages = report["stages"]
    assert [stage["name"] for stage in stages] == [
        "schema_preparation",
        "dual_write",
        "dry_run",
        "backfill_interrupted",
        "resume",
        "validation",
        "read_switch",
        "cleanup",
        "dependency_rebuild",
        "rollback",
    ]
    assert len(report["protected_checksum"]) == 64
    assert all(stage["documents"] == 2 for stage in stages)
    assert all(stage["chunks"] == 2 for stage in stages)
    assert all(stage["protected"] == 1 for stage in stages)
    assert all(stage["checksum"] == report["protected_checksum"] for stage in stages)
    assert stages[0]["parents"] == 0
    assert stages[4]["parents"] == stages[-1]["parents"] == 2
    assert stages[6]["read_enabled"] is True
    assert stages[-1]["read_enabled"] is False
    assert stages[-2]["pending_dependencies"] == 0
    assert report["integration_evidence"] == {
        "tests": 6,
        "failures": 0,
        "container": "tkb-memory-refine-rehearsal",
        "database": "memory_refine_rehearsal",
    }
