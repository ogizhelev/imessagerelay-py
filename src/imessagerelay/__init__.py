"""imessagerelay: async Python client for the iMessageRelay HTTP API.

Example:
    >>> import asyncio
    >>> from imessagerelay import IMessageRelayClient
    >>>
    >>> async def main() -> None:
    ...     async with IMessageRelayClient("10.0.0.5", "my-token") as client:
    ...         job = await client.send_message("hello", to="mom")
    ...         print(job.id, job.state)
    >>>
    >>> asyncio.run(main())  # doctest: +SKIP
"""

from __future__ import annotations

from .client import IMessageRelayClient
from .exceptions import (
    APIError,
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    IMessageRelayConnectionError,
    IMessageRelayError,
    IMessageRelayTimeoutError,
    NotFoundError,
    RateLimitError,
    ServerError,
    UnsupportedMediaTypeError,
    ValidationError,
    error_for_status,
)
from .models import Alias, Attachment, Health, Job, JobState

__version__ = "0.1.0"

__all__ = [
    "IMessageRelayClient",
    "Job",
    "JobState",
    "Health",
    "Alias",
    "Attachment",
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
    "__version__",
]
