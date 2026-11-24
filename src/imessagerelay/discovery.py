"""mDNS/Bonjour service discovery for iMessageRelay.

The iMessageRelay server advertises itself as ``_imessagerelay._tcp.local.``
via Bonjour/mDNS. The helpers here let you discover a running server on the
local network without hard-coding its IP address.

Requires the optional ``discovery`` extra::

    pip install "imessagerelay[discovery]"
"""

from __future__ import annotations

import asyncio
import socket

from .exceptions import IMessageRelayConnectionError

__all__ = ["async_discover", "discover"]

_SERVICE_TYPE = "_imessagerelay._tcp.local."


def _require_zeroconf() -> None:
    """Raise :class:`ImportError` with an actionable message when ``zeroconf`` is absent."""
    try:
        import zeroconf  # noqa: F401  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "mDNS discovery requires the 'discovery' extra: "
            'pip install "imessagerelay[discovery]"'
        ) from exc


async def async_discover(*, timeout: float = 5.0) -> tuple[str, int]:
    """Discover the first iMessageRelay server on the local network.

    Browses for ``_imessagerelay._tcp.local.`` Bonjour/mDNS advertisements
    and returns the host and port of the first responder.

    Args:
        timeout: Maximum seconds to wait for a reply. Defaults to ``5.0``.

    Returns:
        A ``(host, port)`` tuple for the discovered server.

    Raises:
        ImportError: If the ``zeroconf`` package is not installed (install
            ``imessagerelay[discovery]``).
        IMessageRelayConnectionError: If no server is found within ``timeout``.
    """
    _require_zeroconf()
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf  # type: ignore[import-untyped]

    loop = asyncio.get_running_loop()
    found: asyncio.Future[tuple[str, int]] = loop.create_future()
    azc = AsyncZeroconf()

    async def _lookup(type_: str, name: str) -> None:
        """Fetch full service info and resolve to a (host, port) result."""
        info = await azc.async_get_service_info(type_, name, timeout=int(timeout * 1000))
        if info is None or found.done():
            return
        # Use parsed_addresses() when available (zeroconf >= 0.38); fall back to manual decode.
        if hasattr(info, "parsed_addresses"):
            addrs: list[str] = info.parsed_addresses()
            host: str | None = addrs[0] if addrs else None
        elif info.addresses:
            raw = info.addresses[0]
            if len(raw) == 4:
                host = socket.inet_ntoa(raw)
            else:
                host = socket.inet_ntop(socket.AF_INET6, raw)
        else:
            host = None
        if host and not found.done():
            found.set_result((host, int(info.port)))

    class _Listener:
        """Internal zeroconf service browser listener."""

        def add_service(self, _zc: object, type_: str, name: str) -> None:
            """Schedule a service info lookup when a new service advertisement is seen."""
            if not found.done():
                asyncio.ensure_future(_lookup(type_, name))

        def remove_service(self, *_: object) -> None:
            """No-op; service removal is not relevant for discovery."""

        def update_service(self, *_: object) -> None:
            """No-op; service updates do not trigger re-discovery."""

    browser = AsyncServiceBrowser(azc.zeroconf, _SERVICE_TYPE, _Listener())
    try:
        result = await asyncio.wait_for(asyncio.shield(found), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        raise IMessageRelayConnectionError(
            f"No iMessageRelay service found on the local network within {timeout}s"
        ) from None
    finally:
        await browser.async_cancel()
        await azc.async_close()


def discover(*, timeout: float = 5.0) -> tuple[str, int]:
    """Discover the first iMessageRelay server on the local network (synchronous).

    Blocking synchronous wrapper around :func:`async_discover`. Creates a
    temporary ``asyncio`` event loop to run discovery.

    Args:
        timeout: Maximum seconds to wait for a reply. Defaults to ``5.0``.

    Returns:
        A ``(host, port)`` tuple for the discovered server.

    Raises:
        ImportError: If the ``zeroconf`` package is not installed.
        IMessageRelayConnectionError: If no server is found within ``timeout``.
    """
    return asyncio.run(async_discover(timeout=timeout))
