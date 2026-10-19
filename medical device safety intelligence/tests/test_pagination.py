# File purpose: verifies page collection stops correctly and guards against repeated pages.
import pytest
from medical_device_safety.pagination import collect_pages

# Return a full page and a short final page, then check all rows are collected once.
def test_pagination_short_page_termination():
    pages = {0:[{"id":1},{"id":2}], 2:[{"id":3}]}
    assert len(collect_pages(lambda skip, limit: pages.get(skip, []), 2, 10, "id")) == 3

# Repeat the same page from a fake fetcher and confirm pagination raises an error.
def test_duplicate_page_protection():
    page = [{"id": 1}, {"id": 2}]
    with pytest.raises(ValueError, match="Repeated page"):
        collect_pages(lambda skip, limit: page, 2, 3, "id")
