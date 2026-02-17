"""Tests for batch directory processing."""

import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

from src.api import RateLimitError
from src.parser import compile_pattern, DEFAULT_PATTERN
from src.processor import process_directory, _INTER_FILE_SLEEP
from src.types import (
    PatientLookupResult,
    PatientLookupStatus,
    UploadResult,
    UploadStatus,
)

METATAGS = {
    "L": "laboratory",
    "R": "radiology",
    "HP": "h&p/consults",
}

FAKE_CONFIG = {"access_token": "test-token"}


@pytest.fixture
def pattern_re():
    return compile_pattern(DEFAULT_PATTERN, METATAGS)


@pytest.fixture
def doc_dir(tmp_path):
    """Create a temp directory with sample files."""
    (tmp_path / "DOE,JANE_R_020326_CXR.pdf").write_text("fake")
    (tmp_path / "SMITH,JOHN_L_120124_CBC.pdf").write_text("fake")
    (tmp_path / "badfile.txt").write_text("fake")
    return tmp_path


def _found_patient(pid=42, doc=7):
    return PatientLookupResult(status=PatientLookupStatus.FOUND, patient_id=pid, doctor_id=doc)


def _not_found():
    return PatientLookupResult(status=PatientLookupStatus.NOT_FOUND)


def _upload_ok(doc_id=100):
    return UploadResult(status=UploadStatus.SUCCESS, document_id=doc_id)


def _upload_fail():
    return UploadResult(status=UploadStatus.FAILED, detail="400: bad request")


# -----------------------------------------------------------------------
# Counting: skipped, failed, success, duplicates
# -----------------------------------------------------------------------

class TestCounting:
    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_skipped_unparseable(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.return_value = _found_patient()
        mock_upload.return_value = _upload_ok()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Skipped:       1" in output  # badfile.txt
        assert "Uploaded:      2" in output

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_failed_patient_not_found(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.return_value = _not_found()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Failed:        2" in output  # both valid files fail
        assert "Skipped:       1" in output  # badfile.txt

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=True)
    @patch("src.processor.find_patient")
    def test_duplicates_counted(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.return_value = _found_patient()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Duplicates:    2" in output
        mock_upload.assert_not_called()

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_upload_failure_counted(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.return_value = _found_patient()
        mock_upload.return_value = _upload_fail()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Failed:        2" in output


# -----------------------------------------------------------------------
# Dry run
# -----------------------------------------------------------------------

class TestDryRun:
    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_dry_run_skips_upload(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.return_value = _found_patient()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re, dry_run=True)

        mock_upload.assert_not_called()
        mock_dup.assert_called()  # duplicate check still runs
        output = capsys.readouterr().out
        assert "DRY RUN" in output
        assert "Uploaded:      2" in output  # counted as would-upload

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_dry_run_no_file_moves(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, tmp_path):
        mock_find.return_value = _found_patient()
        dest = tmp_path / "done"

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re, dry_run=True, dest_dir=str(dest))

        # Files should still be in original directory
        assert (doc_dir / "DOE,JANE_R_020326_CXR.pdf").exists()
        assert (doc_dir / "SMITH,JOHN_L_120124_CBC.pdf").exists()

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=True)
    @patch("src.processor.find_patient")
    def test_dry_run_reports_duplicates(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.return_value = _found_patient()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re, dry_run=True)

        mock_upload.assert_not_called()
        output = capsys.readouterr().out
        assert "DRY RUN" in output
        assert "Duplicates:    2" in output
        assert "Uploaded:      0" in output


# -----------------------------------------------------------------------
# Dest dir (file moving)
# -----------------------------------------------------------------------

class TestDestDir:
    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_moves_on_success(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, tmp_path):
        mock_find.return_value = _found_patient()
        mock_upload.return_value = _upload_ok()
        dest = tmp_path / "done"

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re, dest_dir=str(dest))

        assert (dest / "DOE,JANE_R_020326_CXR.pdf").exists()
        assert (dest / "SMITH,JOHN_L_120124_CBC.pdf").exists()
        # Originals should be gone
        assert not (doc_dir / "DOE,JANE_R_020326_CXR.pdf").exists()
        assert not (doc_dir / "SMITH,JOHN_L_120124_CBC.pdf").exists()
        # Unparseable stays
        assert (doc_dir / "badfile.txt").exists()

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_failed_files_not_moved(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, tmp_path):
        mock_find.return_value = _found_patient()
        mock_upload.return_value = _upload_fail()
        dest = tmp_path / "done"

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re, dest_dir=str(dest))

        # Failed files stay in source
        assert (doc_dir / "DOE,JANE_R_020326_CXR.pdf").exists()
        assert not (dest / "DOE,JANE_R_020326_CXR.pdf").exists()


# -----------------------------------------------------------------------
# Multi-worker
# -----------------------------------------------------------------------

