"""Exception hierarchy for the imessagerelay client library.

All exceptions raised by this library ultimately inherit from
:class:`IMessageRelayError`, so callers can catch everything with a single
``except IMessageRelayError``. HTTP-level failures map to :class:`APIError`
subclasses via :func:`error_for_status`.
"""

from __future__ import annotations

__all__ = [
    "IMessageRelayError",
    "IMessageRelayConnectionError",
    "IMessageRelayTimeoutError",
    "APIError",
    "AuthenticationError",
    "ForbiddenError",
    "ValidationError",
    "NotFoundError",
    "ConflictError",
    "UnsupportedMediaTypeError",
    "RateLimitError",
    "ServerError",
    "error_for_status",
]


class IMessageRelayError(Exception):
    """Base class for every error raised by this library."""


class IMessageRelayConnectionError(IMessageRelayError):
    """Raised on network/transport failure (DNS, connection refused, TLS, etc.)."""


class IMessageRelayTimeoutError(IMessageRelayConnectionError):
    """Raised when a request exceeds its configured timeout."""


class APIError(IMessageRelayError):
    """Raised when the server returns an HTTP error response.

    Args:
        message: Human-readable error message, typically the server's
            ``error`` field.
        status_code: The HTTP status code of the response.
        payload: The decoded JSON body of the response, if any.

    Attributes:
        message: The error message.
        status_code: The HTTP status code.
        payload: The decoded JSON body, or ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        payload: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.status_code: int = status_code
        self.payload: dict | None = payload

    def __str__(self) -> str:
        return f"[{self.status_code}] {self.message}"


class AuthenticationError(APIError):
    """Raised for HTTP 401 responses (unknown, revoked, or missing token)."""


class ForbiddenError(APIError):
    """Raised for HTTP 403 responses (alias not configured or not permitted)."""


class ValidationError(APIError):
    """Raised for HTTP 400 responses (request failed server-side validation)."""


class NotFoundError(APIError):
    """Raised for HTTP 404 responses (job or resource not found)."""


class ConflictError(APIError):
    """Raised for HTTP 409 responses (idempotency-key payload mismatch)."""


class UnsupportedMediaTypeError(APIError):
    """Raised for HTTP 415 responses (Content-Type was not application/json)."""


class RateLimitError(APIError):
    """Raised for HTTP 429 responses (rate limit exceeded)."""


class ServerError(APIError):
    """Raised for HTTP 5xx responses (server-side failure)."""


def error_for_status(
    status_code: int,
    message: str,
    payload: dict | None,
) -> APIError:
    """Build the appropriate :class:`APIError` subclass for a status code.

    Args:
        status_code: The HTTP status code of the error response.
        message: The error message to attach.
        payload: The decoded JSON body of the response, if any.

    Returns:
        An :class:`APIError` instance of the most specific matching subclass.
        Unmapped 4xx codes and anything outside the known mapping fall back to
        the base :class:`APIError`.
    """
    mapping: dict[int, type[APIError]] = {
        400: ValidationError,
        401: AuthenticationError,
        403: ForbiddenError,
        404: NotFoundError,
        409: ConflictError,
        415: UnsupportedMediaTypeError,
        429: RateLimitError,
    }
    exc_type: type[APIError]
    if status_code in mapping:
        exc_type = mapping[status_code]
    elif 500 <= status_code <= 599:
        exc_type = ServerError
    else:
        exc_type = APIError
    return exc_type(message, status_code=status_code, payload=payload)
