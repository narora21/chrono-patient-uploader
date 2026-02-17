"""Batch directory processing: parse, lookup, upload, report."""

import random
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests

from src.api import RateLimitError, find_patient, is_duplicate, upload_document
from src.parser import parse_filename
from src.types import (
    FileError,
    FileErrorReason,
    PatientLookupStatus,
    UploadStatus,
)

# Max jitter in seconds between worker starts
_WORKER_JITTER_MAX = 0.5

# Sleep between consecutive API-touching files per worker (seconds).
# DrChrono throttles at 290 requests per 10-minute window and 10 requests
# per second.  A 2-second pause keeps us well under both limits.
_INTER_FILE_SLEEP = 2.0


class _FileResult:
    """Result of processing a single file."""

    def __init__(
        self,
        filename: str,
        succeeded: bool = False,
        error: Optional[FileError] = None,
        category: str = "failed",  # "failed", "skipped", "duplicate"
        document_id: Optional[int] = None,
    ):
        self.filename = filename
        self.succeeded = succeeded
        self.error = error
        self.category = category
        self.document_id = document_id


def _process_single_file(
    config,
    file_path: Path,
    metatags: dict,
    pattern_re: re.Pattern,
    dry_run: bool,
    dest_dir: Optional[Path],
    worker_id: int,
) -> _FileResult:
    """Process a single file: parse, lookup patient, check dupe, upload."""
    filename = file_path.name
    tag = f"[W{worker_id}]"

    parsed = parse_filename(filename, metatags, pattern_re)
    if parsed is None:
        print(f"  {tag} SKIP  {filename} (could not parse filename)")
        return _FileResult(
            filename=filename,
            error=FileError(filename=filename, reason=FileErrorReason.PARSE_FAILED),
            category="skipped",
        )

    print(f"  {tag} Processing: {filename}")
    print(f"  {tag}   Patient: {parsed.last_name}, {parsed.first_name}"
          f"{' ' + parsed.middle_initial if parsed.middle_initial else ''}")
    print(f"  {tag}   Tag: {parsed.tag_code} ({parsed.tag_full})")
    print(f"  {tag}   Date: {parsed.date}")
    print(f"  {tag}   Description: {parsed.description}")
    if parsed.dob:
        print(f"  {tag}   DOB: {parsed.dob}")

    try:
        lookup = find_patient(
            config,
            parsed.last_name,
            parsed.first_name,
            parsed.middle_initial,
            dob=parsed.dob,
        )
    except RateLimitError as exc:
        print(f"  {tag}   RATE LIMITED  {exc}")
        if exc.is_app_limit:
            print(f"  {tag}   *** You have hit the DrChrono application rate limit (500 requests/hour). ***")
            print(f"  {tag}   *** Please wait until the top of the hour before running again. ***")
        return _FileResult(
            filename=filename,
            error=FileError(filename=filename, reason=FileErrorReason.RATE_LIMITED, detail=str(exc)),
            category="failed",
        )
    except requests.RequestException as exc:
        detail = f"patient lookup failed: {exc}"
        print(f"  {tag}   FAIL  {detail}")
        return _FileResult(
            filename=filename,
            error=FileError(filename=filename, reason=FileErrorReason.UPLOAD_FAILED, detail=detail),
            category="failed",
        )

    if lookup.status != PatientLookupStatus.FOUND:
        if lookup.status == PatientLookupStatus.NOT_FOUND:
            error_reason = FileErrorReason.PATIENT_NOT_FOUND
            print(f"  {tag}   FAIL  patient not found")
        else:
            error_reason = FileErrorReason.PATIENT_MULTIPLE_MATCHES
            print(f"  {tag}   FAIL  patient multiple matches: {lookup.detail}")
        return _FileResult(
            filename=filename,
            error=FileError(filename=filename, reason=error_reason, detail=lookup.detail),
            category="failed",
        )

    try:
        dup = is_duplicate(config, lookup.patient_id, parsed.date, parsed.description, parsed.tag_full)
    except RateLimitError as exc:
        print(f"  {tag}   RATE LIMITED  {exc}")
        if exc.is_app_limit:
            print(f"  {tag}   *** You have hit the DrChrono application rate limit (500 requests/hour). ***")
            print(f"  {tag}   *** Please wait until the top of the hour before running again. ***")
        return _FileResult(
            filename=filename,
            error=FileError(filename=filename, reason=FileErrorReason.RATE_LIMITED, detail=str(exc)),
            category="failed",
        )
    except requests.RequestException as exc:
        detail = f"duplicate check failed: {exc}"
        print(f"  {tag}   FAIL  {detail}")
        return _FileResult(
            filename=filename,
            error=FileError(filename=filename, reason=FileErrorReason.UPLOAD_FAILED, detail=detail),
            category="failed",
        )
    if dup:
        print(f"  {tag}   DUP   duplicate document already exists")
        return _FileResult(
            filename=filename,
            error=FileError(
                filename=filename,
                reason=FileErrorReason.DUPLICATE,
                detail=f"patient {lookup.patient_id}, date {parsed.date}, description '{parsed.description}'",
            ),
            category="duplicate",
        )

    if dry_run:
        print(f"  {tag}   DRY   would upload to patient {lookup.patient_id}")
        return _FileResult(filename=filename, succeeded=True)

    try:
        result = upload_document(
            config,
            str(file_path),
            lookup.patient_id,
            lookup.doctor_id,
            parsed.date,
            parsed.description,
            parsed.tag_full,
        )
    except RateLimitError as exc:
        print(f"  {tag}   RATE LIMITED  {exc}")
        if exc.is_app_limit:
            print(f"  {tag}   *** You have hit the DrChrono application rate limit (500 requests/hour). ***")
            print(f"  {tag}   *** Please wait until the top of the hour before running again. ***")
        return _FileResult(
            filename=filename,
            error=FileError(filename=filename, reason=FileErrorReason.RATE_LIMITED, detail=str(exc)),
            category="failed",
        )

    if result.status == UploadStatus.SUCCESS:
        print(f"  {tag}   OK    Document ID: {result.document_id}")
        if dest_dir:
            dest_path = dest_dir / filename
            shutil.move(str(file_path), str(dest_path))
            print(f"  {tag}   MOVED {dest_path}")
        return _FileResult(filename=filename, succeeded=True, document_id=result.document_id)
    else:
        print(f"  {tag}   FAIL  {result.detail}")
        return _FileResult(
            filename=filename,
            error=FileError(filename=filename, reason=FileErrorReason.UPLOAD_FAILED, detail=result.detail),
            category="failed",
        )


