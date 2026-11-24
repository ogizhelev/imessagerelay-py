"""Tests for imessagerelay.models."""

from __future__ import annotations

import base64
import datetime
from dataclasses import FrozenInstanceError

import pytest

from imessagerelay.models import Alias, Attachment, Health, Job, JobState


class TestJobState:
    def test_values(self) -> None:
        assert JobState.QUEUED.value == "queued"
        assert JobState.DISPATCHING.value == "dispatching"
        assert JobState.SUBMITTED.value == "submitted"
        assert JobState.UNCERTAIN.value == "uncertain"
        assert JobState.FAILED.value == "failed"

    def test_is_str_enum(self) -> None:
        assert isinstance(JobState.QUEUED, str)
        assert JobState.QUEUED == "queued"  # type: ignore[comparison-overlap]

    @pytest.mark.parametrize(
        "state, terminal",
        [
            (JobState.QUEUED, False),
            (JobState.DISPATCHING, False),
            (JobState.SUBMITTED, True),
            (JobState.UNCERTAIN, True),
            (JobState.FAILED, True),
        ],
    )
    def test_is_terminal(self, state: JobState, terminal: bool) -> None:
        assert state.is_terminal is terminal

    @pytest.mark.parametrize(
        "state, success",
        [
            (JobState.SUBMITTED, True),
            (JobState.FAILED, False),
            (JobState.UNCERTAIN, False),
            (JobState.QUEUED, False),
            (JobState.DISPATCHING, False),
        ],
    )
    def test_is_success(self, state: JobState, success: bool) -> None:
        assert state.is_success is success

    def test_from_str_valid(self) -> None:
        assert JobState.from_str("submitted") is JobState.SUBMITTED

    def test_from_str_unknown_raises_valueerror(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            JobState.from_str("bogus")
        message = str(exc_info.value)
        assert "bogus" in message
        # The error enumerates the valid states for clarity.
        assert "submitted" in message


class TestJob:
    def test_from_dict(self) -> None:
        job = Job.from_dict({"id": "abc-123", "state": "queued"})
        assert job.id == "abc-123"
        assert job.state is JobState.QUEUED

    def test_from_dict_coerces_id_to_str(self) -> None:
        job = Job.from_dict({"id": 42, "state": "submitted"})
        assert job.id == "42"
        assert job.state is JobState.SUBMITTED

    def test_from_dict_unknown_state_raises(self) -> None:
        with pytest.raises(ValueError):
            Job.from_dict({"id": "x", "state": "nope"})

    def test_frozen(self) -> None:
        job = Job.from_dict({"id": "x", "state": "queued"})
        with pytest.raises(FrozenInstanceError):
            job.id = "other"  # type: ignore[misc]


class TestHealth:
    def test_from_dict_full(self) -> None:
        data = {
            "status": "ok",
            "queueDepth": 3,
            "runnerState": "idle",
            "lastCallbackAt": "2026-11-24T10:00:00Z",
            "tlsExpiresAt": "2027-01-01T00:00:00+00:00",
            "uptime": "1h 23m",
        }
        health = Health.from_dict(data)
        assert health.status == "ok"
        assert health.queue_depth == 3
        assert health.runner_state == "idle"
        assert health.uptime == "1h 23m"
        assert health.last_callback_at == datetime.datetime(
            2026, 11, 24, 10, 0, 0, tzinfo=datetime.timezone.utc
        )
        assert health.tls_expires_at == datetime.datetime(
            2027, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc
        )
        assert health.raw is data

    def test_from_dict_null_timestamps(self) -> None:
        data = {
            "status": "ok",
            "queueDepth": 0,
            "runnerState": "dispatching",
            "lastCallbackAt": None,
            "tlsExpiresAt": None,
            "uptime": "5m",
        }
        health = Health.from_dict(data)
        assert health.last_callback_at is None
        assert health.tls_expires_at is None

    def test_from_dict_missing_fields_tolerated(self) -> None:
        health = Health.from_dict({})
        assert health.status == ""
        assert health.queue_depth == 0
        assert health.runner_state == ""
        assert health.uptime == ""
        assert health.last_callback_at is None
        assert health.tls_expires_at is None
        assert health.raw == {}

    def test_parsed_datetime_is_aware(self) -> None:
        health = Health.from_dict({"lastCallbackAt": "2026-11-24T10:00:00"})
        assert health.last_callback_at is not None
        assert health.last_callback_at.tzinfo is not None

    def test_raw_preserves_forward_compat_fields(self) -> None:
        data = {"status": "ok", "futureField": "surprise"}
        health = Health.from_dict(data)
        assert health.raw["futureField"] == "surprise"


class TestAlias:
    def test_fields(self) -> None:
        alias = Alias(name="mom", contact="+359123456")
        assert alias.name == "mom"
        assert alias.contact == "+359123456"


class TestAttachment:
    def test_from_bytes(self) -> None:
        raw = b"hello world"
        att = Attachment.from_bytes(raw, "greeting.txt", "text/plain")
        assert att.name == "greeting.txt"
        assert att.mime == "text/plain"
        assert base64.b64decode(att.data) == raw

    def test_from_bytes_default_mime_none(self) -> None:
        att = Attachment.from_bytes(b"x", "file.bin")
        assert att.mime is None

    def test_to_payload_with_mime(self) -> None:
        att = Attachment.from_bytes(b"data", "a.png", "image/png")
        payload = att.to_payload()
        assert payload == {"data": att.data, "name": "a.png", "mime": "image/png"}

    def test_to_payload_without_mime_omits_key(self) -> None:
        att = Attachment.from_bytes(b"data", "a.bin")
        payload = att.to_payload()
        assert payload == {"data": att.data, "name": "a.bin"}
        assert "mime" not in payload

    def test_from_path(self, tmp_path) -> None:
        raw = b"\x89PNG\r\n\x1a\n filecontent"
        f = tmp_path / "picture.png"
        f.write_bytes(raw)
        att = Attachment.from_path(str(f))
        assert att.name == "picture.png"
        assert att.mime == "image/png"  # guessed from extension
        assert base64.b64decode(att.data) == raw

    def test_from_path_name_override(self, tmp_path) -> None:
        f = tmp_path / "picture.png"
        f.write_bytes(b"data")
        att = Attachment.from_path(str(f), name="renamed.png")
        assert att.name == "renamed.png"

    def test_from_path_mime_override(self, tmp_path) -> None:
        f = tmp_path / "picture.png"
        f.write_bytes(b"data")
        att = Attachment.from_path(str(f), mime="application/octet-stream")
        assert att.mime == "application/octet-stream"

    def test_from_path_unknown_extension_mime_none(self, tmp_path) -> None:
        f = tmp_path / "mystery.zzz"
        f.write_bytes(b"data")
        att = Attachment.from_path(str(f))
        assert att.mime is None
