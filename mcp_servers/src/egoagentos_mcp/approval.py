"""Scoped, expiring, single-use HMAC approval tokens for mutating MCP tools."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from .common import StrictModel, StructuredToolError, canonical_json

TOKEN_PREFIX = "egoap1"
MAX_TOKEN_BYTES = 4_096
MAX_TOKEN_TTL_SECONDS = 900


class ApprovalClaims(StrictModel):
    version: Literal[1]
    jti: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")
    action: str = Field(min_length=1, max_length=128)
    scope: str = Field(min_length=1, max_length=512)
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)


class ReplayStore(Protocol):
    def consume(self, jti: str) -> bool:
        """Atomically return True only the first time a token id is consumed."""


class InMemoryReplayStore:
    """Process-local replay protection, primarily useful for tests and demos."""

    def __init__(self) -> None:
        self._consumed: set[str] = set()
        self._lock = threading.Lock()

    def consume(self, jti: str) -> bool:
        with self._lock:
            if jti in self._consumed:
                return False
            self._consumed.add(jti)
            return True


class FileReplayStore:
    """Cross-process replay protection using atomic exclusive nonce files."""

    def __init__(self, directory: str | Path) -> None:
        path = Path(directory).expanduser()
        if not path.is_absolute():
            raise StructuredToolError(
                "approval_replay_path_invalid", "The approval replay directory must be absolute"
            )
        if path.exists() and path.is_symlink():
            raise StructuredToolError(
                "approval_replay_symlink_rejected", "The approval replay directory cannot be a symlink"
            )
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory = path.resolve(strict=True)
        except OSError as exc:
            raise StructuredToolError(
                "approval_replay_store_unavailable",
                "The approval replay directory could not be prepared",
                {"reason": type(exc).__name__},
            ) from exc
        if not self.directory.is_dir():
            raise StructuredToolError(
                "approval_replay_path_invalid", "The approval replay path must be a directory"
            )

    def consume(self, jti: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", jti):
            raise StructuredToolError("approval_token_invalid", "The approval token is invalid")
        target = self.directory / jti
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags, 0o600)
        except FileExistsError:
            return False
        except OSError as exc:
            raise StructuredToolError(
                "approval_replay_store_unavailable",
                "The approval replay ledger could not be updated",
                {"reason": type(exc).__name__},
            ) from exc
        try:
            os.write(descriptor, b"consumed\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return True


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise StructuredToolError("approval_token_invalid", "The approval token is invalid")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise StructuredToolError("approval_token_invalid", "The approval token is invalid") from exc


class HMACApprovalManager:
    """Issue and validate exact-action approval tokens.

    `issue` is an operator-side library method and is deliberately not registered
    as an MCP tool. Servers use `validate_and_consume` only.
    """

    def __init__(
        self,
        secret: str | bytes,
        *,
        replay_store: ReplayStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(secret_bytes) < 32:
            raise StructuredToolError(
                "approval_secret_too_short", "The HMAC approval secret must be at least 32 bytes"
            )
        self._secret = bytes(secret_bytes)
        self._replay_store = replay_store or InMemoryReplayStore()
        self._clock = clock or time.time

    def _sign(self, payload: bytes) -> bytes:
        return hmac.new(self._secret, payload, hashlib.sha256).digest()

    def issue(
        self,
        *,
        jti: str,
        action: str,
        scope: str,
        action_digest: str,
        config_sha256: str,
        ttl_seconds: int = 300,
        now: int | None = None,
    ) -> str:
        """Mint a token for an already-approved action; never expose this via MCP."""

        if not 1 <= ttl_seconds <= MAX_TOKEN_TTL_SECONDS:
            raise StructuredToolError(
                "approval_ttl_invalid",
                f"Approval TTL must be between 1 and {MAX_TOKEN_TTL_SECONDS} seconds",
            )
        issued_at = int(self._clock() if now is None else now)
        claims = ApprovalClaims(
            version=1,
            jti=jti,
            action=action,
            scope=scope,
            action_digest=action_digest,
            config_sha256=config_sha256,
            issued_at=issued_at,
            expires_at=issued_at + ttl_seconds,
        )
        payload = canonical_json(claims).encode("utf-8")
        return f"{TOKEN_PREFIX}.{_b64encode(payload)}.{_b64encode(self._sign(payload))}"

    def validate_and_consume(
        self,
        token: str,
        *,
        expected_action: str,
        expected_scope: str,
        expected_digest: str,
        expected_config_sha256: str,
        now: int | None = None,
    ) -> ApprovalClaims:
        checked_at = int(self._clock() if now is None else now)
        if not isinstance(token, str) or len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
            raise StructuredToolError("approval_token_invalid", "The approval token is invalid")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
            raise StructuredToolError("approval_token_invalid", "The approval token is invalid")
        payload = _b64decode(parts[1])
        signature = _b64decode(parts[2])
        if not hmac.compare_digest(signature, self._sign(payload)):
            raise StructuredToolError("approval_token_invalid", "The approval token is invalid")
        try:
            decoded = json.loads(payload.decode("utf-8"))
            claims = ApprovalClaims.model_validate(decoded)
        except (UnicodeDecodeError, ValueError) as exc:
            raise StructuredToolError("approval_token_invalid", "The approval token is invalid") from exc

        if claims.expires_at <= claims.issued_at or (
            claims.expires_at - claims.issued_at > MAX_TOKEN_TTL_SECONDS
        ):
            raise StructuredToolError("approval_token_invalid", "The approval token is invalid")
        if claims.issued_at > checked_at + 30:
            raise StructuredToolError(
                "approval_token_not_yet_valid", "The approval token was issued in the future"
            )
        if checked_at >= claims.expires_at:
            raise StructuredToolError(
                "approval_token_expired",
                "The approval token has expired",
                {"expires_at": claims.expires_at},
            )
        mismatches: list[str] = []
        if not hmac.compare_digest(claims.action, expected_action):
            mismatches.append("action")
        if not hmac.compare_digest(claims.scope, expected_scope):
            mismatches.append("scope")
        if not hmac.compare_digest(claims.action_digest, expected_digest):
            mismatches.append("action_digest")
        if not hmac.compare_digest(claims.config_sha256, expected_config_sha256):
            mismatches.append("config_sha256")
        if mismatches:
            raise StructuredToolError(
                "approval_scope_mismatch",
                "The approval token is not valid for this exact action",
                {"mismatched_fields": mismatches},
            )
        if not self._replay_store.consume(claims.jti):
            raise StructuredToolError(
                "approval_token_replayed", "The approval token has already been consumed"
            )
        return claims
