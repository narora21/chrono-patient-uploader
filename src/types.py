"""Enums and Pydantic models used across the application."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PatientLookupStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    MULTIPLE_MATCHES = "multiple_matches"


class UploadStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class FileErrorReason(str, Enum):
    PARSE_FAILED = "parse_failed"
    PATIENT_NOT_FOUND = "patient_not_found"
    PATIENT_MULTIPLE_MATCHES = "patient_multiple_matches"
    DUPLICATE = "duplicate"
    UPLOAD_FAILED = "upload_failed"
    RATE_LIMITED = "rate_limited"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ParsedFilename(BaseModel):
    last_name: str
    first_name: str
    middle_initial: Optional[str] = None
    tag_code: str
    tag_full: str
    date: str
    description: str


class PatientLookupResult(BaseModel):
    status: PatientLookupStatus
    patient_id: Optional[int] = None
    doctor_id: Optional[int] = None
    detail: Optional[str] = None


class UploadResult(BaseModel):
    status: UploadStatus
    document_id: Optional[int] = None
    detail: Optional[str] = None


class FileError(BaseModel):
    filename: str
    reason: FileErrorReason
    detail: Optional[str] = None
