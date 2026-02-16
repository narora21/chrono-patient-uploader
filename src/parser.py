"""Filename pattern compilation and parsing."""

import datetime
import re
from pathlib import Path
from typing import Optional

from src.types import ParsedFilename

DEFAULT_PATTERN = "{name}_{tag}_{date}_{description}"

_PLACEHOLDER_REGEX: dict[str, str] = {
    "name": r"(?P<last_name>[^,]+),(?P<first_name>[^,]+?)(?:,(?P<middle_initial>[^,]+?))?",
    "last_name": r"(?P<last_name>.+?)",
    "first_name": r"(?P<first_name>.+?)",
    "middle_initial": r"(?P<middle_initial>[A-Z])",
    "date": r"(?P<date>\d{6})",
}

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def compile_pattern(pattern: str, metatags: dict) -> re.Pattern:
    """Compile a filename pattern string into a regex.

    Supported placeholders: {name}, {last_name}, {first_name},
    {middle_initial}, {tag}, {date}, {description}.
    Literal characters between placeholders are escaped.
    """
    tag_keys = sorted(metatags.keys(), key=len, reverse=True)
    tag_regex = r"(?P<tag>" + "|".join(re.escape(k) for k in tag_keys) + ")"

    placeholders = {**_PLACEHOLDER_REGEX, "tag": tag_regex}

    result_parts: list[str] = []
    pos = 0
    found_description = False

    for m in _PLACEHOLDER_RE.finditer(pattern):
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

    tag_code = groups.get("tag", "").upper()
    if tag_code not in metatags:
        return None
    tag_full = metatags[tag_code]

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
