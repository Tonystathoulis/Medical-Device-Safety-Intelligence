# File purpose: collects API pages safely and stops when results end or repeat.
"""Bounded pagination helpers that stop on short or duplicate pages."""
from typing import Any, Callable


# Step 1: request each page, detect repeated data, and stop on empty or short pages.
def collect_pages(fetch: Callable[[int, int], list[dict[str, Any]]], page_size: int,
                  max_pages: int, identifier: str) -> list[dict[str, Any]]:
    rows, seen_page_keys = [], set()
    for page_number in range(max_pages):
        page = fetch(page_number * page_size, page_size)
        if not page:
            break
        keys = tuple(str(row.get(identifier, "")) for row in page)
        if keys in seen_page_keys:
            raise ValueError("Repeated page detected; pagination stopped")
        seen_page_keys.add(keys)
        rows.extend(page)
        if len(page) < page_size:
            break
    return rows
