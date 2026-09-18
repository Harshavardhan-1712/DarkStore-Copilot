"""Typed failures. Every one maps to a clean HTTP status — no unhandled 500s reach the picker."""


class AppError(Exception):
    status = 400
    code = "BAD_REQUEST"

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_body(self):
        return {"ok": False, "error": {"code": self.code, "message": self.message, "detail": self.detail}}


class NotFound(AppError):
    status = 404
    code = "NOT_FOUND"


class Conflict(AppError):
    """State transition or stock condition failed. Safe to retry after refetching the order."""
    status = 409
    code = "CONFLICT"


class GuardrailRejection(AppError):
    """Bedrock produced something the backend refuses to act on. Picker falls back to manual."""
    status = 422
    code = "GUARDRAIL_REJECTED"


class PolicyViolation(AppError):
    status = 422
    code = "POLICY_VIOLATION"


class ServiceUnavailable(AppError):
    status = 503
    code = "SERVICE_UNAVAILABLE"