class TestMultiWorker:
    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_multi_worker_same_results(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.return_value = _found_patient()
        mock_upload.return_value = _upload_ok()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re, num_workers=3)

        output = capsys.readouterr().out
        assert "Uploaded:      2" in output
        assert "Skipped:       1" in output
        assert "3 worker" in output

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_multi_worker_moves_files(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, tmp_path):
        mock_find.return_value = _found_patient()
        mock_upload.return_value = _upload_ok()
        dest = tmp_path / "done"

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re, dest_dir=str(dest), num_workers=2)

        assert (dest / "DOE,JANE_R_020326_CXR.pdf").exists()
        assert (dest / "SMITH,JOHN_L_120124_CBC.pdf").exists()


# -----------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_directory(self, tmp_path, pattern_re, capsys):
        process_directory(FAKE_CONFIG, str(tmp_path), METATAGS, pattern_re)
        output = capsys.readouterr().out
        assert "No files found" in output

    def test_nonexistent_directory(self, pattern_re):
        with pytest.raises(SystemExit):
            process_directory(FAKE_CONFIG, "/nonexistent/path", METATAGS, pattern_re)


# -----------------------------------------------------------------------
# API errors (raise_for_status / network failures)
# -----------------------------------------------------------------------

class TestAPIErrors:
    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient", side_effect=requests.ConnectionError("connection refused"))
    def test_patient_lookup_error_counted_as_failed(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Failed:        2" in output
        assert "patient lookup failed" in output
        mock_upload.assert_not_called()

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", side_effect=requests.HTTPError("500 Server Error"))
    @patch("src.processor.find_patient")
    def test_duplicate_check_error_counted_as_failed(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.return_value = _found_patient()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Failed:        2" in output
        assert "duplicate check failed" in output
        mock_upload.assert_not_called()

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_patient_lookup_http_error_counted_as_failed(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        mock_find.side_effect = requests.HTTPError("401 Unauthorized")

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Failed:        2" in output
        mock_upload.assert_not_called()


# -----------------------------------------------------------------------
# Rate-limit handling in processor
# -----------------------------------------------------------------------

class TestRateLimitHandling:
    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_rate_limit_on_patient_lookup(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        """RateLimitError during patient lookup is counted as rate-limited."""
        mock_find.side_effect = RateLimitError("rate limit exceeded", retry_after=2.0, is_app_limit=False)

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Rate-limited:  2" in output
        assert "RATE LIMITED" in output
        mock_upload.assert_not_called()

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate")
    @patch("src.processor.find_patient")
    def test_rate_limit_on_duplicate_check(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        """RateLimitError during duplicate check is counted as rate-limited."""
        mock_find.return_value = _found_patient()
        mock_dup.side_effect = RateLimitError("rate limit exceeded")

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Rate-limited:  2" in output
        mock_upload.assert_not_called()

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_rate_limit_on_upload(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        """RateLimitError during upload is counted as rate-limited."""
        mock_find.return_value = _found_patient()
        mock_upload.side_effect = RateLimitError("rate limit exceeded")

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Rate-limited:  2" in output

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_app_limit_shows_clear_message(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        """Application-level rate limit shows a clear user-facing message."""
        mock_find.side_effect = RateLimitError(
            "DrChrono application rate limit reached (500 requests/hour)",
            retry_after=3600.0,
            is_app_limit=True,
        )

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "500 requests/hour" in output
        assert "wait until the top of the hour" in output

    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_rate_limited_separate_from_failed(self, mock_find, mock_dup, mock_upload, doc_dir, pattern_re, capsys):
        """Rate-limited files appear in their own section, not under Failed."""
        mock_find.side_effect = RateLimitError("rate limit exceeded")

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        output = capsys.readouterr().out
        assert "Rate-limited:  2" in output
        assert "Failed:        0" in output


# -----------------------------------------------------------------------
# Inter-file sleep
# -----------------------------------------------------------------------

class TestInterFileSleep:
    @patch("src.processor.time.sleep")
    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_sleep_between_files(self, mock_find, mock_dup, mock_upload, mock_sleep, doc_dir, pattern_re):
        """Workers sleep between consecutive files (not before the first)."""
        mock_find.return_value = _found_patient()
        mock_upload.return_value = _upload_ok()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re)

        # 3 files total, worker processes them sequentially
        # Sleep calls: 1 jitter + inter-file sleeps between non-first files
        inter_file_sleeps = [
            c for c in mock_sleep.call_args_list
            if c[0][0] == _INTER_FILE_SLEEP
        ]
        # 3 files = 2 inter-file sleeps (before file 2 and 3)
        assert len(inter_file_sleeps) == 2

    @patch("src.processor.time.sleep")
    @patch("src.processor.upload_document")
    @patch("src.processor.is_duplicate", return_value=False)
    @patch("src.processor.find_patient")
    def test_no_sleep_in_dry_run(self, mock_find, mock_dup, mock_upload, mock_sleep, doc_dir, pattern_re):
        """Dry run skips inter-file sleep since no API calls are throttled."""
        mock_find.return_value = _found_patient()

        process_directory(FAKE_CONFIG, str(doc_dir), METATAGS, pattern_re, dry_run=True)

        inter_file_sleeps = [
            c for c in mock_sleep.call_args_list
            if c[0][0] == _INTER_FILE_SLEEP
        ]
        assert len(inter_file_sleeps) == 0
