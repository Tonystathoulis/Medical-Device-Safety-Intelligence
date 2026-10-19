# File purpose: checks date parsing, inclusive range boundaries, and invalid or missing values.
from datetime import date
from medical_device_safety.validation import parse_date, in_date_range

# Parse valid dates and compare dates at, inside, and outside the selected bounds.
def test_dates_and_boundaries():
    assert parse_date("20190101") == date(2019, 1, 1)
    assert in_date_range("20190101", date(2019,1,1), date(2020,1,1))
    assert not in_date_range("20181231", date(2019,1,1), date(2020,1,1))

# Pass missing and malformed strings to the parser and verify they become null results.
def test_missing_and_invalid_dates():
    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("not-a-date") is None
