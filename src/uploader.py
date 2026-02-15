#!/usr/bin/env python3
"""DrChrono Document Uploader - Batch upload documents from a directory."""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import threading
import urllib.parse
import webbrowser
from enum import Enum
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel

DRCHRONO_BASE = "https://app.drchrono.com"
REDIRECT_PORT = 8585
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
METATAG_FILE = os.path.join(APP_DIR, "metatag.json")


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


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def load_metatags():
    if not os.path.exists(METATAG_FILE):
        print(f"Error: metatag.json not found at {METATAG_FILE}")
        print("Create it with tag code -> full name mappings, e.g.:")
        print('  {"L": "laboratory", "R": "radiology", ...}')
        sys.exit(1)
    with open(METATAG_FILE, "r") as f:
        return json.load(f)


def ensure_credentials(config):
    if not config.get("client_id") or not config.get("client_secret"):
        print("No DrChrono credentials found. Let's set them up.")
        print("(Register an app at https://app.drchrono.com/api-management/ first)\n")
        config["client_id"] = input("Client ID: ").strip()
        config["client_secret"] = input("Client Secret: ").strip()
        save_config(config)
    return config


# ---------------------------------------------------------------------------
# OAuth2
# ---------------------------------------------------------------------------

class _CallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect and captures the authorization code."""

    auth_code = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "error" in params:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization denied. You can close this tab.")
            _CallbackHandler.auth_code = None
        elif "code" in params:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization successful! You can close this tab.")
            _CallbackHandler.auth_code = params["code"][0]
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Unexpected response. You can close this tab.")
            _CallbackHandler.auth_code = None

        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format, *args):
        pass


def authorize(config):
    """Run the full OAuth2 browser flow and return updated config with tokens."""
    client_id = urllib.parse.quote(config["client_id"])
    redirect = urllib.parse.quote(REDIRECT_URI)
    scopes = urllib.parse.quote("patients:summary:read patients:read clinical:read clinical:write")
    url = (
        f"{DRCHRONO_BASE}/o/authorize/"
        f"?redirect_uri={redirect}&response_type=code"
        f"&client_id={client_id}&scope={scopes}"
    )

    _CallbackHandler.auth_code = None
    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)

    print("Opening browser for DrChrono authorization...")
    webbrowser.open(url)
    print("Waiting for authorization (complete the login in your browser)...")
    server.handle_request()
    server.server_close()

    code = _CallbackHandler.auth_code
    if not code:
        print("Authorization failed or was cancelled.")
        sys.exit(1)

    resp = requests.post(f"{DRCHRONO_BASE}/o/token/", data={
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
    })
    resp.raise_for_status()
    data = resp.json()

    config["access_token"] = data["access_token"]
    config["refresh_token"] = data["refresh_token"]
    config["expires_at"] = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=data["expires_in"])
    ).isoformat()
    save_config(config)
    print("Authorization successful! Tokens saved.\n")
    return config


def refresh_token(config):
    """Refresh an expired access token."""
    resp = requests.post(f"{DRCHRONO_BASE}/o/token/", data={
        "refresh_token": config["refresh_token"],
        "grant_type": "refresh_token",
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
    })
    resp.raise_for_status()
    data = resp.json()

    config["access_token"] = data["access_token"]
    config["refresh_token"] = data["refresh_token"]
    config["expires_at"] = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=data["expires_in"])
    ).isoformat()
    save_config(config)
    return config


def ensure_auth(config):
    """Ensure we have a valid access token, refreshing or re-authorizing as needed."""
    if not config.get("access_token"):
        return authorize(config)

    expires_at = datetime.datetime.fromisoformat(config["expires_at"])
    if datetime.datetime.now(datetime.timezone.utc) >= expires_at:
        print("Access token expired, refreshing...")
        try:
            return refresh_token(config)
        except requests.HTTPError:
            print("Refresh failed, re-authorizing...")
            return authorize(config)

    return config


def api_headers(config):
    return {"Authorization": f"Bearer {config['access_token']}"}


# ---------------------------------------------------------------------------
# Filename pattern compilation & parsing
# ---------------------------------------------------------------------------

DEFAULT_PATTERN = "{name}_{tag}_{date}_{description}"

# Regex fragments for each placeholder
_PLACEHOLDER_REGEX: dict[str, str] = {
    "name": r"(?P<last_name>[^,]+),\s*(?P<first_name>\S+?)(?:\s+(?P<middle_initial>[A-Z]))?",
    "last_name": r"(?P<last_name>.+?)",
    "first_name": r"(?P<first_name>.+?)",
    "middle_initial": r"(?P<middle_initial>[A-Z])",
    "date": r"(?P<date>\d{6})",
}

# Placeholders that are always valid even if not matched (optional)
_OPTIONAL_PLACEHOLDERS = {"middle_initial"}

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def compile_pattern(pattern: str, metatags: dict) -> re.Pattern:
    """Compile a filename pattern string into a regex.

    Supported placeholders: {name}, {last_name}, {first_name},
    {middle_initial}, {tag}, {date}, {description}.
    Literal characters between placeholders are escaped.
    """
    # Build tag alternation sorted longest-first so e.g. "HP" matches before "H"
    tag_keys = sorted(metatags.keys(), key=len, reverse=True)
    tag_regex = r"(?P<tag>" + "|".join(re.escape(k) for k in tag_keys) + ")"

    placeholders = {**_PLACEHOLDER_REGEX, "tag": tag_regex}

    result_parts: list[str] = []
    pos = 0
    found_description = False

    for m in _PLACEHOLDER_RE.finditer(pattern):
        # Add escaped literal text between placeholders
        if m.start() > pos:
            result_parts.append(re.escape(pattern[pos:m.start()]))

        name = m.group(1)
        if name == "description":
            found_description = True
            result_parts.append(r"(?P<description>.+)")
        elif name in placeholders:
            result_parts.append(placeholders[name])
        else:
            raise ValueError(f"Unknown placeholder: {{{name}}}")

        pos = m.end()

    # Add any trailing literal text
    if pos < len(pattern):
        result_parts.append(re.escape(pattern[pos:]))

    if not found_description:
        raise ValueError("Pattern must include {description} placeholder")

    return re.compile("^" + "".join(result_parts) + "$")


def parse_date_mmddyy(date_str: str) -> Optional[datetime.date]:
    """Parse a MMDDYY date string into a date object."""
    if len(date_str) != 6 or not date_str.isdigit():
        return None
    mm, dd, yy = date_str[0:2], date_str[2:4], date_str[4:6]
    year = int(yy)
    year = 2000 + year if year <= 50 else 1900 + year
    try:
        return datetime.date(year, int(mm), int(dd))
    except ValueError:
        return None


def parse_filename(filename: str, metatags: dict, pattern_re: re.Pattern) -> Optional[ParsedFilename]:
    """Parse a filename using the compiled pattern regex."""
    stem = Path(filename).stem
    m = pattern_re.match(stem)
    if not m:
        return None

    groups = m.groupdict()

    # Validate tag
    tag_code = groups.get("tag", "").upper()
    if tag_code not in metatags:
        return None
    tag_full = metatags[tag_code]

    # Validate date
    doc_date = parse_date_mmddyy(groups.get("date", ""))
    if doc_date is None:
        return None

    last_name = groups.get("last_name", "").strip()
    first_name = groups.get("first_name", "").strip()
    middle_initial = groups.get("middle_initial")
    description = groups.get("description", "").strip()

    if not last_name or not first_name:
        return None

    if not description:
        description = tag_full

    return ParsedFilename(
        last_name=last_name,
        first_name=first_name,
        middle_initial=middle_initial,
        tag_code=tag_code,
        tag_full=tag_full,
        date=doc_date.isoformat(),
        description=description,
    )


# ---------------------------------------------------------------------------
# Patient lookup (by name, with caching)
# ---------------------------------------------------------------------------

_patient_cache: dict[str, PatientLookupResult] = {}


def find_patient(config, last_name, first_name, middle_initial=None) -> PatientLookupResult:
    """Find a patient by name via the DrChrono API.

    If zero or multiple matches are found, returns an error.
    Results are cached so the same patient isn't looked up twice in one run.
    """
    cache_key = f"{last_name.lower()}|{first_name.lower()}|{(middle_initial or '').lower()}"
    if cache_key in _patient_cache:
        return _patient_cache[cache_key]

    resp = requests.get(
        f"{DRCHRONO_BASE}/api/patients",
        headers=api_headers(config),
        params={"last_name": last_name, "first_name": first_name},
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", data.get("data", []))

    # Filter by middle initial if provided
    if middle_initial and len(results) > 1:
        filtered = [
            p for p in results
            if (p.get("middle_name") or "").upper().startswith(middle_initial.upper())
        ]
        if filtered:
            results = filtered

    if len(results) == 0:
        result = PatientLookupResult(status=PatientLookupStatus.NOT_FOUND)
        _patient_cache[cache_key] = result
        return result

    if len(results) > 1:
        names = "; ".join(
            f"{p.get('first_name', '')} {p.get('middle_name', '') or ''} {p.get('last_name', '')}".strip()
            + f" (DOB: {p.get('date_of_birth', 'N/A')}, ID: {p['id']})"
            for p in results
        )
        result = PatientLookupResult(
            status=PatientLookupStatus.MULTIPLE_MATCHES,
            detail=names,
        )
        _patient_cache[cache_key] = result
        return result

    patient = results[0]
    result = PatientLookupResult(
        status=PatientLookupStatus.FOUND,
        patient_id=patient["id"],
        doctor_id=patient.get("doctor"),
    )
    _patient_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

_documents_cache: dict[int, list[dict]] = {}


def get_patient_documents(config, patient_id: int) -> list[dict]:
    """Fetch all existing documents for a patient (cached per run)."""
    if patient_id in _documents_cache:
        return _documents_cache[patient_id]

    documents: list[dict] = []
    url: str | None = f"{DRCHRONO_BASE}/api/documents"
    params: dict = {"patient": patient_id}

    while url:
        resp = requests.get(url, headers=api_headers(config), params=params)
        resp.raise_for_status()
        data = resp.json()
        documents.extend(data.get("results", data.get("data", [])))
        url = data.get("next")
        params = {}  # next URL already includes query params

    _documents_cache[patient_id] = documents
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
        resp = requests.post(
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


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def process_directory(config, directory, metatags, pattern_re, dry_run=False, dest_dir=None):
    """Read all files from a directory, parse filenames, and upload to DrChrono."""
    directory = Path(directory)
    if not directory.is_dir():
        print(f"Error: '{directory}' is not a directory.")
        sys.exit(1)

    if dest_dir:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        print("[DRY RUN] No files will be uploaded or moved.\n")

    files = sorted([f for f in directory.iterdir() if f.is_file()])
    if not files:
        print(f"No files found in '{directory}'.")
        return

    print(f"Found {len(files)} file(s) in '{directory}'.\n")

    succeeded = 0
    failed_files: list[FileError] = []
    skipped_files: list[FileError] = []
    duplicate_files: list[FileError] = []

    for file_path in files:
        filename = file_path.name
        parsed = parse_filename(filename, metatags, pattern_re)

        if parsed is None:
            print(f"  SKIP  {filename} (could not parse filename)")
            skipped_files.append(FileError(filename=filename, reason=FileErrorReason.PARSE_FAILED))
            continue

        print(f"  Processing: {filename}")
        print(f"    Patient: {parsed.last_name}, {parsed.first_name}"
              f"{' ' + parsed.middle_initial if parsed.middle_initial else ''}")
        print(f"    Tag: {parsed.tag_code} ({parsed.tag_full})")
        print(f"    Date: {parsed.date}")
        print(f"    Description: {parsed.description}")

        lookup = find_patient(
            config,
            parsed.last_name,
            parsed.first_name,
            parsed.middle_initial,
        )

        if lookup.status != PatientLookupStatus.FOUND:
            if lookup.status == PatientLookupStatus.NOT_FOUND:
                error_reason = FileErrorReason.PATIENT_NOT_FOUND
                print(f"    FAIL  patient not found")
            else:
                error_reason = FileErrorReason.PATIENT_MULTIPLE_MATCHES
                print(f"    FAIL  patient multiple matches: {lookup.detail}")
            failed_files.append(FileError(
                filename=filename,
                reason=error_reason,
                detail=lookup.detail,
            ))
            continue

        if not dry_run and is_duplicate(config, lookup.patient_id, parsed.date, parsed.description, parsed.tag_full):
            print(f"    DUP   duplicate document already exists")
            duplicate_files.append(FileError(
                filename=filename,
                reason=FileErrorReason.DUPLICATE,
                detail=f"patient {lookup.patient_id}, date {parsed.date}, description '{parsed.description}'",
            ))
            continue

        if dry_run:
            print(f"    DRY   would upload to patient {lookup.patient_id}")
            succeeded += 1
            continue

        result = upload_document(
            config,
            str(file_path),
            lookup.patient_id,
            lookup.doctor_id,
            parsed.date,
            parsed.description,
            parsed.tag_full,
        )

        if result.status == UploadStatus.SUCCESS:
            print(f"    OK    Document ID: {result.document_id}")
            succeeded += 1
            if dest_dir:
                dest_path = dest_dir / filename
                shutil.move(str(file_path), str(dest_path))
                print(f"    MOVED {dest_path}")
        else:
            print(f"    FAIL  {result.detail}")
            failed_files.append(FileError(
                filename=filename,
                reason=FileErrorReason.UPLOAD_FAILED,
                detail=result.detail,
            ))

    def _print_report(title, items):
        if not items:
            return
        print(f"\n--- {title} ({len(items)}) ---")
        for err in items:
            line = f"  {err.filename}: {err.reason.value}"
            if err.detail:
                line += f" ({err.detail})"
            print(line)

    _print_report("Failed Files", failed_files)
    _print_report("Skipped Files", skipped_files)
    _print_report("Duplicate Files", duplicate_files)

    # --- Summary ---
    print(f"\n{'=' * 50}")
    print(f"Uploaded:   {succeeded}")
    print(f"Failed:     {len(failed_files)}")
    print(f"Skipped:    {len(skipped_files)}")
    print(f"Duplicates: {len(duplicate_files)}")
    print(f"Total:      {len(files)}")
    print(f"{'=' * 50}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Batch upload documents to DrChrono.",
        epilog=(
            "Pattern placeholders: {name} (LAST,FIRST[ M]), {last_name}, {first_name}, "
            "{middle_initial}, {tag}, {date} (MMDDYY), {description}. "
            "Default: %(default)s"
        ),
    )
    parser.add_argument("directory", help="Path to directory containing patient documents")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate files without uploading or moving")
    parser.add_argument("--dest", metavar="DIR", help="Move successfully uploaded files to this directory")
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help="Filename pattern using placeholders (default: %(default)s)",
    )
    args = parser.parse_args()

    print("=== DrChrono Batch Document Uploader ===\n")

    config = load_config()
    config = ensure_credentials(config)
    config = ensure_auth(config)
    metatags = load_metatags()

    try:
        pattern_re = compile_pattern(args.pattern, metatags)
    except ValueError as e:
        print(f"Error in --pattern: {e}")
        sys.exit(1)

    print(f"Using filename pattern: {args.pattern}\n")

    process_directory(config, args.directory, metatags, pattern_re, dry_run=args.dry_run, dest_dir=args.dest)


if __name__ == "__main__":
    main()
