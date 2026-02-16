#!/usr/bin/env python3
"""CLI entrypoint for the DrChrono Batch Document Uploader."""

import argparse
import sys

from src.auth import ensure_auth
from src.config import ensure_credentials, load_config, load_metatags
from src.parser import DEFAULT_PATTERN, compile_pattern
from src.processor import process_directory
from src.updater import check_for_update, cleanup_old_binary, self_update, uninstall
from src.version import __version__


def _run_upload(args):
    """Run the upload workflow."""
    print("=== DrChrono Batch Document Uploader ===\n")
    check_for_update()

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

    process_directory(
        config, args.directory, metatags, pattern_re,
        dry_run=args.dry_run, dest_dir=args.dest, num_workers=args.num_workers,
    )


def _run_update(args):
    """Run the self-update workflow."""
    self_update(target_version=args.version)


def main():
    cleanup_old_binary()

    parser = argparse.ArgumentParser(
        description="DrChrono Batch Document Uploader.",
    )
    parser.add_argument(
        "--version", action="version", version=f"chrono-uploader {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- upload subcommand (default) ---
    upload_parser = subparsers.add_parser(
        "upload",
        help="Upload documents from a directory",
        epilog=(
            "Pattern placeholders: {name} (LAST,FIRST[,MIDDLE]), {last_name}, {first_name}, "
            "{middle_initial}, {tag}, {date} (MMDDYY), {description}. "
            "Default: {name}_{tag}_{date}_{description}"
        ),
    )
    upload_parser.add_argument("directory", help="Path to directory containing patient documents")
    upload_parser.add_argument("--dry-run", action="store_true", help="Parse and validate files without uploading or moving")
    upload_parser.add_argument("--dest", metavar="DIR", help="Move successfully uploaded files to this directory")
    upload_parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help="Filename pattern using placeholders (default: %(default)s)",
    )
    upload_parser.add_argument(
        "--num-workers",
        type=int,
        default=1,
        metavar="N",
        help="Number of parallel upload workers (default: 1)",
    )

    # --- update subcommand ---
    update_parser = subparsers.add_parser("update", help="Update to the latest version")
    update_parser.add_argument("version", nargs="?", default=None, help="Specific version to install (e.g. v0.0.2). Defaults to latest.")

    # --- uninstall subcommand ---
    subparsers.add_parser("uninstall", help="Remove chrono-uploader from this machine")

    # If no subcommand given but args look like a path, treat as upload
    args = parser.parse_args()

    if args.command is None:
        # Check if first positional arg might be a directory path (not a subcommand)
        if len(sys.argv) > 1 and sys.argv[1] not in ("--help", "-h", "--version"):
            # Re-parse as upload subcommand
            upload_args = upload_parser.parse_args(sys.argv[1:])
            _run_upload(upload_args)
        else:
            parser.print_help()
    elif args.command == "upload":
        _run_upload(args)
    elif args.command == "update":
        _run_update(args)
    elif args.command == "uninstall":
        uninstall()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Exception in main: {e}")
