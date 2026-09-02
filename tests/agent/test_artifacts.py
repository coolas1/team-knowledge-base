from __future__ import annotations

import json
import zipfile

import pytest

from src.agent.artifacts import generate_artifact, resolve_artifact


@pytest.mark.parametrize("format", ["docx", "pdf", "pptx"])
def test_generate_downloadable_office_artifacts(tmp_path, monkeypatch, format):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))

    artifact = generate_artifact(
        format=format,
        title="项目周报",
        file_name="weekly-report",
        content="# 本周进展\n\n- 完成文档生成\n- 增加下载入口",
    )
    path, resolved = resolve_artifact(artifact.id)

    assert resolved == artifact
    assert path.name == f"weekly-report.{format}"
    assert path.stat().st_size == artifact.size
    assert artifact.download_url == f"/api/artifacts/{artifact.id}/download"
    metadata = json.loads((path.parent / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["title"] == "项目周报"
    if format == "pdf":
        assert path.read_bytes().startswith(b"%PDF")
    else:
        assert zipfile.is_zipfile(path)


def test_pptx_includes_slidev_source(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))

    artifact = generate_artifact(
        format="pptx",
        title="路线图",
        content="# 第一阶段\n\n- 调研\n\n---\n\n# 第二阶段\n\n- 发布",
    )
    source, _ = resolve_artifact(artifact.id, slidev=True)
    text = source.read_text(encoding="utf-8")

    assert artifact.slidev_url == f"/api/artifacts/{artifact.id}/slidev"
    assert "theme: default" in text
    assert "# 第一阶段" in text
    assert "# 第二阶段" in text


def test_generate_artifact_rejects_empty_and_unsupported_content(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="标题"):
        generate_artifact(format="docx", title=" ", content="content")
    with pytest.raises(ValueError, match="内容"):
        generate_artifact(format="pdf", title="Title", content=" ")


def test_resolve_artifact_rejects_invalid_id(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))

    with pytest.raises(FileNotFoundError):
        resolve_artifact("../../etc/passwd")
