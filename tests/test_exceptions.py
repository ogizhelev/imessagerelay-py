"""Tests for imessagerelay.exceptions."""

from __future__ import annotations

import pytest

from imessagerelay.exceptions import (
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


class TestHierarchy:
    def test_connection_error_is_base(self) -> None:
        assert issubclass(IMessageRelayConnectionError, IMessageRelayError)

    def test_timeout_is_connection_error(self) -> None:
        assert issubclass(IMessageRelayTimeoutError, IMessageRelayConnectionError)
        assert issubclass(IMessageRelayTimeoutError, IMessageRelayError)

    def test_api_error_is_base(self) -> None:
        assert issubclass(APIError, IMessageRelayError)

    @pytest.mark.parametrize(
        "cls",
        [
            AuthenticationError,
            ForbiddenError,
            ValidationError,
            NotFoundError,
            ConflictError,
            RateLimitError,
            ServerError,
        ],
    )
    def test_api_subclasses(self, cls: type) -> None:
        assert issubclass(cls, APIError)


class TestAPIError:
    def test_attributes(self) -> None:
        payload = {"error": "boom"}
        err = APIError("boom", status_code=400, payload=payload)
        assert err.message == "boom"
        assert err.status_code == 400
        assert err.payload is payload

    def test_payload_defaults_none(self) -> None:
        err = APIError("boom", status_code=500)
        assert err.payload is None

    def test_str_format(self) -> None:
        err = APIError("bad request", status_code=400)
        assert str(err) == "[400] bad request"


class TestErrorForStatus:
    @pytest.mark.parametrize(
        "status, expected",
        [
            (400, ValidationError),
            (401, AuthenticationError),
            (403, ForbiddenError),
            (404, NotFoundError),
            (409, ConflictError),
            (415, UnsupportedMediaTypeError),
            (429, RateLimitError),
            (500, ServerError),
            (503, ServerError),
            (599, ServerError),
        ],
    )
    def test_mapping(self, status: int, expected: type) -> None:
        err = error_for_status(status, "msg", {"error": "msg"})
        assert type(err) is expected
        assert err.status_code == status
        assert err.message == "msg"
        assert err.payload == {"error": "msg"}

    def test_unmapped_4xx_falls_back_to_apierror(self) -> None:
        err = error_for_status(418, "teapot", None)
        assert type(err) is APIError

    def test_unmapped_other_falls_back_to_apierror(self) -> None:
        err = error_for_status(302, "redirect", None)
        assert type(err) is APIError