def _worker_task(
    worker_id: int,
    config,
    file_paths: list[Path],
    metatags: dict,
    pattern_re: re.Pattern,
    dry_run: bool,
    dest_dir: Optional[Path],
) -> list[_FileResult]:
    """Worker function that processes its assigned chunk of files with an initial jitter."""
    jitter = random.uniform(0, _WORKER_JITTER_MAX)
    time.sleep(jitter)

    results = []
    for i, file_path in enumerate(file_paths):
        if i > 0 and not dry_run:
            time.sleep(_INTER_FILE_SLEEP)
        result = _process_single_file(config, file_path, metatags, pattern_re, dry_run, dest_dir, worker_id)
        results.append(result)
    return results


def process_directory(config, directory, metatags, pattern_re: re.Pattern, dry_run=False, dest_dir=None, num_workers=1):
    """Read all files from a directory, parse filenames, and upload to DrChrono."""
    directory = Path(directory)
    if not directory.is_dir():
        print(f"Error: '{directory}' is not a directory.")
        sys.exit(1)

    dest_path = None
    if dest_dir:
        dest_path = Path(dest_dir)
        dest_path.mkdir(parents=True, exist_ok=True)

    if dry_run:
        print("[DRY RUN] No files will be uploaded or moved.\n")

    files = sorted([f for f in directory.iterdir() if f.is_file()])
    if not files:
        print(f"No files found in '{directory}'.")
        return

    print(f"Found {len(files)} file(s) in '{directory}'.")
    print(f"Using {num_workers} worker(s).\n")

    # Distribute files round-robin across workers
    chunks: list[list[Path]] = [[] for _ in range(num_workers)]
    for i, f in enumerate(files):
        chunks[i % num_workers].append(f)

    succeeded = 0
    failed_files: list[FileError] = []
    skipped_files: list[FileError] = []
    duplicate_files: list[FileError] = []
    rate_limited_files: list[FileError] = []

    if num_workers == 1:
        # Single worker — run directly, no threading overhead
        all_results = _worker_task(1, config, files, metatags, pattern_re, dry_run, dest_path)
    else:
        all_results = []
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(
                    _worker_task, worker_id + 1, config, chunk, metatags, pattern_re, dry_run, dest_path
                ): worker_id
                for worker_id, chunk in enumerate(chunks)
                if chunk  # skip empty chunks
            }
            for future in as_completed(futures):
                all_results.extend(future.result())

    for r in all_results:
        if r.succeeded:
            succeeded += 1
        elif r.error:
            if r.category == "skipped":
                skipped_files.append(r.error)
            elif r.category == "duplicate":
                duplicate_files.append(r.error)
            elif r.error.reason == FileErrorReason.RATE_LIMITED:
                rate_limited_files.append(r.error)
            else:
                failed_files.append(r.error)

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
    _print_report("Rate-Limited Files", rate_limited_files)

    if rate_limited_files:
        print(
            "\n*** Some files were not uploaded because the DrChrono API rate "
            "limit was reached. ***\n"
            "*** Please wait until the top of the hour and try again for "
            "the remaining files. ***"
        )

    # --- Summary ---
    print(f"\n{'=' * 50}")
    print(f"Uploaded:      {succeeded}")
    print(f"Failed:        {len(failed_files)}")
    print(f"Skipped:       {len(skipped_files)}")
    print(f"Duplicates:    {len(duplicate_files)}")
    print(f"Rate-limited:  {len(rate_limited_files)}")
    print(f"Total:         {len(files)}")
    print(f"{'=' * 50}")

    if dry_run:
        print("\n[DRY RUN] No documents were uploaded or moved. Disable dry run to upload.")
