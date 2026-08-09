"""Canonical dataset-manifest creation and verification MCP server."""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .common import (
    StrictModel,
    StructuredToolError,
    TrustedRoot,
    canonical_json,
    canonical_sha256,
    descriptor_sha256,
    is_sensitive_path,
    open_child_descriptor,
    read_regular_descriptor,
    run_mcp_server,
)

MANIFEST_NAME = ".egoagentos-manifest.json"
MANIFEST_SCHEMA = "egoagentos.dataset-manifest.v1"
MAX_DATASET_FILES = 100_000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class DatasetFile(StrictModel):
    path: str = Field(min_length=1, max_length=2_048)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatasetManifest(StrictModel):
    schema_name: Literal[MANIFEST_SCHEMA] = Field(alias="schema")
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    dataset_path: str = Field(min_length=1, max_length=1_024)
    files: list[DatasetFile] = Field(max_length=MAX_DATASET_FILES)
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = StrictModel.model_config | {"populate_by_name": True}


class DatasetManifestService:
    """Filesystem policy and deterministic manifest implementation."""

    def __init__(self, root: str | Path, *, max_files: int = MAX_DATASET_FILES) -> None:
        self.root = TrustedRoot(root, label="dataset root")
        self.max_files = max_files

    @classmethod
    def from_env(cls) -> DatasetManifestService:
        root = TrustedRoot.from_env("EGO_MCP_DATASET_ROOT", label="dataset root")
        return cls(root.path)

    def _scan(self, dataset_descriptor: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        def walk(directory_descriptor: int, prefix: str) -> None:
            try:
                with os.scandir(directory_descriptor) as scanner:
                    entries = sorted(scanner, key=lambda item: item.name)
            except OSError as exc:
                raise StructuredToolError(
                    "directory_read_failed",
                    "A dataset directory could not be read safely",
                    {"reason": type(exc).__name__},
                ) from exc
            for entry in entries:
                relative = "%s/%s" % (prefix, entry.name) if prefix else entry.name
                if not prefix and entry.name == MANIFEST_NAME:
                    continue
                if entry.is_symlink():
                    raise StructuredToolError(
                        "symlink_rejected", "Symlinks are not permitted in deterministic manifests"
                    )
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise StructuredToolError(
                        "filesystem_race_detected",
                        "A dataset entry changed while the manifest was built",
                        {"reason": type(exc).__name__},
                    ) from exc
                if stat.S_ISDIR(metadata.st_mode):
                    child, _ = open_child_descriptor(
                        directory_descriptor,
                        entry.name,
                        expected=metadata,
                        require_directory=True,
                    )
                    try:
                        walk(child, relative)
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise StructuredToolError(
                        "special_file_rejected",
                        "Only regular files and directories are permitted in deterministic manifests",
                    )
                if is_sensitive_path(relative):
                    raise StructuredToolError(
                        "dataset_sensitive_path",
                        "Credential-like files cannot be included in a dataset manifest",
                    )
                if len(records) >= self.max_files:
                    raise StructuredToolError(
                        "dataset_file_limit",
                        "The dataset exceeds the configured manifest file limit",
                        {"max_files": self.max_files},
                    )
                child, opened_metadata = open_child_descriptor(
                    directory_descriptor,
                    entry.name,
                    expected=metadata,
                    require_file=True,
                )
                try:
                    records.append(
                        {
                            "path": relative,
                            "size_bytes": opened_metadata.st_size,
                            "sha256": descriptor_sha256(child),
                        }
                    )
                finally:
                    os.close(child)

        walk(dataset_descriptor, "")
        return records

    def _manifest_core(
        self, dataset_relative: str, dataset_id: str, files: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA,
            "dataset_id": dataset_id,
            "dataset_path": dataset_relative,
            "files": files,
            "file_count": len(files),
            "total_bytes": sum(record["size_bytes"] for record in files),
            "tree_sha256": canonical_sha256(files),
        }

    @staticmethod
    def _existing_manifest(directory_descriptor: int) -> bytes | None:
        try:
            metadata = os.stat(MANIFEST_NAME, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode):
            raise StructuredToolError(
                "manifest_symlink_rejected", "The canonical manifest path must not be a symlink"
            )
        child, _ = open_child_descriptor(
            directory_descriptor,
            MANIFEST_NAME,
            expected=metadata,
            require_file=True,
        )
        try:
            return read_regular_descriptor(child, max_bytes=MAX_MANIFEST_BYTES)
        finally:
            os.close(child)

    @classmethod
    def _atomic_write(cls, directory_descriptor: int, content: bytes) -> str:
        existing = cls._existing_manifest(directory_descriptor)
        if existing is not None:
            if existing == content:
                return "unchanged"
            try:
                existing_payload = json.loads(existing.decode("utf-8"))
                DatasetManifest.model_validate(existing_payload)
            except (UnicodeDecodeError, ValueError) as exc:
                raise StructuredToolError(
                    "manifest_overwrite_rejected",
                    "Refusing to overwrite a file that is not an EgoAgentOS dataset manifest",
                ) from exc
            raise StructuredToolError(
                "manifest_immutable",
                "A published dataset manifest is immutable; use a new versioned dataset path",
            )

        temporary_name = ".%s.%s.tmp" % (MANIFEST_NAME, secrets.token_hex(12))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_descriptor)
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            # Hard-link publication is atomic and, unlike replace(), never overwrites an
            # independently published manifest that won the race.
            try:
                os.link(
                    temporary_name,
                    MANIFEST_NAME,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                winner = cls._existing_manifest(directory_descriptor)
                if winner == content:
                    return "unchanged"
                raise StructuredToolError(
                    "manifest_immutable",
                    "A different manifest was published concurrently; use a new versioned dataset path",
                )
            os.fsync(directory_descriptor)
            return "created"
        finally:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass

    def create(self, dataset_path: str, dataset_id: str) -> dict[str, Any]:
        # Validate the public model-level identifier contract on direct calls too.
        if not dataset_id or len(dataset_id) > 128:
            raise StructuredToolError("invalid_dataset_id", "dataset_id is missing or too long")
        import re

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", dataset_id):
            raise StructuredToolError(
                "invalid_dataset_id", "dataset_id may contain only letters, digits, dot, dash, underscore"
            )
        with self.root.open_directory_descriptor(dataset_path) as (descriptor, normalised):
            files = self._scan(descriptor)
            core = self._manifest_core(normalised, dataset_id, files)
            manifest = {**core, "manifest_sha256": canonical_sha256(core)}
            # Round-trip through the strict schema before committing any bytes.
            DatasetManifest.model_validate(manifest)
            encoded = (canonical_json(manifest) + "\n").encode("utf-8")
            status = self._atomic_write(descriptor, encoded)
        manifest_path = MANIFEST_NAME if normalised == "." else "%s/%s" % (normalised, MANIFEST_NAME)
        return {
            "schema": "egoagentos.dataset-manifest-write.v1",
            "write_status": status,
            "manifest_path": manifest_path,
            "manifest": manifest,
            "synthetic_or_local_only": True,
        }

    def verify(self, dataset_path: str) -> dict[str, Any]:
        with self.root.open_directory_descriptor(dataset_path) as (descriptor, normalised):
            raw = self._existing_manifest(descriptor)
            if raw is None:
                raise StructuredToolError("manifest_not_found", "No canonical dataset manifest exists")
        try:
            decoded = raw.decode("utf-8")
            def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError(f"duplicate key: {key}")
                    result[key] = value
                return result

            payload = json.loads(
                decoded,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                object_pairs_hook=reject_duplicate_keys,
            )
            manifest = DatasetManifest.model_validate(payload)
        except (UnicodeDecodeError, ValueError) as exc:
            raise StructuredToolError(
                "invalid_manifest", "The dataset manifest is not valid strict UTF-8 JSON"
            ) from exc

        canonical_bytes = (
            canonical_json(manifest.model_dump(mode="json", by_alias=True)) + "\n"
        ).encode("utf-8")
        with self.root.open_directory_descriptor(dataset_path) as (
            scan_descriptor,
            scan_relative,
        ):
            if scan_relative != normalised:
                raise StructuredToolError(
                    "filesystem_race_detected", "Dataset path changed during verification"
                )
            files = self._scan(scan_descriptor)
        expected_core = self._manifest_core(normalised, manifest.dataset_id, files)
        expected = {**expected_core, "manifest_sha256": canonical_sha256(expected_core)}
        actual = manifest.model_dump(mode="json", by_alias=True)

        mismatches: list[str] = []
        if raw != canonical_bytes:
            mismatches.append("non_canonical_encoding")
        for key in (
            "schema",
            "dataset_id",
            "dataset_path",
            "files",
            "file_count",
            "total_bytes",
            "tree_sha256",
            "manifest_sha256",
        ):
            if actual.get(key) != expected.get(key):
                mismatches.append(key)
        return {
            "schema": "egoagentos.dataset-manifest-verification.v1",
            "valid": not mismatches,
            "mismatches": sorted(set(mismatches)),
            "manifest_path": MANIFEST_NAME if normalised == "." else "%s/%s" % (normalised, MANIFEST_NAME),
            "expected_manifest_sha256": expected["manifest_sha256"],
            "actual_manifest_sha256": actual["manifest_sha256"],
            "file_count": len(files),
        }


mcp = MCPServer(
    "egoagentos-dataset",
    version="0.1.0",
    instructions=(
        "Create and verify one canonical manifest below EGO_MCP_DATASET_ROOT. "
        "Server-side path and symlink policy is authoritative."
    ),
)


@mcp.tool(
    title="Create a canonical dataset manifest",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def dataset_create_manifest(dataset_path: str, dataset_id: str) -> dict[str, Any]:
    """Write .egoagentos-manifest.json atomically inside a trusted dataset directory."""

    return DatasetManifestService.from_env().create(dataset_path, dataset_id)


@mcp.tool(
    title="Verify a canonical dataset manifest",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def dataset_verify_manifest(dataset_path: str) -> dict[str, Any]:
    """Re-hash every regular dataset file and compare it to the canonical manifest."""

    return DatasetManifestService.from_env().verify(dataset_path)


def main() -> None:
    run_mcp_server(mcp)


if __name__ == "__main__":
    main()
