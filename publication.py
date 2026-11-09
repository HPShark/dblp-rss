"""Shared publication chronology for dump selection, SQLite, and RSS rendering."""

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import re
import unicodedata


@dataclass(frozen=True)
class PublicationDate:
    value: str = ""
    precision: str = "unknown"
    source: str = ""

    @property
    def lower_bound(self):
        if not self.value:
            return None
        return date.fromisoformat((self.value + "-01-01")[:10])


MONTHS = {name: index for index, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"),
    ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
    ("aug", "august"), ("sep", "sept", "september"),
    ("oct", "october"), ("nov", "november"), ("dec", "december"),
), 1) for name in names}


def publication_year(value):
    match = re.fullmatch(r"\s*(\d{4})\s*", str(value or ""))
    return int(match[1]) if match and 1 <= int(match[1]) <= 9999 else 0


@lru_cache(maxsize=8192)
def publication_date(year, month=""):
    """Read only explicit XML dates. Never infer dates from DOI, issue, or mdate."""
    year_number = publication_year(year)
    if not year_number:
        return PublicationDate()
    fallback = PublicationDate(f"{year_number:04d}", "year", "dblp:year")
    value = str(month or "").strip().casefold()
    if not value:
        return fallback
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", value)
    month_number, day = 0, None
    if iso:
        # A conflicting online-first year must not override the bibliographic year.
        if int(iso[1]) != year_number:
            return fallback
        month_number = int(iso[2])
        day = int(iso[3]) if iso[3] else None
    elif re.fullmatch(r"\d{1,2}", value):
        month_number = int(value)
    else:
        words = re.findall(r"[a-z]+", value)
        months = [MONTHS[word] for word in words if word in MONTHS]
        if months:
            # A range such as March-April retains month precision at its start;
            # the original range is preserved separately in the RSS description.
            month_number = months[0]
            numbers = re.findall(r"\d+", value)
            years = [int(n) for n in numbers if len(n) == 4]
            if any(n != year_number for n in years):
                return fallback
            days = [int(n) for n in numbers if len(n) <= 2]
            if len(months) == 1 and len(days) == 1:
                day = days[0]
        else:
            month_range = re.fullmatch(r"(\d{1,2})\s*[-/\u2013\u2014]\s*(\d{1,2})", value)
            if month_range and 1 <= int(month_range[2]) <= 12:
                month_number = int(month_range[1])
    try:
        date(year_number, month_number, 1 if day is None else day)
    except ValueError:
        return fallback
    if day is not None:
        return PublicationDate(f"{year_number:04d}-{month_number:02d}-{day:02d}",
                               "day", "dblp:year/month")
    return PublicationDate(f"{year_number:04d}-{month_number:02d}", "month", "dblp:year/month")


@lru_cache(maxsize=16384)
def natural_number(value):
    """Comparable text key: numeric/ranged issues, textual supplements, then missing."""
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    if not value:
        return "0!"
    tokens = re.findall(r"[0-9]+|[^\W\d_]+", value)
    if not tokens:
        return "0!"
    result = "2" if value[0].isdigit() else "1"
    for token in tokens:
        if token.isascii() and token.isdigit():
            digits = token.lstrip("0") or "0"
            result += f"D{len(digits):04d}:{digits}"
        else:
            result += "T" + token + ":"
    # The terminator sorts before additional tokens: 3 < 3-4 < 4, S2 < S10.
    return result + "!"


def publication_key(stream, year, volume, number, month, modified, key):
    """A single descending key, identical in Python heaps and SQLite queries."""
    year_number = publication_year(year)
    parts = [f"{year_number:04d}"]
    if str(stream).startswith("journals/"):
        parts.extend((natural_number(volume), natural_number(number)))
    published = publication_date(str(year or ""), str(month or ""))
    lower = published.lower_bound
    parts.extend((lower.isoformat() if lower else "0000-00-00", str(modified or ""),
                  str(key or "")))
    return "|".join(parts)


def value_from_row(row, name, default=""):
    return row[name] if name in row.keys() else default


def row_publication_key(stream, row):
    return publication_key(stream, row["year"], row["volume"], row["number"],
                           value_from_row(row, "month"), row["mdate"], row["key"])
