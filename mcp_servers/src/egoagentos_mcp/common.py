"""Shared deterministic encoding, safe-path, error, and redaction primitives."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import errno
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict

MAX_RELATIVE_PATH_LENGTH = 1_024
REDACTED = "<redacted>"


class StrictModel(BaseModel):
    """Pydantic base model that rejects undeclared tool fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


def _normalise(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalise(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numbers cannot be canonically encoded")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return stable UTF-8 JSON text with no insignificant whitespace."""

    return json.dumps(
        _normalise(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """Hash a regular non-symlink file without following a final symlink."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StructuredToolError(
            "file_open_failed",
            "The requested file could not be opened safely",
            {"reason": type(exc).__name__},
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StructuredToolError("not_regular_file", "Only regular files are allowed")
        return descriptor_sha256(descriptor)
    finally:
        os.close(descriptor)


_SENSITIVE_EXACT_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "known_hosts",
    "netrc",
    "secrets.json",
}
_SENSITIVE_SUFFIXES = (".key", ".p12", ".pfx", ".pem")


def is_sensitive_path(path: str | Path) -> bool:
    """Conservatively identify files that should never be returned by a tool."""

    parts = [part.lower() for part in Path(path).parts]
    for part in parts:
        if part in {".git", ".ssh", ".aws", ".gnupg"}:
            return True
    name = parts[-1] if parts else ""
    return (
        name in _SENSITIVE_EXACT_NAMES
        or name.startswith((".env.", "credentials.", "secrets."))
        or name.endswith(_SENSITIVE_SUFFIXES)
    )


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_NAME = (
    r"[A-Za-z0-9_-]*(?:api[_-]?key|access[_-]?key[_-]?secret|"
    r"secret[_-]?access[_-]?key|access[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|password|secret|token)"
)
_ASSIGNMENT_SECRET_RE = re.compile(
    rf"(?im)^(\s*(?:export\s+)?{_SECRET_NAME}\s*[:=]\s*)"
    r"(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s#]+)"
)
_JSON_SECRET_RE = re.compile(
    rf'(?i)("{_SECRET_NAME}"\s*:\s*)"[^"]*"'
)


def redact_text(value: str) -> str:
    """Redact common credential forms before data crosses the MCP boundary."""

    value = _PRIVATE_KEY_RE.sub(REDACTED, value)
    value = _BEARER_RE.sub(f"Bearer {REDACTED}", value)
    value = _ASSIGNMENT_SECRET_RE.sub(lambda match: match.group(1) + REDACTED, value)
    return _JSON_SECRET_RE.sub(lambda match: match.group(1) + f'"{REDACTED}"', value)


def redact_value(value: Any) -> Any:
    """Recursively redact values used in error details and tool output."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in ("password", "secret", "token", "key")):
                redacted[str(key)] = REDACTED
            else:
                redacted[str(key)] = redact_value(item)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    return value


class StructuredToolError(Exception):
    """An MCP-safe error whose string form remains machine-readable JSON."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = redact_value(dict(details or {}))
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }

    def __str__(self) -> str:
        return canonical_json(self.as_dict())


class TrustedRoot:
    """Resolve caller paths beneath one operator-configured canonical directory.

    Caller-controlled absolute paths, parent traversal, and every symlink in the
    requested path are rejected. Recursive users must also refuse symlink entries
    while walking; this class intentionally never grants a path by prefix matching.
    """

    def __init__(self, root: str | Path, *, label: str) -> None:
        supplied = Path(root).expanduser()
        if not supplied.is_absolute():
            raise StructuredToolError(
                "trusted_root_not_absolute",
                f"The configured {label} must be an absolute path",
            )
        if supplied.is_symlink():
            raise StructuredToolError(
                "trusted_root_symlink_rejected",
                f"The configured {label} itself must not be a symlink",
            )
        try:
            resolved = supplied.resolve(strict=True)
        except OSError as exc:
            raise StructuredToolError(
                "trusted_root_unavailable",
                f"The configured {label} is unavailable",
                {"reason": type(exc).__name__},
            ) from exc
        if not resolved.is_dir():
            raise StructuredToolError(
                "trusted_root_not_directory", f"The configured {label} must be a directory"
            )
        self.path = resolved
        self.label = label

    @classmethod
    def from_env(cls, variable: str, *, label: str) -> TrustedRoot:
        value = os.environ.get(variable)
        if not value:
            raise StructuredToolError(
                "configuration_missing",
                f"Set {variable} to an absolute trusted directory before using this tool",
                {"environment_variable": variable},
            )
        return cls(value, label=label)

    def _relative_parts(self, relative: str) -> tuple[str, ...]:
        if not isinstance(relative, str) or not relative or len(relative) > MAX_RELATIVE_PATH_LENGTH:
            raise StructuredToolError("invalid_path", "A non-empty bounded relative path is required")
        if "\x00" in relative or "\\" in relative:
            raise StructuredToolError("invalid_path", "NUL and backslash path syntax are forbidden")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part == ".." for part in pure.parts):
            raise StructuredToolError(
                "path_outside_trusted_root", "Absolute paths and parent traversal are forbidden"
            )
        return tuple(part for part in pure.parts if part not in ("", "."))

    def _assert_contained(self, path: Path) -> None:
        try:
            common = os.path.commonpath((str(self.path), str(path)))
        except ValueError as exc:
            raise StructuredToolError(
                "path_outside_trusted_root", "The path is outside the configured trusted root"
            ) from exc
        if common != str(self.path):
            raise StructuredToolError(
                "path_outside_trusted_root", "The path is outside the configured trusted root"
            )

    def _reject_symlink_chain(self, parts: Sequence[str]) -> Path:
        current = self.path
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise StructuredToolError(
                    "symlink_rejected", "Symlinks are not permitted in trusted tool paths"
                )
        return current

    def resolve_existing(
        self,
        relative: str,
        *,
        require_file: bool = False,
        require_directory: bool = False,
    ) -> Path:
        parts = self._relative_parts(relative)
        candidate = self._reject_symlink_chain(parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise StructuredToolError(
                "path_not_found", "The requested path does not exist", {"reason": type(exc).__name__}
            ) from exc
        self._assert_contained(resolved)
        if require_file and not resolved.is_file():
            raise StructuredToolError("not_regular_file", "The requested path is not a regular file")
        if require_directory and not resolved.is_dir():
            raise StructuredToolError("not_directory", "The requested path is not a directory")
        return resolved

    def relative(self, path: Path) -> str:
        self._assert_contained(path)
        relative = path.relative_to(self.path).as_posix()
        return relative or "."

    def normalised_relative(self, relative: str) -> str:
        """Validate public path syntax and return a canonical POSIX relative path."""

        parts = self._relative_parts(relative)
        return "/".join(parts) or "."

    @contextmanager
    def open_directory_descriptor(self, relative: str) -> Iterator[tuple[int, str]]:
        """Open a directory capability without following any path component symlink."""

        parts = self._relative_parts(relative)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(self.path, flags)
            root_metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(root_metadata.st_mode):
                raise StructuredToolError("trusted_root_not_directory", "Trusted root is not a directory")
            for part in parts:
                child, _ = open_child_descriptor(
                    descriptor,
                    part,
                    require_directory=True,
                )
                os.close(descriptor)
                descriptor = child
            yield descriptor, "/".join(parts) or "."
        except OSError as exc:
            raise StructuredToolError(
                "path_open_failed",
                "The requested directory could not be opened without following symlinks",
                {"reason": type(exc).__name__},
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def open_file_descriptor(self, relative: str) -> Iterator[tuple[int, str, os.stat_result]]:
        """Open a regular-file capability with descriptor-relative path traversal."""

        parts = self._relative_parts(relative)
        if not parts:
            raise StructuredToolError("not_regular_file", "A file path is required")
        parent = "/".join(parts[:-1]) or "."
        with self.open_directory_descriptor(parent) as (directory_descriptor, _):
            child, metadata = open_child_descriptor(
                directory_descriptor,
                parts[-1],
                require_file=True,
            )
            try:
                yield child, "/".join(parts), metadata
            finally:
                os.close(child)


def open_child_descriptor(
    parent_descriptor: int,
    name: str,
    *,
    expected: os.stat_result | None = None,
    require_file: bool = False,
    require_directory: bool = False,
) -> tuple[int, os.stat_result]:
    """Open one already-enumerated child and reject check/use replacement races."""

    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise StructuredToolError("invalid_path", "Invalid descriptor-relative child name")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if require_directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        code = (
            "filesystem_race_detected"
            if expected is not None
            else "symlink_rejected"
            if exc.errno == errno.ELOOP
            else "path_open_failed"
        )
        raise StructuredToolError(
            code,
            "A filesystem entry changed or could not be opened safely",
            {"reason": type(exc).__name__},
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if expected is not None and (
            metadata.st_dev != expected.st_dev or metadata.st_ino != expected.st_ino
        ):
            raise StructuredToolError(
                "filesystem_race_detected",
                "A filesystem entry was replaced between enumeration and open",
            )
        if require_file and not stat.S_ISREG(metadata.st_mode):
            raise StructuredToolError("not_regular_file", "Only regular files are allowed")
        if require_directory and not stat.S_ISDIR(metadata.st_mode):
            raise StructuredToolError("not_directory", "Only directories are allowed")
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def descriptor_sha256(descriptor: int) -> str:
    """Hash bytes from an already-authorized regular-file descriptor."""

    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise StructuredToolError("not_regular_file", "Only regular files are allowed")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def read_regular_descriptor(descriptor: int, *, max_bytes: int) -> bytes:
    """Read bounded bytes from an already-authorized regular-file descriptor."""

    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise StructuredToolError("not_regular_file", "Only regular files may be read")
    if metadata.st_size > max_bytes:
        raise StructuredToolError(
            "file_too_large",
            "The requested file exceeds the read limit",
            {"size_bytes": metadata.st_size, "max_bytes": max_bytes},
        )
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(remaining, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > max_bytes:
        raise StructuredToolError("file_too_large", "The requested file grew beyond the read limit")
    return content


def read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    """Read a bounded regular file with final-component no-follow semantics."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StructuredToolError(
            "file_open_failed",
            "The requested file could not be opened safely",
            {"reason": type(exc).__name__},
        ) from exc
    try:
        return read_regular_descriptor(descriptor, max_bytes=max_bytes)
    finally:
        os.close(descriptor)


def require_regular_entry(entry: os.DirEntry[str]) -> None:
    """Reject special filesystem nodes during deterministic recursive walks."""

    if entry.is_symlink():
        raise StructuredToolError(
            "symlink_rejected", "Symlinks are not permitted in deterministic manifests"
        )
    try:
        mode = entry.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise StructuredToolError(
            "filesystem_race_detected",
            "A filesystem entry changed while it was being inspected",
            {"reason": type(exc).__name__},
        ) from exc
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise StructuredToolError(
            "special_file_rejected",
            "Only regular files and directories are permitted in deterministic manifests",
        )


def iter_tree_entries(root: Path) -> Iterator[os.DirEntry[str]]:
    """Yield a stable depth-first walk without following symlinks."""

    with os.scandir(root) as scanner:
        entries = sorted(scanner, key=lambda item: item.name)
    for entry in entries:
        require_regular_entry(entry)
        yield entry
        if entry.is_dir(follow_symlinks=False):
            yield from iter_tree_entries(Path(entry.path))


def run_mcp_server(server: Any) -> None:
    """Run stdio by default, with an explicit loopback HTTP deployment profile."""

    transport = os.environ.get("EGO_MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if transport != "streamable-http":
        raise StructuredToolError(
            "transport_invalid", "EGO_MCP_TRANSPORT must be stdio or streamable-http"
        )
    host = os.environ.get("EGO_MCP_HOST", "127.0.0.1").strip()
    try:
        port = int(os.environ.get("EGO_MCP_PORT", "8000"))
    except ValueError as exc:
        raise StructuredToolError("transport_port_invalid", "EGO_MCP_PORT must be an integer") from exc
    if not host or not 1 <= port <= 65_535:
        raise StructuredToolError(
            "transport_endpoint_invalid", "A non-empty host and port in 1..65535 are required"
        )
    server.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=False,
    )
