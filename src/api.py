"""DrChrono API operations: patient lookup, duplicate detection, document upload."""

import json
import random
import threading
import time
from pathlib import Path

import requests

from src.auth import DRCHRONO_BASE, api_headers
from src.types import (
    PatientLookupResult,
    PatientLookupStatus,
    UploadResult,
    UploadStatus,
)

# ---------------------------------------------------------------------------
# Rate-limit handling
# ---------------------------------------------------------------------------

# Retry configuration for 429 responses
MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds — multiplied by 2^attempt + jitter
BACKOFF_MAX = 30  # cap on any single wait


class RateLimitError(Exception):
    """Raised when the API returns 429 and all retries are exhausted.

    Attributes:
        retry_after: Seconds the server asked us to wait (from Retry-After header), or None.
        is_app_limit: True when this looks like the 500 req/hour application-level limit.
    """

    def __init__(self, message: str, retry_after: float | None = None, is_app_limit: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.is_app_limit = is_app_limit


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Execute an HTTP request with retry + exponential backoff on 429 responses.

    For non-429 errors this behaves identically to ``requests.request``.
    """
    for attempt in range(MAX_RETRIES + 1):
        resp = requests.request(method, url, **kwargs)

        if resp.status_code != 429:
            return resp

        # Determine how long to wait
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = BACKOFF_BASE * (2 ** attempt)
        else:
            wait = BACKOFF_BASE * (2 ** attempt)

        wait = min(wait, BACKOFF_MAX)
        wait += random.uniform(0, 1)  # jitter

        # Detect application-level limit (large Retry-After typically means hourly reset)
        is_app_limit = retry_after is not None and float(retry_after) > 60

        if attempt == MAX_RETRIES:
            msg = (
                "DrChrono API rate limit exceeded (HTTP 429). "
                "All retries exhausted."
            )
            if is_app_limit:
                msg = (
                    "DrChrono application rate limit reached (500 requests/hour). "
                    "Please wait until the top of the hour and try again."
                )
            raise RateLimitError(msg, retry_after=float(retry_after) if retry_after else None, is_app_limit=is_app_limit)

        print(f"  [RATE LIMIT] 429 received — waiting {wait:.1f}s before retry {attempt + 1}/{MAX_RETRIES}…")
        time.sleep(wait)

    # Should not reach here, but just in case:
    raise RateLimitError("DrChrono API rate limit exceeded (HTTP 429).")

# ---------------------------------------------------------------------------
# Patient lookup (by name, with caching)
# ---------------------------------------------------------------------------

_patient_cache: dict[str, PatientLookupResult] = {}
_patient_cache_lock = threading.Lock()


def find_patient(config, last_name, first_name, middle_initial=None, dob=None) -> PatientLookupResult:
    """Find a patient by name via the DrChrono API.

    If zero or multiple matches are found, returns an error.
    Results are cached so the same patient isn't looked up twice in one run.
    Thread-safe.
    """
    cache_key = f"{last_name.lower()}|{first_name.lower()}|{(middle_initial or '').lower()}|{dob or ''}"
    with _patient_cache_lock:
        if cache_key in _patient_cache:
            return _patient_cache[cache_key]

    resp = _request_with_retry(
        "GET",
        f"{DRCHRONO_BASE}/api/patients",
        headers=api_headers(config),
        params={"last_name": last_name, "first_name": first_name},
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", data.get("data", []))

    if middle_initial and len(results) > 1:
        filtered = [
            p for p in results
            if (p.get("middle_name") or "").upper().startswith(middle_initial.upper())
        ]
        if filtered:
            results = filtered

    # Try exact name match to narrow down results
    if len(results) > 1:
        exact = [
            p for p in results
            if p.get("last_name", "").upper() == last_name.upper()
            and p.get("first_name", "").upper() == first_name.upper()
        ]
        if len(exact) >= 1:
            results = exact

    # Filter by date of birth if available
    if dob and len(results) > 1:
        dob_filtered = [
            p for p in results
            if p.get("date_of_birth") == dob
        ]
        if dob_filtered:
            results = dob_filtered

    if len(results) == 0:
        result = PatientLookupResult(status=PatientLookupStatus.NOT_FOUND)
    elif len(results) > 1:
        names = "; ".join(
            f"{p.get('first_name', '')} {p.get('middle_name', '') or ''} {p.get('last_name', '')}".strip()
            + f" (DOB: {p.get('date_of_birth', 'N/A')}, ID: {p['id']})"
            for p in results
        )
        result = PatientLookupResult(
            status=PatientLookupStatus.MULTIPLE_MATCHES,
            detail=names,
        )
    else:
        patient = results[0]
        result = PatientLookupResult(
            status=PatientLookupStatus.FOUND,
            patient_id=patient["id"],
            doctor_id=patient.get("doctor"),
        )

    with _patient_cache_lock:
        _patient_cache.setdefault(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

_documents_cache: dict[int, list[dict]] = {}
_documents_cache_lock = threading.Lock()


def get_patient_documents(config, patient_id: int) -> list[dict]:
    """Fetch all existing documents for a patient (cached per run). Thread-safe."""
    with _documents_cache_lock:
        if patient_id in _documents_cache:
            return _documents_cache[patient_id]

    documents: list[dict] = []
    url: str | None = f"{DRCHRONO_BASE}/api/documents"
    params: dict = {"patient": patient_id}

    while url:
        resp = _request_with_retry("GET", url, headers=api_headers(config), params=params)
        resp.raise_for_status()
        data = resp.json()
        documents.extend(data.get("results", data.get("data", [])))
        url = data.get("next")
        params = {}

    with _documents_cache_lock:
        _documents_cache.setdefault(patient_id, documents)
    return documents


def is_duplicate(config, patient_id: int, date: str, description: str, metatag: str) -> bool:
    """Check if a document with the same date, description, and metatag already exists."""
    existing = get_patient_documents(config, patient_id)
    for doc in existing:
        if doc.get("date") != date:
            continue
        if doc.get("description") != description:
            continue
        raw_tags = doc.get("metatags") or "[]"
        try:
            tags = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
        except (json.JSONDecodeError, TypeError):
            tags = []
        if metatag in tags:
            return True
    return False


# ---------------------------------------------------------------------------
# Document upload
# ---------------------------------------------------------------------------

def upload_document(config, file_path, patient_id, doctor_id, date, description, metatag) -> UploadResult:
    """Upload a single document to DrChrono."""
    metatags_json = json.dumps([metatag])

    with open(file_path, "rb") as f:
        resp = _request_with_retry(
            "POST",
            f"{DRCHRONO_BASE}/api/documents",
            headers=api_headers(config),
            data={
                "patient": patient_id,
                "doctor": doctor_id,
                "date": date,
                "description": description,
                "metatags": metatags_json,
            },
            files={"document": (Path(file_path).name, f)},
        )

    if resp.status_code == 201:
        doc = resp.json()
        return UploadResult(status=UploadStatus.SUCCESS, document_id=doc.get("id"))
    else:
        return UploadResult(
            status=UploadStatus.FAILED,
            detail=f"{resp.status_code}: {resp.text}",
        )
