"""Filename pattern compilation and parsing."""

import datetime
import re
from pathlib import Path
from typing import Optional

from src.types import ParsedFilename

DEFAULT_PATTERN = "{name}({dob})_{tag}_{date}_{description}"

_PLACEHOLDER_REGEX: dict[str, str] = {
    "name": r"(?P<last_name>[^,]+),\s*(?P<first_name>[^,]+?)(?:,\s*(?P<middle_initial>[^,]+?))?",
    "last_name": r"(?P<last_name>.+?)",
    "first_name": r"(?P<first_name>.+?)",
    "middle_initial": r"(?P<middle_initial>[A-Z])",
    "date": r"(?P<date>\d{6})",
    "dob": r"(?P<dob>\d{6})",
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
        literal_before = pattern[pos:m.start()]
        name = m.group(1)
        after_end = m.end()

        # Detect ({placeholder}) — parentheses make the group optional
        wrapped_in_parens = (
            literal_before.endswith("(")
            and after_end < len(pattern)
            and pattern[after_end] == ")"
        )

        if wrapped_in_parens:
            # Append literal text before the opening paren
            literal_without_paren = literal_before[:-1]
            if literal_without_paren:
                result_parts.append(re.escape(literal_without_paren))

            if name == "description":
                found_description = True
                ph_regex = r"(?P<description>.+)"
            elif name in placeholders:
                ph_regex = placeholders[name]
            else:
                raise ValueError(f"Unknown placeholder: {{{name}}}")

            result_parts.append(r"(?:\(" + ph_regex + r"\))?")
            pos = after_end + 1  # skip past closing ")"
        else:
            if literal_before:
                result_parts.append(re.escape(literal_before))

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
    if middle_initial:
        middle_initial = middle_initial.strip() or None
    description = groups.get("description", "").strip()

    dob_raw = groups.get("dob")
    dob_date = parse_date_mmddyy(dob_raw) if dob_raw else None
    dob_iso = dob_date.isoformat() if dob_date else None

    if not last_name or not first_name:
        return None

    if not description:
        description = tag_full

    return ParsedFilename(
        last_name=last_name,
        first_name=first_name,
        middle_initial=middle_initial,
        dob=dob_iso,
        tag_code=tag_code,
        tag_full=tag_full,
        date=doc_date.isoformat(),
        description=description,
    )
