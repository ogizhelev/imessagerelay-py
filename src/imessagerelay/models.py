"""Typed data models for the imessagerelay client library.

These dataclasses and the :class:`JobState` enum mirror the JSON shapes
exchanged with the iMessageRelay HTTP server, converting loosely-typed JSON
into strongly-typed, immutable Python objects.
"""

from __future__ import annotations

import base64
import datetime
import mimetypes
import os
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "JobState",
    "Job",
    "Health",
    "Alias",
    "Attachment",
]


class JobState(str, Enum):
    """Lifecycle state of an enqueued send job.

    The state machine is::

        queued -> dispatching -> submitted | uncertain | failed

    ``submitted`` is success, ``failed`` is a permanent rejection, and
    ``uncertain`` means the dispatch callback was lost or the runner died
    mid-dispatch. The latter three are terminal.
    """

    QUEUED = "queued"
    DISPATCHING = "dispatching"
    SUBMITTED = "submitted"
    UNCERTAIN = "uncertain"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Whether the state is terminal (no further transitions expected)."""
        return self in {JobState.SUBMITTED, JobState.FAILED, JobState.UNCERTAIN}

    @property
    def is_success(self) -> bool:
        """Whether the job completed successfully (``submitted``)."""
        return self is JobState.SUBMITTED

    @classmethod
    def from_str(cls, value: str) -> JobState:
        """Parse a wire string into a :class:`JobState`.

        Args:
            value: The state string from the server.

        Returns:
            The matching :class:`JobState`.

        Raises:
            ValueError: If ``value`` is not a recognised job state.
        """
        try:
            return cls(value)
        except ValueError:
            valid = ", ".join(sorted(member.value for member in cls))
            raise ValueError(
                f"Unknown job state {value!r}; expected one of: {valid}"
            ) from None


def _parse_datetime(value: str | None) -> datetime.datetime | None:
    """Parse an ISO 8601 string into an aware datetime, tolerating ``None``.

    Args:
        value: An ISO 8601 timestamp, or ``None``.

    Returns:
        An aware :class:`datetime.datetime`, or ``None`` if ``value`` was
        ``None`` or could not be parsed.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # datetime.fromisoformat does not accept a trailing "Z" before 3.11.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


@dataclass(frozen=True, slots=True)
class Job:
    """An enqueued send job.

    Attributes:
        id: The server-assigned job identifier (a UUID string).
        state: The current :class:`JobState`.
    """

    id: str
    state: JobState

    @classmethod
    def from_dict(cls, data: dict) -> Job:
        """Build a :class:`Job` from a decoded JSON object.

        Args:
            data: A mapping with ``id`` and ``state`` keys.

        Returns:
            The parsed :class:`Job`.
        """
        return cls(id=str(data["id"]), state=JobState.from_str(str(data["state"])))


@dataclass(frozen=True, slots=True)
class Health:
    """A snapshot of the server's health endpoint.

    Attributes:
        status: Overall status string (e.g. ``"ok"``).
        queue_depth: Number of jobs currently queued.
        runner_state: One of ``idle``, ``dispatching``, ``waitingForCallback``.
        uptime: Human-readable uptime string (e.g. ``"1h 23m"``).
        last_callback_at: When the last callback was received, if any.
        tls_expires_at: When the server's TLS certificate expires, if known.
        raw: The original decoded JSON, preserved for forward compatibility.
    """

    status: str
    queue_depth: int
    runner_state: str
    uptime: str
    last_callback_at: datetime.datetime | None
    tls_expires_at: datetime.datetime | None
    raw: dict

    @classmethod
    def from_dict(cls, data: dict) -> Health:
        """Build a :class:`Health` from a decoded JSON object.

        Missing or ``None`` fields are tolerated; ISO 8601 timestamps are
        parsed into aware datetimes. The original mapping is kept in ``raw``.

        Args:
            data: The decoded ``/health`` response body.

        Returns:
            The parsed :class:`Health`.
        """
        return cls(
            status=str(data.get("status", "")),
            queue_depth=int(data.get("queueDepth", 0) or 0),
            runner_state=str(data.get("runnerState", "")),
            uptime=str(data.get("uptime", "")),
            last_callback_at=_parse_datetime(data.get("lastCallbackAt")),
            tls_expires_at=_parse_datetime(data.get("tlsExpiresAt")),
            raw=data,
        )


@dataclass(frozen=True, slots=True)
class Alias:
    """A named alias mapping to a contact (phone number or email).

    Attributes:
        name: The alias name (lowercased server-side on store).
        contact: The underlying contact handle.
    """

    name: str
    contact: str


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file attachment to include with a message.

    The payload carries base64-encoded bytes; the server derives the final
    extension and MIME type from ``name`` (and ``mime`` when provided).

    Attributes:
        data: Base64-encoded file bytes.
        name: The filename, which drives the extension/MIME on the server.
        mime: The MIME type, or ``None`` to let the server infer it.
    """

    data: str
    name: str
    mime: str | None = None

    @classmethod
    def from_path(
        cls,
        path: str | os.PathLike[str],
        *,
        name: str | None = None,
        mime: str | None = None,
    ) -> Attachment:
        """Build an :class:`Attachment` by reading a file from disk.

        This performs blocking file IO and is intended to be called by the
        caller before invoking the async client, which never touches the
        filesystem itself.

        Args:
            path: Path to the file to attach.
            name: Override for the attachment filename; defaults to the
                basename of ``path``.
            mime: Override for the MIME type; guessed via :mod:`mimetypes`
                when not given.

        Returns:
            The constructed :class:`Attachment`.
        """
        fs_path = os.fspath(path)
        with open(fs_path, "rb") as handle:
            raw = handle.read()
        resolved_name = name if name is not None else os.path.basename(fs_path)
        resolved_mime = mime if mime is not None else mimetypes.guess_type(resolved_name)[0]
        return cls.from_bytes(raw, resolved_name, resolved_mime)

    @classmethod
    def from_bytes(
        cls,
        raw: bytes,
        name: str,
        mime: str | None = None,
    ) -> Attachment:
        """Build an :class:`Attachment` from in-memory bytes.

        Args:
            raw: The raw file bytes.
            name: The attachment filename.
            mime: The MIME type, or ``None`` to let the server infer it.

        Returns:
            The constructed :class:`Attachment`.
        """
        encoded = base64.b64encode(raw).decode("ascii")
        return cls(data=encoded, name=name, mime=mime)

    def to_payload(self) -> dict:
        """Serialise to the JSON shape expected by ``POST /send``.

        Returns:
            A mapping with ``data`` and ``name``, plus ``mime`` when it is set.
        """
        payload: dict = {"data": self.data, "name": self.name}
        if self.mime is not None:
            payload["mime"] = self.mime
        return payload
