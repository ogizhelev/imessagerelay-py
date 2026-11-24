"""Async HTTP client for the iMessageRelay server.

:class:`IMessageRelayClient` wraps the iMessageRelay HTTP API in a clean,
async-first, strongly-typed interface suitable for embedding in arbitrary
automations.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Sequence
from types import TracebackType
from typing import Any, cast
from urllib.parse import quote, urlsplit

import httpx

from .exceptions import (
    IMessageRelayConnectionError,
    IMessageRelayTimeoutError,
    error_for_status,
)
from .models import Alias, Attachment, Health, Job

__all__ = ["IMessageRelayClient"]

logger = logging.getLogger("imessagerelay")

_DEFAULT_PORT = 8443
_DEFAULT_SCHEME = "https"


class IMessageRelayClient:
    """Async client for the iMessageRelay HTTP API.

    The client talks to a SwiftNIO HTTPS server that typically presents a
    bundled self-signed certificate, so TLS verification is disabled by
    default (``verify_ssl=False``). All endpoints except ``/health`` and
    ``/debug`` require a bearer token.

    The client can own its underlying :class:`httpx.AsyncClient` (created
    lazily on first use and closed by :meth:`aclose`) or borrow one injected
    by the caller (never closed by this client). Use it as an async context
    manager to guarantee cleanup::

        async with IMessageRelayClient("10.0.0.5", token) as client:
            job = await client.send_message("hello", to="mom")

    Args:
        host: Server host. A scheme and/or port embedded here (for example
            ``"https://10.0.0.5:8443"``) are stripped and honoured.
        token: Bearer token used to authenticate requests.
        port: Server port. Defaults to ``8443``.
        verify_ssl: Whether to verify the server's TLS certificate. Defaults
            to ``False`` for the common self-signed deployment.
        timeout: Per-request timeout in seconds.
        scheme: URL scheme, normally ``"https"``.
        client: An optional pre-built :class:`httpx.AsyncClient` to use. When
            provided it is not closed by :meth:`aclose`.
    """

    def __init__(
        self,
        host: str,
        token: str,
        *,
        port: int = _DEFAULT_PORT,
        verify_ssl: bool = False,
        timeout: float = 30.0,
        scheme: str = _DEFAULT_SCHEME,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        host, embedded_port = self._split_host_port(host)

        if embedded_port is not None and port == _DEFAULT_PORT:
            # Prefer a port embedded in the host unless the caller explicitly
            # overrode the default port kwarg.
            port = embedded_port

        self._host: str = host
        self._port: int = port
        self._scheme: str = scheme
        self._token: str = token
        self._verify_ssl: bool = verify_ssl
        self._timeout: float = timeout
        self._base_url: str = f"{scheme}://{host}:{port}"

        self._client: httpx.AsyncClient | None = client
        self._owns_client: bool = client is None

    @staticmethod
    def _split_host_port(host: str) -> tuple[str, int | None]:
        """Strip any scheme/trailing slash and split off an embedded port.

        Args:
            host: A raw host value, possibly like ``"https://10.0.0.5:8443/"``.

        Returns:
            A ``(host, port)`` tuple where ``port`` is ``None`` when no integer
            port was embedded.
        """
        value = host.strip()
        if "://" in value:
            value = value.split("://", 1)[1]
        value = value.strip("/")
        port: int | None = None
        if ":" in value:
            host_part, _, port_part = value.rpartition(":")
            try:
                port = int(port_part)
                value = host_part
            except ValueError:
                port = None
        return value, port

    @classmethod
    def from_url(cls, url: str, token: str, **kwargs: object) -> IMessageRelayClient:
        """Construct a client from a full base URL.

        Args:
            url: A URL such as ``"https://host:port"``.
            token: Bearer token used to authenticate requests.
            **kwargs: Extra keyword arguments forwarded to :meth:`__init__`.

        Returns:
            A configured :class:`IMessageRelayClient`.
        """
        parts = urlsplit(url)
        scheme = parts.scheme or _DEFAULT_SCHEME
        host = parts.hostname or ""
        port = parts.port or _DEFAULT_PORT
        kwargs.setdefault("scheme", scheme)  # type: ignore[arg-type]
        kwargs.setdefault("port", port)  # type: ignore[arg-type]
        return cls(host, token, **kwargs)  # type: ignore[arg-type]

    @property
    def base_url(self) -> str:
        """The fully-qualified base URL of the server."""
        return self._base_url

    def _get_client(self) -> httpx.AsyncClient:
        """Return the underlying client, lazily creating an owned one."""
        if self._client is None:
            self._client = httpx.AsyncClient(verify=self._verify_ssl, timeout=self._timeout)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        """Close the underlying client if (and only if) this client owns it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> IMessageRelayClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        """Perform an HTTP request and return the decoded JSON body.

        Args:
            method: HTTP method (e.g. ``"GET"``, ``"POST"``).
            path: Request path beginning with ``/``.
            json: JSON body to send, if any.
            headers: Extra request headers.
            auth: Whether to attach the ``Authorization`` bearer header.

        Returns:
            The decoded JSON object, or an empty dict when there is no body.

        Raises:
            IMessageRelayTimeoutError: If the request times out.
            IMessageRelayConnectionError: On any other transport failure.
            APIError: If the server returns a 4xx/5xx status.
        """
        request_headers: dict[str, str] = {}
        if auth:
            request_headers["Authorization"] = f"Bearer {self._token}"
        if headers:
            request_headers.update(headers)

        url = f"{self._base_url}{path}"
        client = self._get_client()

        logger.debug("%s %s", method, url)
        try:
            response = await client.request(method, url, json=json, headers=request_headers)
        except httpx.TimeoutException as exc:
            raise IMessageRelayTimeoutError(f"Request to {url} timed out") from exc
        except httpx.TransportError as exc:
            raise IMessageRelayConnectionError(f"Failed to reach {url}: {exc}") from exc

        payload = self._decode_json(response)

        if response.status_code >= 400:
            message = ""
            if isinstance(payload, dict):
                raw_message = payload.get("error")
                if isinstance(raw_message, str):
                    message = raw_message
            if not message:
                message = response.reason_phrase or f"HTTP {response.status_code}"
            raise error_for_status(
                response.status_code,
                message,
                payload if isinstance(payload, dict) else None,
            )

        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _decode_json(response: httpx.Response) -> dict[str, Any] | None:
        """Decode a response body as JSON, tolerating an empty body.

        Args:
            response: The HTTP response.

        Returns:
            The decoded dict, or ``None`` if the body is empty or not an object.
        """
        if not response.content:
            return None
        try:
            data: Any = response.json()
        except ValueError:
            return None
        if isinstance(data, dict):
            return cast("dict[str, Any]", data)
        return None

    async def health(self) -> Health:
        """Fetch the server's health snapshot.

        Returns:
            A :class:`~imessagerelay.models.Health` snapshot. This endpoint
            requires no authentication.
        """
        data = await self._request("GET", "/health", auth=False)
        return Health.from_dict(data)

    async def send_message(
        self,
        text: str,
        to: str | Sequence[str],
        *,
        attach: Attachment | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        """Enqueue a message for delivery.

        Args:
            text: The message body (non-empty after trimming, max 2000 chars).
            to: A single alias, a comma-joined alias string, or a sequence of
                aliases (each stripped and joined with ``","``).
            attach: An optional :class:`~imessagerelay.models.Attachment`.
            idempotency_key: Idempotency key; a random hex key is generated
                when omitted. Re-sending an identical payload with the same key
                returns the existing job rather than enqueuing a duplicate.

        Returns:
            The enqueued :class:`~imessagerelay.models.Job`.
        """
        if isinstance(to, str):
            recipients = to
        else:
            recipients = ",".join(item.strip() for item in to)

        key = idempotency_key if idempotency_key else uuid.uuid4().hex

        body: dict[str, Any] = {"to": recipients, "text": text}
        if attach is not None:
            body["attach"] = attach.to_payload()

        headers = {
            "Idempotency-Key": key,
            "Content-Type": "application/json",
        }
        data = await self._request("POST", "/send", json=body, headers=headers)
        return Job.from_dict(data)

    async def get_job(self, job_id: str | uuid.UUID) -> Job:
        """Fetch the current state of a job.

        Args:
            job_id: The job identifier.

        Returns:
            The :class:`~imessagerelay.models.Job` with its current state.
        """
        data = await self._request("GET", f"/jobs/{quote(str(job_id), safe='')}")
        return Job.from_dict(data)

    async def wait_for_job(
        self,
        job_id: str | uuid.UUID,
        *,
        poll_interval: float = 1.0,
        timeout: float = 60.0,
    ) -> Job:
        """Poll a job until it reaches a terminal state or the timeout elapses.

        Uses a monotonic deadline from the running event loop clock so it is
        deterministic under test harnesses.

        Args:
            job_id: The job identifier.
            poll_interval: Seconds to wait between polls.
            timeout: Maximum seconds to wait before giving up.

        Returns:
            The :class:`~imessagerelay.models.Job` in its terminal state.

        Raises:
            IMessageRelayTimeoutError: If no terminal state is reached in time.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            job = await self.get_job(job_id)
            if job.state.is_terminal:
                return job
            if loop.time() >= deadline:
                raise IMessageRelayTimeoutError(
                    f"Job {job_id} did not reach a terminal state within {timeout}s"
                )
            remaining = deadline - loop.time()
            await asyncio.sleep(min(poll_interval, max(remaining, 0.0)))

    async def list_aliases(self) -> dict[str, str]:
        """List the aliases visible to the current token.

        Returns:
            A mapping of alias name to contact handle (may be empty).
        """
        data = await self._request("GET", "/aliases")
        return {str(name): str(contact) for name, contact in data.items()}

    async def add_alias(self, name: str, contact: str) -> Alias:
        """Create or overwrite an alias.

        Args:
            name: The alias name (lowercased server-side).
            contact: The contact handle (phone number or email).

        Returns:
            The stored :class:`~imessagerelay.models.Alias`.
        """
        body = {"name": name, "contact": contact}
        data = await self._request("POST", "/alias", json=body)
        return Alias(name=str(data.get("name", name)), contact=str(data.get("contact", contact)))

    async def remove_alias(self, name: str) -> None:
        """Delete an alias.

        Args:
            name: The alias name to delete.
        """
        await self._request("DELETE", f"/alias/{quote(name, safe='')}")

    async def send_file(
        self,
        path: str | os.PathLike[str],
        to: str | Sequence[str],
        text: str = "",
        **kwargs: object,
    ) -> Job:
        """Send a file attachment, reading it from disk.

        This is a convenience wrapper around
        :meth:`~imessagerelay.models.Attachment.from_path` and
        :meth:`send_message`. Note that reading the file is synchronous and
        blocks briefly before the async request is made.

        Args:
            path: Path to the file to attach.
            to: Recipient alias(es), as accepted by :meth:`send_message`.
            text: Optional message body to accompany the attachment.
            **kwargs: Extra keyword arguments forwarded to :meth:`send_message`
                (for example ``idempotency_key``).

        Returns:
            The enqueued :class:`~imessagerelay.models.Job`.
        """
        attachment = Attachment.from_path(path)
        return await self.send_message(text, to, attach=attachment, **kwargs)  # type: ignore[arg-type]
