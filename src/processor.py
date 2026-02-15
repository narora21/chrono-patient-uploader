"""Batch directory processing: parse, lookup, upload, report."""

import re
import shutil
import sys
from pathlib import Path

from src.api import find_patient, is_duplicate, upload_document
from src.parser import parse_filename
from src.types import (
    FileError,
    FileErrorReason,
    PatientLookupStatus,
    UploadStatus,
)


def process_directory(config, directory, metatags, pattern_re: re.Pattern, dry_run=False, dest_dir=None):
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
