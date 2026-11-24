"""Tests for imessagerelay.client.IMessageRelayClient (httpx mocked via respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from imessagerelay import IMessageRelayClient
from imessagerelay.exceptions import (
    APIError,
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    IMessageRelayConnectionError,
    IMessageRelayTimeoutError,
    NotFoundError,
    RateLimitError,
    ServerError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from imessagerelay.models import Attachment, Health, Job, JobState

BASE_URL = "https://10.0.0.5:8443"


# ---------------------------------------------------------------------------
# Construction / URL handling
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_base_url(self) -> None:
        c = IMessageRelayClient("10.0.0.5", "tok", port=8443)
        assert c.base_url == "https://10.0.0.5:8443"

    def test_default_port(self) -> None:
        c = IMessageRelayClient("host", "tok")
        assert c.base_url == "https://host:8443"

    def test_custom_scheme_and_port(self) -> None:
        c = IMessageRelayClient("host", "tok", port=9000, scheme="http")
        assert c.base_url == "http://host:9000"

    def test_host_with_scheme_stripped(self) -> None:
        c = IMessageRelayClient("https://10.0.0.5:8443", "tok")
        assert c.base_url == "https://10.0.0.5:8443"

    def test_host_with_scheme_and_trailing_slash(self) -> None:
        c = IMessageRelayClient("https://10.0.0.5:8443/", "tok")
        assert c.base_url == "https://10.0.0.5:8443"

    def test_embedded_port_used_when_port_kwarg_default(self) -> None:
        c = IMessageRelayClient("10.0.0.5:9999", "tok")
        assert c.base_url == "https://10.0.0.5:9999"

    def test_explicit_port_kwarg_overrides_embedded(self) -> None:
        c = IMessageRelayClient("10.0.0.5:9999", "tok", port=7000)
        assert c.base_url == "https://10.0.0.5:7000"

    def test_from_url(self) -> None:
        c = IMessageRelayClient.from_url("https://example.com:8443", "tok")
        assert c.base_url == "https://example.com:8443"

    def test_from_url_default_port(self) -> None:
        c = IMessageRelayClient.from_url("https://example.com", "tok")
        assert c.base_url == "https://example.com:8443"

    def test_from_url_http_scheme(self) -> None:
        c = IMessageRelayClient.from_url("http://example.com:8080", "tok")
        assert c.base_url == "http://example.com:8080"


# ---------------------------------------------------------------------------
# Client ownership / lifecycle
# ---------------------------------------------------------------------------


class TestClientOwnership:
    async def test_context_manager_closes_owned_client(self) -> None:
        async with IMessageRelayClient("host", "tok") as c:
            underlying = c._get_client()
            assert not underlying.is_closed
        assert underlying.is_closed

    async def test_aclose_closes_owned_client(self) -> None:
        c = IMessageRelayClient("host", "tok")
        underlying = c._get_client()
        await c.aclose()
        assert underlying.is_closed

    async def test_injected_client_not_closed(self) -> None:
        injected = httpx.AsyncClient(verify=False)
        try:
            c = IMessageRelayClient("host", "tok", client=injected)
            async with c:
                pass
            await c.aclose()
            assert not injected.is_closed
        finally:
            await injected.aclose()

    async def test_injected_client_is_used(self) -> None:
        injected = httpx.AsyncClient(verify=False)
        try:
            c = IMessageRelayClient("host", "tok", client=injected)
            assert c._get_client() is injected
        finally:
            await injected.aclose()

    async def test_lazy_client_creation(self) -> None:
        c = IMessageRelayClient("host", "tok")
        assert c._client is None
        created = c._get_client()
        assert created is not None
        assert c._client is created
        await c.aclose()


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class TestHealth:
    @respx.mock
    async def test_health_parsing(self, client: IMessageRelayClient) -> None:
        route = respx.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "queueDepth": 2,
                    "runnerState": "idle",
                    "lastCallbackAt": "2026-11-24T10:00:00Z",
                    "tlsExpiresAt": None,
                    "uptime": "1h 23m",
                },
            )
        )
        health = await client.health()
        assert isinstance(health, Health)
        assert health.status == "ok"
        assert health.queue_depth == 2
        assert health.runner_state == "idle"
        assert health.uptime == "1h 23m"
        assert health.last_callback_at is not None
        assert health.tls_expires_at is None
        assert route.called

    @respx.mock
    async def test_health_sends_no_auth_header(self, client: IMessageRelayClient) -> None:
        route = respx.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        await client.health()
        request = route.calls.last.request
        assert "Authorization" not in request.headers


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    @respx.mock
    async def test_happy_path_returns_queued_job(self, client: IMessageRelayClient) -> None:
        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(
                202,
                json={"id": "job-1", "state": "queued"},
                headers={"Location": "/jobs/job-1"},
            )
        )
        job = await client.send_message("hello", to="mom")
        assert isinstance(job, Job)
        assert job.id == "job-1"
        assert job.state is JobState.QUEUED
        assert route.called

    @respx.mock
    async def test_sends_auth_and_content_type_and_idempotency(
        self, client: IMessageRelayClient
    ) -> None:
        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "x", "state": "queued"})
        )
        await client.send_message("hello", to="mom")
        req = route.calls.last.request
        assert req.headers["Authorization"] == "Bearer test-token"
        assert req.headers["Content-Type"] == "application/json"
        assert req.headers["Idempotency-Key"]  # non-empty auto-generated

    @respx.mock
    async def test_body_single_recipient(self, client: IMessageRelayClient) -> None:
        import json as _json

        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "x", "state": "queued"})
        )
        await client.send_message("hi there", to="mom")
        body = _json.loads(route.calls.last.request.content)
        assert body == {"to": "mom", "text": "hi there"}

    @respx.mock
    async def test_to_list_joined_and_stripped(self, client: IMessageRelayClient) -> None:
        import json as _json

        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "x", "state": "queued"})
        )
        await client.send_message("hi", to=[" mom ", "dad", " sis "])
        body = _json.loads(route.calls.last.request.content)
        assert body["to"] == "mom,dad,sis"

    @respx.mock
    async def test_idempotency_key_auto_generated_unique(self, client: IMessageRelayClient) -> None:
        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "x", "state": "queued"})
        )
        await client.send_message("a", to="mom")
        await client.send_message("b", to="mom")
        key1 = route.calls[0].request.headers["Idempotency-Key"]
        key2 = route.calls[1].request.headers["Idempotency-Key"]
        assert key1 != key2

    @respx.mock
    async def test_idempotency_key_passthrough(self, client: IMessageRelayClient) -> None:
        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "x", "state": "queued"})
        )
        await client.send_message("a", to="mom", idempotency_key="my-key")
        assert route.calls.last.request.headers["Idempotency-Key"] == "my-key"

    @respx.mock
    async def test_empty_idempotency_key_is_replaced(self, client: IMessageRelayClient) -> None:
        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "x", "state": "queued"})
        )
        await client.send_message("a", to="mom", idempotency_key="")
        sent = route.calls.last.request.headers["Idempotency-Key"]
        assert sent != ""
        assert len(sent) >= 16

    @respx.mock
    async def test_attachment_in_body(self, client: IMessageRelayClient) -> None:
        import json as _json

        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "x", "state": "queued"})
        )
        att = Attachment.from_bytes(b"data", "a.png", "image/png")
        await client.send_message("caption", to="mom", attach=att)
        body = _json.loads(route.calls.last.request.content)
        assert body["attach"] == {"data": att.data, "name": "a.png", "mime": "image/png"}

    @respx.mock
    async def test_duplicate_idempotency_returns_existing_state(
        self, client: IMessageRelayClient
    ) -> None:
        respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "dup", "state": "submitted"})
        )
        job = await client.send_message("hi", to="mom", idempotency_key="k")
        assert job.id == "dup"
        assert job.state is JobState.SUBMITTED


# ---------------------------------------------------------------------------
# Error status -> exception mapping, message from {"error": ...}
# ---------------------------------------------------------------------------


class TestErrorMapping:
    @pytest.mark.parametrize(
        "status, exc, error_message",
        [
            (400, ValidationError, "Required: {...} max 2000 chars"),
            (401, AuthenticationError, "Unknown or revoked token"),
            (403, ForbiddenError, "Alias 'x' not configured"),
            (404, NotFoundError, "Not found"),
            (409, ConflictError, "Same Idempotency-Key with different payload"),
            (415, UnsupportedMediaTypeError, "Content-Type must be application/json"),
            (429, RateLimitError, "Rate limit exceeded"),
            (503, ServerError, "Storage error"),
        ],
    )
    @respx.mock
    async def test_send_error_status_maps_to_exception(
        self,
        client: IMessageRelayClient,
        status: int,
        exc: type,
        error_message: str,
    ) -> None:
        respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(status, json={"error": error_message})
        )
        with pytest.raises(exc) as exc_info:
            await client.send_message("hi", to="mom")
        raised = exc_info.value
        assert isinstance(raised, APIError)
        assert raised.status_code == status
        assert raised.message == error_message
        assert raised.payload == {"error": error_message}
        assert str(raised) == f"[{status}] {error_message}"

    @respx.mock
    async def test_message_falls_back_to_reason_phrase_without_error_body(
        self, client: IMessageRelayClient
    ) -> None:
        respx.post(f"{BASE_URL}/send").mock(return_value=httpx.Response(400, json={}))
        with pytest.raises(ValidationError) as exc_info:
            await client.send_message("hi", to="mom")
        # No {"error": ...} present -> message is non-empty fallback.
        assert exc_info.value.message


# ---------------------------------------------------------------------------
# Transport failures
# ---------------------------------------------------------------------------


class TestTransportErrors:
    @respx.mock
    async def test_timeout_maps_to_timeout_error(self, client: IMessageRelayClient) -> None:
        respx.get(f"{BASE_URL}/health").mock(side_effect=httpx.TimeoutException("timed out"))
        with pytest.raises(IMessageRelayTimeoutError):
            await client.health()

    @respx.mock
    async def test_connect_error_maps_to_connection_error(
        self, client: IMessageRelayClient
    ) -> None:
        respx.get(f"{BASE_URL}/health").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(IMessageRelayConnectionError):
            await client.health()


# ---------------------------------------------------------------------------
# get_job / wait_for_job
# ---------------------------------------------------------------------------


class TestGetJob:
    @respx.mock
    async def test_get_job(self, client: IMessageRelayClient) -> None:
        respx.get(f"{BASE_URL}/jobs/abc").mock(
            return_value=httpx.Response(200, json={"id": "abc", "state": "dispatching"})
        )
        job = await client.get_job("abc")
        assert job.id == "abc"
        assert job.state is JobState.DISPATCHING

    @respx.mock
    async def test_get_job_not_found(self, client: IMessageRelayClient) -> None:
        respx.get(f"{BASE_URL}/jobs/missing").mock(
            return_value=httpx.Response(404, json={"error": "Not found"})
        )
        with pytest.raises(NotFoundError) as exc_info:
            await client.get_job("missing")
        assert exc_info.value.message == "Not found"


class TestWaitForJob:
    @respx.mock
    async def test_polls_to_terminal_state(self, client: IMessageRelayClient) -> None:
        respx.get(f"{BASE_URL}/jobs/job-1").mock(
            side_effect=[
                httpx.Response(200, json={"id": "job-1", "state": "queued"}),
                httpx.Response(200, json={"id": "job-1", "state": "dispatching"}),
                httpx.Response(200, json={"id": "job-1", "state": "submitted"}),
            ]
        )
        job = await client.wait_for_job("job-1", poll_interval=0.0, timeout=5.0)
        assert job.state is JobState.SUBMITTED

    @respx.mock
    async def test_returns_immediately_if_already_terminal(
        self, client: IMessageRelayClient
    ) -> None:
        route = respx.get(f"{BASE_URL}/jobs/job-1").mock(
            return_value=httpx.Response(200, json={"id": "job-1", "state": "failed"})
        )
        job = await client.wait_for_job("job-1", poll_interval=0.0, timeout=5.0)
        assert job.state is JobState.FAILED
        assert route.call_count == 1

    @respx.mock
    async def test_timeout_raises(self, client: IMessageRelayClient) -> None:
        respx.get(f"{BASE_URL}/jobs/job-1").mock(
            return_value=httpx.Response(200, json={"id": "job-1", "state": "queued"})
        )
        with pytest.raises(IMessageRelayTimeoutError):
            await client.wait_for_job("job-1", poll_interval=0.0, timeout=0.0)


# ---------------------------------------------------------------------------
# Alias management
# ---------------------------------------------------------------------------


class TestAliases:
    @respx.mock
    async def test_list_aliases(self, client: IMessageRelayClient) -> None:
        respx.get(f"{BASE_URL}/aliases").mock(
            return_value=httpx.Response(200, json={"mom": "+359123", "work": "a@example.com"})
        )
        aliases = await client.list_aliases()
        assert aliases == {"mom": "+359123", "work": "a@example.com"}

    @respx.mock
    async def test_list_aliases_empty(self, client: IMessageRelayClient) -> None:
        respx.get(f"{BASE_URL}/aliases").mock(return_value=httpx.Response(200, json={}))
        assert await client.list_aliases() == {}

    @respx.mock
    async def test_list_aliases_unauthorized(self, client: IMessageRelayClient) -> None:
        respx.get(f"{BASE_URL}/aliases").mock(
            return_value=httpx.Response(401, json={"error": "Invalid or missing token"})
        )
        with pytest.raises(AuthenticationError) as exc_info:
            await client.list_aliases()
        assert exc_info.value.message == "Invalid or missing token"

    @respx.mock
    async def test_add_alias(self, client: IMessageRelayClient) -> None:
        import json as _json

        route = respx.post(f"{BASE_URL}/alias").mock(
            return_value=httpx.Response(200, json={"name": "mom", "contact": "+359123"})
        )
        alias = await client.add_alias("mom", "+359123")
        assert alias.name == "mom"
        assert alias.contact == "+359123"
        body = _json.loads(route.calls.last.request.content)
        assert body == {"name": "mom", "contact": "+359123"}

    @respx.mock
    async def test_add_alias_validation_error(self, client: IMessageRelayClient) -> None:
        respx.post(f"{BASE_URL}/alias").mock(
            return_value=httpx.Response(400, json={"error": "Required: name and contact fields"})
        )
        with pytest.raises(ValidationError) as exc_info:
            await client.add_alias("", "")
        assert exc_info.value.message == "Required: name and contact fields"

    @respx.mock
    async def test_remove_alias(self, client: IMessageRelayClient) -> None:
        route = respx.delete(f"{BASE_URL}/alias/mom").mock(
            return_value=httpx.Response(200, json={"deleted": "mom"})
        )
        await client.remove_alias("mom")
        assert route.called

    @respx.mock
    async def test_remove_alias_sends_auth(self, client: IMessageRelayClient) -> None:
        route = respx.delete(f"{BASE_URL}/alias/mom").mock(
            return_value=httpx.Response(200, json={"deleted": "mom"})
        )
        await client.remove_alias("mom")
        assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"


# ---------------------------------------------------------------------------
# send_file convenience wrapper
# ---------------------------------------------------------------------------


class TestSendFile:
    @respx.mock
    async def test_send_file_reads_and_attaches(
        self, client: IMessageRelayClient, tmp_path
    ) -> None:
        import json as _json

        f = tmp_path / "photo.png"
        f.write_bytes(b"\x89PNG pretend bytes")
        route = respx.post(f"{BASE_URL}/send").mock(
            return_value=httpx.Response(202, json={"id": "f1", "state": "queued"})
        )
        job = await client.send_file(str(f), to="mom", text="look")
        assert job.id == "f1"
        body = _json.loads(route.calls.last.request.content)
        assert body["text"] == "look"
        assert body["to"] == "mom"
        assert body["attach"]["name"] == "photo.png"
        assert body["attach"]["mime"] == "image/png"
