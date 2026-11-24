"""Tests for imessagerelay.sync.SyncIMessageRelayClient."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from imessagerelay import SyncIMessageRelayClient
from imessagerelay.models import Alias, Health, Job, JobState


class TestConstruction:
    def test_base_url(self) -> None:
        with SyncIMessageRelayClient("10.0.0.5", "tok", port=8443) as c:
            assert c.base_url == "https://10.0.0.5:8443"

    def test_default_port(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            assert c.base_url == "https://host:8443"

    def test_custom_scheme_and_port(self) -> None:
        with SyncIMessageRelayClient("host", "tok", port=9000, scheme="http") as c:
            assert c.base_url == "http://host:9000"

    def test_from_url(self) -> None:
        with SyncIMessageRelayClient.from_url("https://10.0.0.5:8443", "tok") as c:
            assert c.base_url == "https://10.0.0.5:8443"

    def test_from_url_default_port(self) -> None:
        with SyncIMessageRelayClient.from_url("https://host", "tok") as c:
            assert c.base_url == "https://host:8443"

    def test_from_url_http(self) -> None:
        with SyncIMessageRelayClient.from_url("http://host:8080", "tok") as c:
            assert c.base_url == "http://host:8080"


class TestLifecycle:
    def test_context_manager_closes(self) -> None:
        c = SyncIMessageRelayClient("host", "tok")
        with c:
            assert not c._closed
        assert c._closed

    def test_explicit_close(self) -> None:
        c = SyncIMessageRelayClient("host", "tok")
        c.close()
        assert c._closed

    def test_double_close_is_safe(self) -> None:
        c = SyncIMessageRelayClient("host", "tok")
        c.close()
        c.close()

    def test_run_after_close_raises(self) -> None:
        c = SyncIMessageRelayClient("host", "tok")
        c.close()
        with pytest.raises(RuntimeError, match="closed"):
            c.health()


class TestMethods:
    def test_health(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.health = AsyncMock(  # type: ignore[method-assign]
                return_value=Health.from_dict({"status": "ok"})
            )
            h = c.health()
        assert h.status == "ok"

    def test_send_message(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.send_message = AsyncMock(  # type: ignore[method-assign]
                return_value=Job.from_dict({"id": "x", "state": "queued"})
            )
            job = c.send_message("hello", to="mom")
        assert job.id == "x"
        assert job.state is JobState.QUEUED

    def test_send_message_list_recipients(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.send_message = AsyncMock(  # type: ignore[method-assign]
                return_value=Job.from_dict({"id": "y", "state": "queued"})
            )
            job = c.send_message("hi", to=["mom", "dad"])
        assert job.id == "y"

    def test_get_job(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.get_job = AsyncMock(  # type: ignore[method-assign]
                return_value=Job.from_dict({"id": "j1", "state": "submitted"})
            )
            job = c.get_job("j1")
        assert job.id == "j1"
        assert job.state is JobState.SUBMITTED

    def test_wait_for_job(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.wait_for_job = AsyncMock(  # type: ignore[method-assign]
                return_value=Job.from_dict({"id": "j1", "state": "submitted"})
            )
            job = c.wait_for_job("j1", poll_interval=0.0, timeout=5.0)
        assert job.state is JobState.SUBMITTED

    def test_list_aliases(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.list_aliases = AsyncMock(  # type: ignore[method-assign]
                return_value={"mom": "+1234", "dad": "+5678"}
            )
            aliases = c.list_aliases()
        assert aliases == {"mom": "+1234", "dad": "+5678"}

    def test_add_alias(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.add_alias = AsyncMock(  # type: ignore[method-assign]
                return_value=Alias(name="mom", contact="+1234")
            )
            alias = c.add_alias("mom", "+1234")
        assert alias.name == "mom"
        assert alias.contact == "+1234"

    def test_remove_alias(self) -> None:
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.remove_alias = AsyncMock(return_value=None)  # type: ignore[method-assign]
            c.remove_alias("mom")

    def test_send_file(self, tmp_path) -> None:
        f = tmp_path / "photo.png"
        f.write_bytes(b"\x89PNG pretend bytes")
        with SyncIMessageRelayClient("host", "tok") as c:
            c._async.send_file = AsyncMock(  # type: ignore[method-assign]
                return_value=Job.from_dict({"id": "f1", "state": "queued"})
            )
            job = c.send_file(str(f), to="mom", text="look")
        assert job.id == "f1"
