"""Shared pytest fixtures for the imessagerelay test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from imessagerelay import IMessageRelayClient

HOST = "10.0.0.5"
PORT = 8443
TOKEN = "test-token"
BASE_URL = f"https://{HOST}:{PORT}"


@pytest.fixture
def host() -> str:
    """The test server host."""
    return HOST


@pytest.fixture
def port() -> int:
    """The test server port."""
    return PORT


@pytest.fixture
def token() -> str:
    """The test bearer token."""
    return TOKEN


@pytest.fixture
def base_url() -> str:
    """The exact base URL the client builds, used to register respx routes."""
    return BASE_URL


@pytest_asyncio.fixture
async def client() -> AsyncIterator[IMessageRelayClient]:
    """An IMessageRelayClient that owns its httpx client, closed on teardown."""
    c = IMessageRelayClient(HOST, TOKEN, port=PORT)
    try:
        yield c
    finally:
        await c.aclose()
