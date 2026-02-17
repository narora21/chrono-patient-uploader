"""Tests for DrChrono API operations: patient lookup, duplicate detection, upload."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src import api
from src.api import find_patient, is_duplicate, upload_document
from src.types import PatientLookupStatus, UploadStatus

FAKE_CONFIG = {"access_token": "test-token"}


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear module-level caches before each test."""
    api._patient_cache.clear()
    api._documents_cache.clear()
    yield


# -----------------------------------------------------------------------
# find_patient
# -----------------------------------------------------------------------

class TestFindPatient:
    @patch("src.api.requests.request")
    def test_not_found(self, mock_request):
        mock_request.return_value = _mock_response({"results": []})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE")
        assert result.status == PatientLookupStatus.NOT_FOUND
        assert result.patient_id is None

    @patch("src.api.requests.request")
    def test_single_match(self, mock_request):
        mock_request.return_value = _mock_response({"results": [
            {"id": 42, "doctor": 7, "first_name": "JANE", "last_name": "DOE"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE")
        assert result.status == PatientLookupStatus.FOUND
        assert result.patient_id == 42
        assert result.doctor_id == 7

    @patch("src.api.requests.request")
    def test_multiple_matches(self, mock_request):
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "last_name": "DOE", "date_of_birth": "1990-01-01"},
            {"id": 2, "first_name": "JANE", "last_name": "DOE", "date_of_birth": "1985-05-05"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE")
        assert result.status == PatientLookupStatus.MULTIPLE_MATCHES
        assert result.detail is not None
        assert "1990-01-01" in result.detail
        assert "1985-05-05" in result.detail

    @patch("src.api.requests.request")
    def test_middle_initial_filters(self, mock_request):
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "middle_name": "Marie", "last_name": "DOE"},
            {"id": 2, "first_name": "JANE", "middle_name": "Ann", "last_name": "DOE"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE", middle_initial="M")
        assert result.status == PatientLookupStatus.FOUND
        assert result.patient_id == 1

    @patch("src.api.requests.request")
    def test_middle_initial_no_match_keeps_all(self, mock_request):
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "middle_name": "Ann", "last_name": "DOE", "date_of_birth": "1990-01-01"},
            {"id": 2, "first_name": "JANE", "middle_name": "Beth", "last_name": "DOE", "date_of_birth": "1985-05-05"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE", middle_initial="Z")
        assert result.status == PatientLookupStatus.MULTIPLE_MATCHES

    @patch("src.api.requests.request")
    def test_exact_name_narrows_multiple(self, mock_request):
        """SMITH search returns SMITH and SMITHSON — exact match picks SMITH."""
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JOHN", "last_name": "SMITH", "doctor": 5},
            {"id": 2, "first_name": "JOHN", "last_name": "SMITHSON", "doctor": 6},
        ]})
        result = find_patient(FAKE_CONFIG, "SMITH", "JOHN")
        assert result.status == PatientLookupStatus.FOUND
        assert result.patient_id == 1

    @patch("src.api.requests.request")
    def test_exact_first_name_narrows_multiple(self, mock_request):
        """JO search returns JO and JOHN — exact match picks JO."""
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JO", "last_name": "DOE", "doctor": 5},
            {"id": 2, "first_name": "JOHN", "last_name": "DOE", "doctor": 6},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JO")
        assert result.status == PatientLookupStatus.FOUND
        assert result.patient_id == 1

    @patch("src.api.requests.request")
    def test_middle_initial_still_multiple(self, mock_request):
        """Middle initial filters but still leaves multiple matches."""
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "middle_name": "Marie", "last_name": "DOE", "date_of_birth": "1990-01-01"},
            {"id": 2, "first_name": "JANE", "middle_name": "May", "last_name": "DOE", "date_of_birth": "1985-05-05"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE", middle_initial="M")
        assert result.status == PatientLookupStatus.MULTIPLE_MATCHES

    @patch("src.api.requests.request")
    def test_cache_returns_same_result(self, mock_request):
        mock_request.return_value = _mock_response({"results": [
            {"id": 42, "doctor": 7, "first_name": "JANE", "last_name": "DOE"},
        ]})
        result1 = find_patient(FAKE_CONFIG, "DOE", "JANE")
        result2 = find_patient(FAKE_CONFIG, "DOE", "JANE")
        assert result1 == result2
        assert mock_request.call_count == 1  # only one API call

    @patch("src.api.requests.request")
    def test_cache_key_case_insensitive(self, mock_request):
        mock_request.return_value = _mock_response({"results": [
            {"id": 42, "doctor": 7, "first_name": "Jane", "last_name": "Doe"},
        ]})
        find_patient(FAKE_CONFIG, "DOE", "JANE")
        find_patient(FAKE_CONFIG, "doe", "jane")
        assert mock_request.call_count == 1

    @patch("src.api.requests.request")
    def test_data_key_fallback(self, mock_request):
        """API may return 'data' instead of 'results'."""
        mock_request.return_value = _mock_response({"data": [
            {"id": 10, "doctor": 3, "first_name": "JOHN", "last_name": "SMITH"},
        ]})
        result = find_patient(FAKE_CONFIG, "SMITH", "JOHN")
        assert result.status == PatientLookupStatus.FOUND
        assert result.patient_id == 10

    @patch("src.api.requests.request")
    def test_dob_narrows_multiple_to_one(self, mock_request):
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "last_name": "DOE", "doctor": 5, "date_of_birth": "1990-01-01"},
            {"id": 2, "first_name": "JANE", "last_name": "DOE", "doctor": 6, "date_of_birth": "1985-05-05"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE", dob="1990-01-01")
        assert result.status == PatientLookupStatus.FOUND
        assert result.patient_id == 1

    @patch("src.api.requests.request")
    def test_dob_no_match_keeps_all(self, mock_request):
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "last_name": "DOE", "date_of_birth": "1990-01-01"},
            {"id": 2, "first_name": "JANE", "last_name": "DOE", "date_of_birth": "1985-05-05"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE", dob="2000-12-25")
        assert result.status == PatientLookupStatus.MULTIPLE_MATCHES

    @patch("src.api.requests.request")
    def test_dob_not_provided_unchanged(self, mock_request):
        """Without DOB, multiple matches remain multiple."""
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "last_name": "DOE", "date_of_birth": "1990-01-01"},
            {"id": 2, "first_name": "JANE", "last_name": "DOE", "date_of_birth": "1985-05-05"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE")
        assert result.status == PatientLookupStatus.MULTIPLE_MATCHES

    @patch("src.api.requests.request")
    def test_dob_with_middle_initial_combined(self, mock_request):
        """DOB + middle initial together narrow from 3 to 1."""
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "middle_name": "Marie", "last_name": "DOE", "doctor": 5, "date_of_birth": "1990-01-01"},
            {"id": 2, "first_name": "JANE", "middle_name": "Marie", "last_name": "DOE", "doctor": 6, "date_of_birth": "1985-05-05"},
            {"id": 3, "first_name": "JANE", "middle_name": "Ann", "last_name": "DOE", "doctor": 7, "date_of_birth": "1990-01-01"},
        ]})
        result = find_patient(FAKE_CONFIG, "DOE", "JANE", middle_initial="M", dob="1990-01-01")
        assert result.status == PatientLookupStatus.FOUND
        assert result.patient_id == 1

    @patch("src.api.requests.request")
    def test_cache_key_includes_dob(self, mock_request):
        """Different DOBs should produce separate cache entries."""
        mock_request.return_value = _mock_response({"results": [
            {"id": 1, "first_name": "JANE", "last_name": "DOE", "doctor": 5, "date_of_birth": "1990-01-01"},
            {"id": 2, "first_name": "JANE", "last_name": "DOE", "doctor": 6, "date_of_birth": "1985-05-05"},
        ]})
        result1 = find_patient(FAKE_CONFIG, "DOE", "JANE", dob="1990-01-01")
        result2 = find_patient(FAKE_CONFIG, "DOE", "JANE", dob="1985-05-05")
        assert result1.patient_id == 1
        assert result2.patient_id == 2
        assert mock_request.call_count == 2


