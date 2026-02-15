#!/usr/bin/env python3
"""CLI entrypoint for the DrChrono Batch Document Uploader."""

import argparse
import sys

from src.auth import ensure_auth
from src.config import ensure_credentials, load_config, load_metatags
from src.parser import DEFAULT_PATTERN, compile_pattern
from src.processor import process_directory


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
