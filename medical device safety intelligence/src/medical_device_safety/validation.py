# File purpose: provides small reusable checks for dates, identifiers, and field coverage.
"""Date, identifier and field quality helpers."""
from datetime import date, datetime
from typing import Any, Iterable
import pandas as pd


# Step 1: convert a source date to a date value, returning None for missing or invalid input.
def parse_date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return pd.to_datetime(value, errors="raise").date()
    except (ValueError, TypeError, OverflowError):
        return None


# Step 2: check whether a parsed date falls inside inclusive extraction bounds.
def in_date_range(value: Any, start: date, end: date) -> bool:
    parsed = parse_date(value)
    return parsed is not None and start <= parsed <= end


# Step 3: return each repeated nonempty identifier once, in encounter order.
def duplicate_values(values: Iterable[Any]) -> list[Any]:
    seen, duplicates = set(), []
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        key = str(value)
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    return duplicates


# Step 4: count populated and missing values and record the observed Python types.
def field_profile(records: list[dict[str, Any]], fields: list[str]) -> dict[str, dict[str, Any]]:
    profile = {}
    for field in fields:
        vals = [r.get(field) for r in records]
        present = [v for v in vals if v is not None and v != ""]
        profile[field] = {"present": len(present), "missing": len(vals)-len(present),
                          "sample_types": sorted({type(v).__name__ for v in present})}
    return profile
