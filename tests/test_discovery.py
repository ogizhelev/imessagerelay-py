"""Tests for imessagerelay.discovery."""

from __future__ import annotations

import asyncio
import socket
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from imessagerelay.exceptions import IMessageRelayConnectionError


class TestRequireZeroconf:
    def test_import_error_message(self) -> None:
        # Temporarily hide zeroconf so _require_zeroconf raises ImportError.
        saved = {k: sys.modules[k] for k in list(sys.modules) if k.startswith("zeroconf")}
        for key in saved:
            del sys.modules[key]
        sys.modules["zeroconf"] = None  # type: ignore[assignment]
        try:
            # Re-import to pick up the hidden module state.
            import importlib

            import imessagerelay.discovery as disc_mod

            importlib.reload(disc_mod)
            with pytest.raises(ImportError, match="discovery"):
                disc_mod._require_zeroconf()
        finally:
            for key in list(sys.modules):
                if key.startswith("zeroconf"):
                    del sys.modules[key]
            sys.modules.update(saved)


@pytest.fixture()
def _zeroconf_available() -> None:
    """Skip the whole class when zeroconf is not installed."""
    pytest.importorskip(
        "zeroconf",
        reason="zeroconf not installed; install imessagerelay[discovery]",
    )


class TestAsyncDiscover:
    """Happy-path and timeout tests for async_discover, with zeroconf mocked."""

    pytestmark = pytest.mark.usefixtures("_zeroconf_available")

    def _make_mock_info(self, host: str = "10.0.0.5", port: int = 8443) -> MagicMock:
        info = MagicMock()
        info.addresses = [socket.inet_aton(host)]
        info.port = port
        info.parsed_addresses = MagicMock(return_value=[host])
        return info

    async def test_discovers_service(self) -> None:
        from imessagerelay.discovery import async_discover

        mock_info = self._make_mock_info()
        mock_azc = AsyncMock()
        mock_azc.zeroconf = MagicMock()
        mock_azc.async_get_service_info = AsyncMock(return_value=mock_info)

        listener_ref: list[object] = []

        class _FakeBrowser:
            def __init__(self, zc: object, service_type: str, listener: object) -> None:
                listener_ref.append(listener)
                # Fire add_service on the next event loop iteration.
                asyncio.get_running_loop().call_soon(
                    listener.add_service,  # type: ignore[attr-defined]
                    zc,
                    service_type,
                    "iMessageRelay._imessagerelay._tcp.local.",
                )

            async def async_cancel(self) -> None:
                pass

        with (
            patch("zeroconf.asyncio.AsyncZeroconf", MagicMock(return_value=mock_azc)),
            patch("zeroconf.asyncio.AsyncServiceBrowser", _FakeBrowser),
        ):
            host, port = await async_discover(timeout=2.0)

        assert host == "10.0.0.5"
        assert port == 8443

    async def test_timeout_raises(self) -> None:
        from imessagerelay.discovery import async_discover

        mock_azc = AsyncMock()
        mock_azc.zeroconf = MagicMock()

        class _SilentBrowser:
            def __init__(self, *_a: object, **_kw: object) -> None:
                pass

            async def async_cancel(self) -> None:
                pass

        with (
            patch("zeroconf.asyncio.AsyncZeroconf", MagicMock(return_value=mock_azc)),
            patch("zeroconf.asyncio.AsyncServiceBrowser", _SilentBrowser),
        ):
            with pytest.raises(IMessageRelayConnectionError, match="No iMessageRelay"):
                await async_discover(timeout=0.05)

    async def test_no_addresses_skipped(self) -> None:
        """A service with no addresses must not resolve the future."""
        from imessagerelay.discovery import async_discover

        empty_info = MagicMock()
        empty_info.addresses = []
        empty_info.parsed_addresses = MagicMock(return_value=[])

        mock_azc = AsyncMock()
        mock_azc.zeroconf = MagicMock()
        mock_azc.async_get_service_info = AsyncMock(return_value=empty_info)

        class _EmptyBrowser:
            def __init__(self, zc: object, service_type: str, listener: object) -> None:
                asyncio.get_running_loop().call_soon(
                    listener.add_service,  # type: ignore[attr-defined]
                    zc,
                    service_type,
                    "iMessageRelay._imessagerelay._tcp.local.",
                )

            async def async_cancel(self) -> None:
                pass

        with (
            patch("zeroconf.asyncio.AsyncZeroconf", MagicMock(return_value=mock_azc)),
            patch("zeroconf.asyncio.AsyncServiceBrowser", _EmptyBrowser),
        ):
            with pytest.raises(IMessageRelayConnectionError):
                await async_discover(timeout=0.05)


class TestDiscoverSync:
    """Tests for the synchronous discover() wrapper."""

    pytestmark = pytest.mark.usefixtures("_zeroconf_available")

    def test_discover_calls_asyncio_run(self) -> None:
        from imessagerelay.discovery import discover

        with patch("imessagerelay.discovery.async_discover", new_callable=AsyncMock) as mock_coro:
            mock_coro.return_value = ("10.0.0.5", 8443)
            result = discover(timeout=3.0)

        mock_coro.assert_awaited_once_with(timeout=3.0)
        assert result == ("10.0.0.5", 8443)
