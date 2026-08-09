"""Canonical hashing utilities used for artifacts, actions, runs, and audit events."""

import hashlib
import json
import math
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from .models import RunManifest


def _normalise(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalise(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numbers cannot be canonically hashed")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError("unsupported canonical value: %s" % type(value).__name__)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalise(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def run_manifest_digest(manifest: RunManifest) -> str:
    return canonical_sha256(manifest)
