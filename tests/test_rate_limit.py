"""Tests for rate-limit handling: retry logic, backoff, and RateLimitError."""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from src import api
from src.api import (
    MAX_RETRIES,
    RateLimitError,
    _request_with_retry,
    find_patient,
    get_patient_documents,
    upload_document,
)
from src.types import PatientLookupStatus, UploadStatus

FAKE_CONFIG = {"access_token": "test-token"}


def _mock_response(status_code=200, json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = json.dumps(json_data or {})
    resp.headers = headers or {}
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture(autouse=True)
def _clear_caches():
    api._patient_cache.clear()
    api._documents_cache.clear()
    yield


# -----------------------------------------------------------------------
# _request_with_retry
# -----------------------------------------------------------------------

class TestRequestWithRetry:
    @patch("src.api.requests.request")
    def test_success_on_first_try(self, mock_request):
        """Non-429 response returned immediately without retries."""
        mock_request.return_value = _mock_response(200, {"ok": True})
        resp = _request_with_retry("GET", "https://example.com/api")
        assert resp.status_code == 200
        assert mock_request.call_count == 1

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_retries_on_429_then_succeeds(self, mock_request, mock_sleep):
        """429 on first attempt, success on second — should retry once."""
        mock_request.side_effect = [
            _mock_response(429, headers={"Retry-After": "1"}),
            _mock_response(200, {"ok": True}),
        ]
        resp = _request_with_retry("GET", "https://example.com/api")
        assert resp.status_code == 200
        assert mock_request.call_count == 2
        assert mock_sleep.call_count == 1

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_exhausts_retries_raises_rate_limit_error(self, mock_request, mock_sleep):
        """All retries exhausted raises RateLimitError."""
        mock_request.return_value = _mock_response(429, headers={"Retry-After": "2"})
        with pytest.raises(RateLimitError, match="rate limit"):
            _request_with_retry("GET", "https://example.com/api")
        # 1 initial + MAX_RETRIES retries
        assert mock_request.call_count == MAX_RETRIES + 1
        # Sleeps happen before each retry (not before the final raise)
        assert mock_sleep.call_count == MAX_RETRIES

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_uses_retry_after_header(self, mock_request, mock_sleep):
        """Retry-After header value is respected (plus jitter)."""
        mock_request.side_effect = [
            _mock_response(429, headers={"Retry-After": "5"}),
            _mock_response(200, {"ok": True}),
        ]
        resp = _request_with_retry("GET", "https://example.com/api")
        assert resp.status_code == 200
        # Sleep should be at least 5 seconds (Retry-After) but capped at BACKOFF_MAX + jitter
        actual_sleep = mock_sleep.call_args[0][0]
        assert actual_sleep >= 5.0

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_exponential_backoff_without_retry_after(self, mock_request, mock_sleep):
        """Without Retry-After header, uses exponential backoff."""
        mock_request.side_effect = [
            _mock_response(429),  # no Retry-After
            _mock_response(429),
            _mock_response(200, {"ok": True}),
        ]
        resp = _request_with_retry("GET", "https://example.com/api")
        assert resp.status_code == 200
        # First retry: base * 2^0 = 2s + jitter; second: base * 2^1 = 4s + jitter
        first_sleep = mock_sleep.call_args_list[0][0][0]
        second_sleep = mock_sleep.call_args_list[1][0][0]
        assert first_sleep >= 2.0
        assert second_sleep >= 4.0

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_app_limit_detected_with_large_retry_after(self, mock_request, mock_sleep):
        """Retry-After > 60 indicates application-level limit."""
        mock_request.return_value = _mock_response(429, headers={"Retry-After": "3600"})
        with pytest.raises(RateLimitError) as exc_info:
            _request_with_retry("GET", "https://example.com/api")
        assert exc_info.value.is_app_limit is True
        assert "500 requests/hour" in str(exc_info.value)

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_system_limit_not_flagged_as_app_limit(self, mock_request, mock_sleep):
        """Small Retry-After is not flagged as application-level limit."""
        mock_request.return_value = _mock_response(429, headers={"Retry-After": "2"})
        with pytest.raises(RateLimitError) as exc_info:
            _request_with_retry("GET", "https://example.com/api")
        assert exc_info.value.is_app_limit is False

    @patch("src.api.requests.request")
    def test_non_429_error_not_retried(self, mock_request):
        """Non-429 errors (e.g. 500) are returned immediately, not retried."""
        mock_request.return_value = _mock_response(500)
        resp = _request_with_retry("GET", "https://example.com/api")
        assert resp.status_code == 500
        assert mock_request.call_count == 1

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_backoff_capped_at_max(self, mock_request, mock_sleep):
        """Sleep time never exceeds BACKOFF_MAX + jitter."""
        mock_request.side_effect = [
            _mock_response(429, headers={"Retry-After": "999"}),
            _mock_response(200, {"ok": True}),
        ]
        _request_with_retry("GET", "https://example.com/api")
        actual_sleep = mock_sleep.call_args[0][0]
        assert actual_sleep <= api.BACKOFF_MAX + 1.0  # max + jitter ceiling


# -----------------------------------------------------------------------
# find_patient with 429
# -----------------------------------------------------------------------

class TestFindPatientRateLimit:
    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_find_patient_retries_on_429(self, mock_request, mock_sleep):
        """find_patient succeeds after a transient 429."""
        mock_request.side_effect = [
            _mock_response(429, headers={"Retry-After": "1"}),
            _mock_response(200, {"results": [
                {"id": 42, "doctor": 7, "first_name": "JANE", "last_name": "DOE"},
            ]}),
        ]
        result = find_patient(FAKE_CONFIG, "DOE", "JANE")
        assert result.status == PatientLookupStatus.FOUND
        assert result.patient_id == 42

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_find_patient_raises_on_exhausted_retries(self, mock_request, mock_sleep):
        mock_request.return_value = _mock_response(429, headers={"Retry-After": "2"})
        with pytest.raises(RateLimitError):
            find_patient(FAKE_CONFIG, "DOE", "JANE")


# -----------------------------------------------------------------------
# upload_document with 429
# -----------------------------------------------------------------------

class TestUploadDocumentRateLimit:
    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_upload_retries_on_429(self, mock_request, mock_sleep, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_text("fake pdf")
        mock_request.side_effect = [
            _mock_response(429, headers={"Retry-After": "1"}),
            _mock_response(201, {"id": 999}),
        ]
        result = upload_document(FAKE_CONFIG, str(test_file), 1, 2, "2026-02-03", "CXR", "radiology")
        assert result.status == UploadStatus.SUCCESS
        assert result.document_id == 999

    @patch("src.api.time.sleep")
    @patch("src.api.requests.request")
    def test_upload_raises_on_exhausted_retries(self, mock_request, mock_sleep, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_text("fake pdf")
        mock_request.return_value = _mock_response(429, headers={"Retry-After": "2"})
        with pytest.raises(RateLimitError):
            upload_document(FAKE_CONFIG, str(test_file), 1, 2, "2026-02-03", "CXR", "radiology")


# -----------------------------------------------------------------------
# RateLimitError attributes
# -----------------------------------------------------------------------

class TestRateLimitErrorAttributes:
    def test_retry_after_stored(self):
        err = RateLimitError("msg", retry_after=120.0, is_app_limit=True)
        assert err.retry_after == 120.0
        assert err.is_app_limit is True
        assert str(err) == "msg"

    def test_defaults(self):
        err = RateLimitError("msg")
        assert err.retry_after is None
        assert err.is_app_limit is False
