"""Read-only repository snapshot and bounded text-reading MCP server."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .common import (
    StructuredToolError,
    TrustedRoot,
    canonical_sha256,
    descriptor_sha256,
    is_sensitive_path,
    open_child_descriptor,
    read_regular_descriptor,
    redact_text,
    run_mcp_server,
)

DEFAULT_MAX_FILES = 5_000
DEFAULT_MAX_FILE_BYTES = 256 * 1024
DEFAULT_MAX_TOTAL_BYTES = 1024 * 1024
DEFAULT_MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024 * 1024
IGNORED_DIRECTORIES = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache"}


class RepoService:
    """Policy implementation used by both MCP wrappers and direct unit tests."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
    ) -> None:
        self.root = TrustedRoot(root, label="repository root")
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.max_snapshot_bytes = max_snapshot_bytes

    @classmethod
    def from_env(cls) -> RepoService:
        root = TrustedRoot.from_env("EGO_MCP_REPO_ROOT", label="repository root")
        return cls(root.path)

    def _walk_snapshot(
        self, directory_descriptor: int, base_relative: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        files: list[dict[str, Any]] = []
        exclusions: list[dict[str, str]] = []
        total_snapshot_bytes = 0

        def child_relative(parent: str, name: str) -> str:
            return name if parent == "." else "%s/%s" % (parent, name)

        def walk(current_descriptor: int, current_relative: str) -> None:
            nonlocal total_snapshot_bytes
            try:
                with os.scandir(current_descriptor) as scanner:
                    entries = sorted(scanner, key=lambda item: item.name)
            except OSError as exc:
                raise StructuredToolError(
                    "directory_read_failed",
                    "A repository directory could not be read",
                    {"reason": type(exc).__name__},
                ) from exc
            for entry in entries:
                relative = child_relative(current_relative, entry.name)
                if entry.is_symlink():
                    exclusions.append({"path": relative, "reason": "symlink_not_followed"})
                    continue
                if entry.name in IGNORED_DIRECTORIES and entry.is_dir(follow_symlinks=False):
                    exclusions.append({"path": relative, "reason": "metadata_directory"})
                    continue
                if is_sensitive_path(relative):
                    exclusions.append({"path": relative, "reason": "sensitive_path"})
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise StructuredToolError(
                        "filesystem_race_detected",
                        "A repository entry changed during snapshotting",
                        {"reason": type(exc).__name__},
                    ) from exc
                if stat.S_ISDIR(metadata.st_mode):
                    child_descriptor, _ = open_child_descriptor(
                        current_descriptor,
                        entry.name,
                        expected=metadata,
                        require_directory=True,
                    )
                    try:
                        walk(child_descriptor, relative)
                    finally:
                        os.close(child_descriptor)
                elif stat.S_ISREG(metadata.st_mode):
                    if len(files) >= self.max_files:
                        raise StructuredToolError(
                            "snapshot_file_limit",
                            "The repository snapshot exceeds its configured file limit",
                            {"max_files": self.max_files},
                        )
                    file_descriptor, opened_metadata = open_child_descriptor(
                        current_descriptor,
                        entry.name,
                        expected=metadata,
                        require_file=True,
                    )
                    total_snapshot_bytes += opened_metadata.st_size
                    if total_snapshot_bytes > self.max_snapshot_bytes:
                        os.close(file_descriptor)
                        raise StructuredToolError(
                            "snapshot_byte_limit",
                            "The repository snapshot exceeds its configured byte limit",
                            {"max_snapshot_bytes": self.max_snapshot_bytes},
                        )
                    try:
                        files.append(
                            {
                                "path": relative,
                                "size_bytes": opened_metadata.st_size,
                                "sha256": descriptor_sha256(file_descriptor),
                            }
                        )
                    finally:
                        os.close(file_descriptor)
                else:
                    exclusions.append({"path": relative, "reason": "special_file"})

        walk(directory_descriptor, base_relative)
        return files, exclusions

    def snapshot(self, relative_path: str = ".") -> dict[str, Any]:
        with self.root.open_directory_descriptor(relative_path) as (descriptor, normalised):
            files, exclusions = self._walk_snapshot(descriptor, normalised)
        core = {
            "schema": "egoagentos.repo-snapshot.v1",
            "root": normalised,
            "read_only": True,
            "files": files,
            "excluded": exclusions,
            "file_count": len(files),
            "total_bytes": sum(item["size_bytes"] for item in files),
        }
        return {**core, "snapshot_sha256": canonical_sha256(core)}

    def read_files(self, paths: list[str]) -> dict[str, Any]:
        if not paths or len(paths) > 20:
            raise StructuredToolError(
                "invalid_file_batch", "Request between 1 and 20 repository files"
            )
        if len(set(paths)) != len(paths):
            raise StructuredToolError("duplicate_path", "Duplicate repository paths are forbidden")

        total = 0
        results: list[dict[str, Any]] = []
        for relative in paths:
            if is_sensitive_path(relative):
                raise StructuredToolError(
                    "sensitive_path_rejected", "Credential-like repository paths cannot be read"
                )
            with self.root.open_file_descriptor(relative) as (
                descriptor,
                normalised,
                _metadata,
            ):
                content = read_regular_descriptor(descriptor, max_bytes=self.max_file_bytes)
            total += len(content)
            if total > self.max_total_bytes:
                raise StructuredToolError(
                    "batch_too_large",
                    "The repository read batch exceeds its total byte limit",
                    {"max_total_bytes": self.max_total_bytes},
                )
            if b"\x00" in content:
                raise StructuredToolError(
                    "binary_file_rejected", "Repository read_files only returns UTF-8 text"
                )
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise StructuredToolError(
                    "non_utf8_file_rejected", "Repository read_files only returns UTF-8 text"
                ) from exc
            redacted = redact_text(text)
            results.append(
                {
                    "path": PurePosixPath(normalised).as_posix(),
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content": redacted,
                    "redaction_applied": redacted != text,
                }
            )
        return {
            "schema": "egoagentos.repo-read.v1",
            "read_only": True,
            "files": results,
            "total_bytes": total,
        }


mcp = MCPServer(
    "egoagentos-repo",
    version="0.1.0",
    instructions="Read-only repository tools. Server-side trusted-root policy is authoritative.",
)


@mcp.tool(
    title="Create a deterministic repository snapshot",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def repo_snapshot(relative_path: str = ".") -> dict[str, Any]:
    """Hash a repository subtree without following symlinks or returning file contents."""

    return RepoService.from_env().snapshot(relative_path)


@mcp.tool(
    title="Read bounded repository text files",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def repo_read_files(paths: list[str]) -> dict[str, Any]:
    """Read up to 20 UTF-8 files below EGO_MCP_REPO_ROOT with secret redaction."""

    return RepoService.from_env().read_files(paths)


def main() -> None:
    run_mcp_server(mcp)


if __name__ == "__main__":
    main()