# -----------------------------------------------------------------------
# is_duplicate
# -----------------------------------------------------------------------

class TestIsDuplicate:
    def _setup_docs_cache(self, patient_id, docs):
        api._documents_cache[patient_id] = docs

    def test_exact_match(self):
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": '["radiology"]'},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "radiology") is True

    def test_different_date(self):
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": '["radiology"]'},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2025-01-01", "CXR", "radiology") is False

    def test_different_description(self):
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": '["radiology"]'},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "MRI", "radiology") is False

    def test_different_tag(self):
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": '["radiology"]'},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "laboratory") is False

    def test_metatags_as_list(self):
        """metatags may already be a parsed list, not a JSON string."""
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": ["radiology"]},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "radiology") is True

    def test_metatags_null(self):
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": None},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "radiology") is False

    def test_metatags_empty_string(self):
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": ""},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "radiology") is False

    def test_metatags_malformed_json(self):
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": "{bad json"},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "radiology") is False

    def test_no_existing_documents(self):
        self._setup_docs_cache(1, [])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "radiology") is False

    def test_multiple_docs_one_matches(self):
        self._setup_docs_cache(1, [
            {"date": "2025-01-01", "description": "CBC", "metatags": '["laboratory"]'},
            {"date": "2026-02-03", "description": "CXR", "metatags": '["radiology"]'},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "radiology") is True

    def test_multiple_tags_in_metatags(self):
        self._setup_docs_cache(1, [
            {"date": "2026-02-03", "description": "CXR", "metatags": '["laboratory", "radiology"]'},
        ])
        assert is_duplicate(FAKE_CONFIG, 1, "2026-02-03", "CXR", "radiology") is True


# -----------------------------------------------------------------------
# upload_document
# -----------------------------------------------------------------------

class TestUploadDocument:
    @patch("src.api.requests.request")
    def test_success(self, mock_request, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_text("fake pdf")
        mock_request.return_value = _mock_response({"id": 999}, status_code=201)

        result = upload_document(FAKE_CONFIG, str(test_file), 1, 2, "2026-02-03", "CXR", "radiology")
        assert result.status == UploadStatus.SUCCESS
        assert result.document_id == 999

    @patch("src.api.requests.request")
    def test_failure(self, mock_request, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_text("fake pdf")
        mock_request.return_value = _mock_response({"error": "bad request"}, status_code=400)

        result = upload_document(FAKE_CONFIG, str(test_file), 1, 2, "2026-02-03", "CXR", "radiology")
        assert result.status == UploadStatus.FAILED
        assert "400" in result.detail
