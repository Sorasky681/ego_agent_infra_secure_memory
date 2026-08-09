"""Structured control-plane errors."""

from typing import Any, Dict, Optional


class ControlPlaneError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class NotFoundError(ControlPlaneError):
    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(
            "not_found",
            "%s '%s' was not found" % (resource, identifier),
            404,
            {"resource": resource, "identifier": identifier},
        )


class ConflictError(ControlPlaneError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(code, message, 409, details)


class PolicyError(ControlPlaneError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(code, message, 403, details)
