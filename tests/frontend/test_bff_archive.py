"""归档 BFF 契约测试：fake runtime 上验证路由、校验与执行语义。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps


class FakeScanResult:
    def __init__(self, enqueued):
        self.enqueued = enqueued
        self.skipped_duplicate = 0


class FakeArchiveRuntime:
    """覆盖 routes_archive 触碰到的 runtime 面。"""

    def __init__(self, *, inbox_dir: Path, fail_execute=False):
        self.config = SimpleNamespace(inbox_dir=str(inbox_dir))
        self._inbox = inbox_dir
        self._fail_execute = fail_execute
        self.approved: list[str] = []
        self.rejected: list[str] = []
        self.deferred: list[str] = []
        self.replanned: list[str] = []
        self.reassigned: list[tuple] = []
        self.assigned: list[tuple] = []
        self.undos: list[str] = []
        self.reindexes: list[str] = []
        self.modes: list[bool] = []
        self.scans = 0

    async def list_reviews(self):
        return [
            {
                "id": "job-1",
                "file_name": "report.pdf",
                "status": "awaiting_review",
                "plan": {"decision": {"confidence": 0.7}},
                "routing_reason": "within_confidence_band",
            }
        ]

    async def list_attention(self):
        return [
            {
                "id": "job-2",
                "file_name": "scan.jpg",
                "status": "skipped",
                "routing_reason": "below_confidence_band",
            }
        ]

    async def list_operations(self):
        return [
            {
                "id": "op-1",
                "source_path": "/ws/inbox/a.md",
                "destination_path": "/ws/archive/财务/a.md",
                "decision_source": "auto",
                "confidence": 0.9,
                "status": "done",
                "undo_status": None,
            }
        ]

    async def tree(self):
        return [
            {
                "candidate_id": "财务",
                "description": "目录 财务。",
                "doc_count": 1,
                "documents": [
                    {
                        "id": "doc-1",
                        "title": "发票.md",
                        "file_type": "markdown",
                        "status": "indexed",
                        "overview": "第三季度发票",
                        "created_at": None,
                    }
                ],
            }
        ]

    async def approve(self, job_id):
        if self._fail_execute:
            raise RuntimeError("executor down")
        self.approved.append(job_id)
        return {"job_id": job_id, "status": "done", "destination": "/ws/archive/x"}

    async def reject(self, job_id):
        self.rejected.append(job_id)
        return {"job_id": job_id, "status": "skipped"}

    async def defer(self, job_id):
        self.deferred.append(job_id)
        return {"job_id": job_id, "status": "unarchived"}

    async def list_unarchived(self):
        return [
            {
                "id": "job-3",
                "file_name": "later.md",
                "status": "unarchived",
                "routing_reason": "deferred",
            }
        ]

    async def replan(self, job_id):
        self.replanned.append(job_id)
        return {"job_id": job_id, "status": "queued"}

    async def get_policy(self):
        return {
            "id": "policy-1",
            "version": 1,
            "enabled": True,
            "rules": {"instructions": "优先按项目归档"},
        }

    async def update_policy(self, *, enabled, rules):
        return {"id": "policy-2", "version": 2, "enabled": enabled, "rules": rules}

    async def scan_legacy(self):
        return [{"doc_id": "doc-1", "title": "旧文档", "file_exists": True}]

    async def plan_legacy(self, document_ids=None):
        return {"batch_id": "batch-1", "policy_version": 1, "items": []}

    async def execute_legacy(self, batch_id, document_ids=None, overrides=None):
        return {"batch_id": batch_id, "status": "done", "done": 1, "failed": 0}

    async def reassign(self, job_id, directory):
        if not isinstance(directory, str) or not directory.strip():
            raise ValueError("目录非法")
        self.reassigned.append((job_id, directory))
        return {"job_id": job_id, "status": "done"}

    async def assign(self, job_id, directory):
        self.assigned.append((job_id, directory))
        return {"job_id": job_id, "status": "done"}

    async def undo(self, operation_id):
        from src.engine.components.archive.journal import UndoConflict

        if operation_id == "conflict-op":
            raise UndoConflict("归档后文件已被修改，拒绝撤销")
        if operation_id == "missing":
            raise ValueError("操作不存在: missing")
        self.undos.append(operation_id)
        return {"operation_id": operation_id, "undo_status": "undone"}

    async def reindex(self, operation_id):
        self.reindexes.append(operation_id)
        return {"operation_id": operation_id, "status": "done"}

    def mode(self):
        return {"review_all": self._review_all}

    def set_mode(self, review_all):
        self._review_all = review_all
        self.modes.append(review_all)
        return self.mode()

    _review_all = False

    async def scan_now(self):
        self.scans += 1
        enqueued = [p.name for p in self._inbox.iterdir() if p.is_file()] if self._inbox.is_dir() else []
        return FakeScanResult(enqueued)


@pytest.fixture
def client(monkeypatch, tmp_path):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    runtime = FakeArchiveRuntime(inbox_dir=tmp_path / "inbox")
    app_mod.app.dependency_overrides[deps.get_archive] = lambda: runtime
    with TestClient(app_mod.app) as c:
        yield c, runtime, tmp_path
    app_mod.app.dependency_overrides.clear()


def test_list_reviews(client):
    c, runtime, _ = client
    res = c.get("/api/archive/reviews")
    assert res.status_code == 200
    items = res.json()["items"]
    assert items[0]["id"] == "job-1"
    assert items[0]["routing_reason"] == "within_confidence_band"


def test_approve_routes_to_runtime(client):
    c, runtime, _ = client
    res = c.post("/api/archive/reviews/job-1/approve")
    assert res.status_code == 200
    assert runtime.approved == ["job-1"]


def test_approve_maps_runtime_errors_to_503(client, monkeypatch, tmp_path):
    c, _, _ = client
    # 换成执行失败的 runtime
    failing = FakeArchiveRuntime(inbox_dir=tmp_path / "inbox", fail_execute=True)
    app_mod.app.dependency_overrides[deps.get_archive] = lambda: failing
    res = c.post("/api/archive/reviews/job-1/approve")
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "archive_execute_unavailable"


def test_reject_routes_to_runtime(client):
    c, runtime, _ = client
    res = c.post("/api/archive/reviews/job-1/reject")
    assert res.status_code == 200
    assert runtime.rejected == ["job-1"]


def test_defer_uses_unarchived_semantics(client):
    c, runtime, _ = client
    res = c.post("/api/archive/reviews/job-1/defer")
    assert res.status_code == 200
    assert res.json()["status"] == "unarchived"
    assert runtime.deferred == ["job-1"]


def test_unarchived_list_and_replan(client):
    c, runtime, _ = client
    listed = c.get("/api/archive/unarchived")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "unarchived"
    replanned = c.post("/api/archive/unarchived/job-3/replan")
    assert replanned.status_code == 200
    assert replanned.json()["status"] == "queued"
    assert runtime.replanned == ["job-3"]


def test_reassign_validates_directory(client):
    c, runtime, _ = client
    ok = c.post(
        "/api/archive/reviews/job-1/reassign", json={"directory": "财务/发票"}
    )
    assert ok.status_code == 200
    assert runtime.reassigned == [("job-1", "财务/发票")]

    bad = c.post("/api/archive/reviews/job-1/reassign", json={"directory": "  "})
    assert bad.status_code == 400


def test_skipped_listing_and_manual_assign(client):
    c, runtime, _ = client
    res = c.get("/api/archive/skipped")
    assert res.status_code == 200
    assert res.json()["items"][0]["routing_reason"] == "below_confidence_band"

    assign = c.post("/api/archive/skipped/job-2/assign", json={"directory": "会议纪要"})
    assert assign.status_code == 200
    assert runtime.assigned == [("job-2", "会议纪要")]


def test_operations_listing(client):
    c, _, _ = client
    res = c.get("/api/archive/operations")
    assert res.status_code == 200
    assert res.json()["items"][0]["destination_path"].endswith("a.md")


def test_undo_success_and_conflict(client):
    c, runtime, _ = client
    ok = c.post("/api/archive/operations/op-1/undo")
    assert ok.status_code == 200
    assert runtime.undos == ["op-1"]

    conflict = c.post("/api/archive/operations/conflict-op/undo")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "undo_conflict"

    missing = c.post("/api/archive/operations/missing/undo")
    assert missing.status_code == 400


def test_reindex_endpoint(client):
    c, runtime, _ = client
    res = c.post("/api/archive/operations/op-1/reindex")
    assert res.status_code == 200
    assert runtime.reindexes == ["op-1"]


def test_mode_get_set(client):
    c, runtime, _ = client
    assert c.get("/api/archive/mode").json() == {"review_all": False}
    res = c.put("/api/archive/mode", json={"review_all": True})
    assert res.status_code == 200
    assert res.json() == {"review_all": True}
    assert runtime.modes == [True]


def test_tree_endpoint(client):
    c, _, _ = client
    res = c.get("/api/archive/tree")
    assert res.status_code == 200
    assert res.json()["items"][0]["candidate_id"] == "财务"
    assert res.json()["items"][0]["documents"][0]["id"] == "doc-1"


def test_policy_and_legacy_endpoints(client):
    c, _, _ = client
    assert c.get("/api/archive/policy").json()["version"] == 1
    updated = c.put(
        "/api/archive/policy",
        json={"enabled": True, "rules": {"instructions": "按客户归档"}},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert c.get("/api/archive/legacy/scan").json()["items"][0]["doc_id"] == "doc-1"
    planned = c.post("/api/archive/legacy/plan", json={"document_ids": ["doc-1"]})
    assert planned.status_code == 200
    executed = c.post(
        "/api/archive/legacy/execute",
        json={"batch_id": "batch-1", "document_ids": ["doc-1"]},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "done"


def test_legacy_plan_returns_structured_service_error(client):
    c, runtime, _ = client

    async def fail_plan(document_ids=None):
        raise RuntimeError("provider unavailable")

    runtime.plan_legacy = fail_plan
    response = c.post("/api/archive/legacy/plan", json={"document_ids": ["doc-1"]})

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "archive_plan_unavailable",
        "message": "暂时无法生成归档预览，请稍后重试",
        "retryable": True,
    }


def test_inbox_upload_lands_file_and_scans(client):
    c, runtime, tmp_path = client
    res = c.post(
        "/api/archive/inbox",
        files={"file": ("notes.md", b"# notes", "text/markdown")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["filename"] == "notes.md"
    assert body["queued"] is True
    assert (tmp_path / "inbox" / "notes.md").read_bytes() == b"# notes"
    assert runtime.scans == 1  # 上传后立即扫描


def test_inbox_upload_rejects_unsupported_type(client):
    c, _, _ = client
    res = c.post(
        "/api/archive/inbox",
        files={"file": ("malware.exe", b"x", "application/octet-stream")},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "unsupported_file_type"


def test_inbox_upload_rejects_empty_and_conflict(client):
    c, _, tmp_path = client
    empty = c.post(
        "/api/archive/inbox", files={"file": ("e.md", b"", "text/markdown")}
    )
    assert empty.status_code == 400
    assert empty.json()["detail"]["code"] == "empty_file"

    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    (tmp_path / "inbox" / "dup.md").write_bytes(b"x")
    conflict = c.post(
        "/api/archive/inbox", files={"file": ("dup.md", b"y", "text/markdown")}
    )
    assert conflict.status_code == 400
    assert conflict.json()["detail"]["code"] == "inbox_name_conflict"
