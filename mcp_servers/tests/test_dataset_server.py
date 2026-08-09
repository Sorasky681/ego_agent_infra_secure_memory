from __future__ import annotations

from pathlib import Path

import pytest

from egoagentos_mcp import dataset_server
from egoagentos_mcp.common import StructuredToolError
from egoagentos_mcp.dataset_server import MANIFEST_NAME, DatasetManifestService


def test_manifest_creation_is_canonical_idempotent_and_verifiable(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    dataset = root / "demo"
    (dataset / "nested").mkdir(parents=True)
    (dataset / "a.txt").write_text("one\n", encoding="utf-8")
    (dataset / "nested" / "b.bin").write_bytes(b"\x01\x02\x03")
    service = DatasetManifestService(root)

    first = service.create("demo", "synthetic-demo-v1")
    first_bytes = (dataset / MANIFEST_NAME).read_bytes()
    second = service.create("demo", "synthetic-demo-v1")
    second_bytes = (dataset / MANIFEST_NAME).read_bytes()

    assert first["write_status"] == "created"
    assert second["write_status"] == "unchanged"
    assert first["manifest"]["manifest_sha256"] == second["manifest"]["manifest_sha256"]
    assert first_bytes == second_bytes
    assert service.verify("demo")["valid"] is True


def test_manifest_verification_detects_data_drift(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    dataset = root / "demo"
    dataset.mkdir(parents=True)
    target = dataset / "sample.txt"
    target.write_text("before", encoding="utf-8")
    service = DatasetManifestService(root)
    service.create("demo", "demo-v1")

    target.write_text("after", encoding="utf-8")
    result = service.verify("demo")

    assert result["valid"] is False
    assert "files" in result["mismatches"]
    assert "manifest_sha256" in result["mismatches"]

    with pytest.raises(StructuredToolError) as laundering:
        service.create("demo", "demo-v1")
    assert laundering.value.code == "manifest_immutable"
    assert service.verify("demo")["valid"] is False


def test_manifest_rejects_symlink_and_traversal(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    dataset = root / "demo"
    dataset.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    (dataset / "escape").symlink_to(outside)
    service = DatasetManifestService(root)

    with pytest.raises(StructuredToolError) as symlink:
        service.create("demo", "demo-v1")
    assert symlink.value.code == "symlink_rejected"

    with pytest.raises(StructuredToolError) as traversal:
        service.create("../", "demo-v1")
    assert traversal.value.code == "path_outside_trusted_root"


def test_manifest_scan_rejects_directory_replacement_after_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "datasets"
    dataset = root / "demo"
    nested = dataset / "flip"
    nested.mkdir(parents=True)
    (nested / "inside.bin").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = b"outside-marker-must-never-be-hashed"
    (outside / "external.bin").write_bytes(marker)
    original_open = dataset_server.open_child_descriptor
    replaced = False

    def replace_then_open(parent_descriptor: int, name: str, **kwargs: object):
        nonlocal replaced
        if name == "flip" and kwargs.get("require_directory") and not replaced:
            replaced = True
            nested.rename(dataset / "flip-original")
            (dataset / "flip").symlink_to(outside, target_is_directory=True)
        return original_open(parent_descriptor, name, **kwargs)

    monkeypatch.setattr(dataset_server, "open_child_descriptor", replace_then_open)

    with pytest.raises(StructuredToolError) as raced:
        DatasetManifestService(root).create("demo", "demo-v1")
    assert raced.value.code == "filesystem_race_detected"
    assert marker.decode() not in str(raced.value)
    assert not (dataset / MANIFEST_NAME).exists()
