"""Application error definitions and handlers."""

from enum import Enum
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .logging import get_logger

logger = get_logger(__name__)


class ErrorCode(str, Enum):
    """Standard error codes used throughout the application."""

    # Authentication & Authorization
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INSUFFICIENT_PERMISSIONS = "INSUFFICIENT_PERMISSIONS"

    # Plan & Limits
    PLAN_LIMIT = "PLAN_LIMIT"

    # Channel & Predictions
    CHANNEL_NOT_FOUND = "CHANNEL_NOT_FOUND"
    CHANNEL_ALREADY_EXISTS = "CHANNEL_ALREADY_EXISTS"
    PREDICTION_NOT_FOUND = "PREDICTION_NOT_FOUND"
    VOTING_PAUSED = "VOTING_PAUSED"

    # Token & Integration
    TOKEN_NEEDS_REAUTH = "TOKEN_NEEDS_REAUTH"
    KICK_API_ERROR = "KICK_API_ERROR"
    KICK_SCHEMA_ERROR = "KICK_SCHEMA_ERROR"

    # Billing
    BILLING_ERROR = "BILLING_ERROR"
    SUBSCRIPTION_NOT_FOUND = "SUBSCRIPTION_NOT_FOUND"

    # Validation
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"


class ErrorResponse(BaseModel):
    """Standard error response format."""

    error: dict[str, Any]

    @classmethod
    def create(
        cls,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> "ErrorResponse":
        """Create a standardized error response."""
        return cls(
            error={
                "code": code.value,
                "message": message,
                "details": details or {},
            }
        )


class AppException(Exception):
    """Base application exception with error code and details."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)


class PlanLimitException(AppException):
    """Raised when user hits their plan limit."""

    def __init__(self, message: str = "Plan limit reached"):
        super().__init__(
            code=ErrorCode.PLAN_LIMIT,
            message=message,
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
        )


class UnauthorizedError(AppException):
    """Raised when the request is not authenticated."""

    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            code=ErrorCode.INVALID_CREDENTIALS,
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class ValidationError(AppException):
    """Raised when request data fails validation rules."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=message,
            details=details,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


class PlanLimitError(PlanLimitException):
    """Compatibility alias for plan-limit errors across the app."""


class NotFoundError(AppException):
    """Raised when a requested database record is missing."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            code=ErrorCode.NOT_FOUND,
            message=message,
            details=details,
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ConflictError(AppException):
    """Raised when a resource already exists or conflicts."""

    def __init__(
        self, code: str | ErrorCode, message: str, details: dict[str, Any] | None = None
    ):
        error_code = code if isinstance(code, ErrorCode) else ErrorCode(code)
        super().__init__(
            code=error_code,
            message=message,
            details=details,
            status_code=status.HTTP_409_CONFLICT,
        )


class KickSchemaError(AppException):
    """Raised when Kick API returns unexpected data format."""

    def __init__(self, message: str = "Kick API schema mismatch"):
        super().__init__(
            code=ErrorCode.KICK_SCHEMA_ERROR,
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class TokenNeedsReauthError(AppException):
    """Raised when user's Kick token needs reauthorization."""

    def __init__(self, message: str = "Kick token needs reauthorization"):
        super().__init__(
            code=ErrorCode.TOKEN_NEEDS_REAUTH,
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handle application exceptions and return standardized error responses."""
    logger.warning(
        "Application exception occurred",
        error_code=exc.code.value,
        message=exc.message,
        path=str(request.url.path),
        method=request.method,
        details=exc.details,
    )

    error_response = ErrorResponse.create(
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(),
    )


async def validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Handle Pydantic validation errors."""
    logger.warning(
        "Validation error occurred",
        path=str(request.url.path),
        method=request.method,
        error_type=type(exc).__name__,
        error_count=len(getattr(exc, "errors", lambda: [])()),
    )

    error_response = ErrorResponse.create(
        code=ErrorCode.VALIDATION_ERROR,
        message="Validation failed",
        details={
            "validation_errors": [
                {
                    "location": list(error.get("loc", ())),
                    "type": error.get("type", "value_error"),
                }
                for error in getattr(exc, "errors", lambda: [])()
            ]
        },
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_response.model_dump(),
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle FastAPI HTTP exceptions."""
    logger.warning(
        "HTTP exception occurred",
        status_code=exc.status_code,
        detail=exc.detail,
        path=str(request.url.path),
        method=request.method,
    )

    # Map status codes to error codes
    code_mapping = {
        404: ErrorCode.NOT_FOUND,
        401: ErrorCode.INVALID_CREDENTIALS,
        403: ErrorCode.INSUFFICIENT_PERMISSIONS,
        429: ErrorCode.RATE_LIMITED,
        402: ErrorCode.PLAN_LIMIT,
    }

    error_code = code_mapping.get(exc.status_code, ErrorCode.INTERNAL_ERROR)

    error_response = ErrorResponse.create(
        code=error_code,
        message=exc.detail or "An error occurred",
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(),
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    logger.error(
        "Unexpected exception occurred",
        error_type=type(exc).__name__,
        path=str(request.url.path),
        method=request.method,
    )

    error_response = ErrorResponse.create(
        code=ErrorCode.INTERNAL_ERROR,
        message="An internal error occurred",
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response.model_dump(),
    )
