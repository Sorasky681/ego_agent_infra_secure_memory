from __future__ import annotations

from pathlib import Path

import pytest

from egoagentos_mcp import repo_server
from egoagentos_mcp.common import StructuredToolError, canonical_sha256
from egoagentos_mcp.repo_server import RepoService


def test_canonical_hash_is_key_order_independent() -> None:
    assert canonical_sha256({"b": 2, "a": [1, 3]}) == canonical_sha256(
        {"a": [1, 3], "b": 2}
    )


def test_repo_rejects_traversal_and_escaping_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "escape").symlink_to(outside)
    service = RepoService(root)

    with pytest.raises(StructuredToolError) as traversal:
        service.read_files(["../outside.txt"])
    assert traversal.value.code == "path_outside_trusted_root"

    with pytest.raises(StructuredToolError) as symlink:
        service.read_files(["escape"])
    assert symlink.value.code == "symlink_rejected"


def test_repo_snapshot_is_deterministic_and_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "z.txt").write_text("omega", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("never hash this", encoding="utf-8")
    (root / "outside-link").symlink_to(outside)
    service = RepoService(root)

    first = service.snapshot()
    second = service.snapshot()

    assert first == second
    assert [entry["path"] for entry in first["files"]] == ["a.txt", "z.txt"]
    assert first["excluded"] == [
        {"path": "outside-link", "reason": "symlink_not_followed"}
    ]


def test_repo_snapshot_rejects_directory_replacement_after_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    nested = root / "flip"
    nested.mkdir(parents=True)
    (nested / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = "outside-marker-must-never-be-read"
    (outside / "external.txt").write_text(marker, encoding="utf-8")
    original_open = repo_server.open_child_descriptor
    replaced = False

    def replace_then_open(parent_descriptor: int, name: str, **kwargs: object):
        nonlocal replaced
        if name == "flip" and kwargs.get("require_directory") and not replaced:
            replaced = True
            nested.rename(root / "flip-original")
            (root / "flip").symlink_to(outside, target_is_directory=True)
        return original_open(parent_descriptor, name, **kwargs)

    monkeypatch.setattr(repo_server, "open_child_descriptor", replace_then_open)

    with pytest.raises(StructuredToolError) as raced:
        RepoService(root).snapshot()
    assert raced.value.code == "filesystem_race_detected"
    assert marker not in str(raced.value)


def test_repo_denies_secret_paths_and_redacts_content(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env").write_text("PASSWORD=hunter2", encoding="utf-8")
    (root / "settings.txt").write_text(
        "endpoint=local\napi_key=super-secret-value\n", encoding="utf-8"
    )
    service = RepoService(root)

    with pytest.raises(StructuredToolError) as secret_path:
        service.read_files([".env"])
    assert secret_path.value.code == "sensitive_path_rejected"

    response = service.read_files(["settings.txt"])
    assert "super-secret-value" not in response["files"][0]["content"]
    assert "<redacted>" in response["files"][0]["content"]
    assert response["files"][0]["redaction_applied"] is True


def test_repo_redacts_provider_prefixed_secret_assignments(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    values = ["aws-value-must-not-leak", "aliyun-value-must-not-leak", "azure-secret"]
    (root / "cloud.env.example").write_text(
        "AWS_SECRET_ACCESS_KEY=%s\n"
        "export ALIBABA_CLOUD_ACCESS_KEY_SECRET='%s'\n"
        'AZURE_CLIENT_SECRET: "%s"\n'
        '{"OPENAI_API_KEY":"json-value-must-not-leak"}\n'
        % (*values,),
        encoding="utf-8",
    )
    response = RepoService(root).read_files(["cloud.env.example"])
    content = response["files"][0]["content"]

    assert all(value not in content for value in values)
    assert "json-value-must-not-leak" not in content
    assert content.count("<redacted>") == 4
    assert response["files"][0]["redaction_applied"] is True
