"""Synchronous client for the iMessageRelay HTTP API.

:class:`SyncIMessageRelayClient` mirrors :class:`~imessagerelay.IMessageRelayClient`
exactly but runs every call synchronously on a dedicated ``asyncio`` event loop,
making it straightforward to use from plain (non-async) scripts, notebooks, or
automation frameworks that cannot ``await``.

For async contexts use :class:`~imessagerelay.IMessageRelayClient` directly.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Sequence
from types import TracebackType
from typing import TypeVar
from urllib.parse import urlsplit

from .client import IMessageRelayClient
from .models import Alias, Attachment, Health, Job

__all__ = ["SyncIMessageRelayClient"]

_T = TypeVar("_T")

_DEFAULT_PORT = 8443
_DEFAULT_SCHEME = "https"


class SyncIMessageRelayClient:
    """Synchronous client for the iMessageRelay HTTP API.

    A thin wrapper around :class:`~imessagerelay.IMessageRelayClient` that runs
    every coroutine to completion on a private ``asyncio`` event loop. Use it in
    scripts, REPLs, or any framework that cannot ``await``.

    Use it as a context manager to guarantee cleanup::

        with SyncIMessageRelayClient("10.0.0.5", token) as client:
            job = client.send_message("hello", to="mom")

    Or manage the lifecycle manually::

        client = SyncIMessageRelayClient("10.0.0.5", token)
        try:
            job = client.send_message("hello", to="mom")
        finally:
            client.close()

    Args:
        host: Server host. A scheme and/or port embedded in the value
            (e.g. ``"https://10.0.0.5:8443"``) are stripped and honoured.
        token: Bearer token used to authenticate requests.
        port: Server port. Defaults to ``8443``.
        verify_ssl: Whether to verify the server's TLS certificate. Defaults
            to ``False`` for the common self-signed deployment.
        timeout: Per-request timeout in seconds.
        scheme: URL scheme, normally ``"https"``.
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
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._async = IMessageRelayClient(
            host,
            token,
            port=port,
            verify_ssl=verify_ssl,
            timeout=timeout,
            scheme=scheme,
        )
        self._closed = False

    @classmethod
    def from_url(cls, url: str, token: str, **kwargs: object) -> SyncIMessageRelayClient:
        """Construct a client from a full base URL.

        Args:
            url: A URL such as ``"https://host:port"``.
            token: Bearer token for authentication.
            **kwargs: Extra keyword arguments forwarded to :meth:`__init__`.

        Returns:
            A configured :class:`SyncIMessageRelayClient`.
        """
        parts = urlsplit(url)
        scheme = parts.scheme or _DEFAULT_SCHEME
        host = parts.hostname or ""
        port = parts.port or _DEFAULT_PORT
        extra: dict[str, object] = dict(kwargs)
        extra.setdefault("scheme", scheme)
        extra.setdefault("port", port)
        return cls(host, token, **extra)  # type: ignore[arg-type]

    @classmethod
    def discover(
        cls,
        token: str,
        *,
        discovery_timeout: float = 5.0,
        **kwargs: object,
    ) -> SyncIMessageRelayClient:
        """Auto-discover a server via mDNS and create a client.

        Browses for ``_imessagerelay._tcp.local.`` Bonjour/mDNS advertisements
        and connects to the first server found. Install the ``discovery`` extra
        to enable::

            pip install "imessagerelay[discovery]"

        Args:
            token: Bearer token for authentication.
            discovery_timeout: Seconds to wait for mDNS discovery.
            **kwargs: Extra keyword arguments forwarded to :meth:`__init__`.

        Returns:
            A configured :class:`SyncIMessageRelayClient`.

        Raises:
            ImportError: If ``zeroconf`` is not installed.
            IMessageRelayConnectionError: If no server is found in time.
        """
        from .discovery import discover as _discover

        host, port = _discover(timeout=discovery_timeout)
        extra: dict[str, object] = dict(kwargs)
        extra.setdefault("port", port)
        return cls(host, token, **extra)  # type: ignore[arg-type]

    @property
    def base_url(self) -> str:
        """The fully-qualified base URL of the server."""
        return self._async.base_url

    def _run(self, coro: Awaitable[_T]) -> _T:
        """Run a coroutine to completion on the private event loop.

        Args:
            coro: The coroutine to run.

        Returns:
            The coroutine's return value.

        Raises:
            RuntimeError: If this client has already been closed.
        """
        if self._closed:
            # Close the coroutine so Python doesn't emit "was never awaited".
            if asyncio.iscoroutine(coro):
                coro.close()  # type: ignore[union-attr]
            raise RuntimeError("SyncIMessageRelayClient is closed")
        return self._loop.run_until_complete(coro)

    def close(self) -> None:
        """Close the underlying async client and event loop.

        Idempotent — safe to call more than once.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.run_until_complete(self._async.aclose())
        finally:
            self._loop.close()

    def __enter__(self) -> SyncIMessageRelayClient:
        """Return *self* to support the context-manager protocol."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the client on context-manager exit."""
        self.close()

    def health(self) -> Health:
        """Fetch the server's health snapshot.

        Returns:
            A :class:`~imessagerelay.models.Health` snapshot. No auth required.
        """
        return self._run(self._async.health())

    def send_message(
        self,
        text: str,
        to: str | Sequence[str],
        *,
        attach: Attachment | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        """Enqueue a message for delivery.

        Args:
            text: The message body.
            to: Recipient alias(es).
            attach: An optional file attachment.
            idempotency_key: Idempotency key; auto-generated when omitted.

        Returns:
            The enqueued :class:`~imessagerelay.models.Job`.
        """
        return self._run(
            self._async.send_message(text, to, attach=attach, idempotency_key=idempotency_key)
        )

    def get_job(self, job_id: str) -> Job:
        """Fetch the current state of a job.

        Args:
            job_id: The job identifier.

        Returns:
            The :class:`~imessagerelay.models.Job` with its current state.
        """
        return self._run(self._async.get_job(job_id))

    def wait_for_job(
        self,
        job_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: float = 60.0,
    ) -> Job:
        """Poll a job until it reaches a terminal state or the timeout elapses.

        Args:
            job_id: The job identifier.
            poll_interval: Seconds between polls.
            timeout: Maximum seconds to wait.

        Returns:
            The :class:`~imessagerelay.models.Job` in its terminal state.

        Raises:
            IMessageRelayTimeoutError: If no terminal state is reached in time.
        """
        return self._run(
            self._async.wait_for_job(job_id, poll_interval=poll_interval, timeout=timeout)
        )

    def list_aliases(self) -> dict[str, str]:
        """List the aliases visible to the current token.

        Returns:
            A mapping of alias name to contact handle (may be empty).
        """
        return self._run(self._async.list_aliases())

    def add_alias(self, name: str, contact: str) -> Alias:
        """Create or overwrite an alias.

        Args:
            name: The alias name.
            contact: The contact handle (phone number or email).

        Returns:
            The stored :class:`~imessagerelay.models.Alias`.
        """
        return self._run(self._async.add_alias(name, contact))

    def remove_alias(self, name: str) -> None:
        """Delete an alias.

        Args:
            name: The alias name to delete.
        """
        self._run(self._async.remove_alias(name))

    def send_file(
        self,
        path: str | os.PathLike[str],
        to: str | Sequence[str],
        text: str = "",
        **kwargs: object,
    ) -> Job:
        """Send a file attachment, reading it from disk.

        Args:
            path: Path to the file to attach.
            to: Recipient alias(es).
            text: Optional message body to accompany the attachment.
            **kwargs: Forwarded to :meth:`send_message`.

        Returns:
            The enqueued :class:`~imessagerelay.models.Job`.
        """
        return self._run(self._async.send_file(path, to, text, **kwargs))  # type: ignore[arg-type]
