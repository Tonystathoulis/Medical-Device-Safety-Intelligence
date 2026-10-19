# File purpose: checks bounded extraction windows, paging probes, reconciliation, and atomic manifests.
import json
from datetime import date

import pytest

from medical_device_safety.bounded_extraction import (
    Budget, Limits, SafetyLimit, _date_status, _next_link, _query,
    reconcile, write_atomic_json,
)


# Build two date queries and verify their exact inclusive quarter boundaries.
def test_quarter_partition_boundaries_and_inclusive_query():
    assert _query("date_of_event", date(2019, 1, 1), date(2019, 3, 31)) == "date_of_event:[20190101 TO 20190331]"
    assert _query("event_date_initiated", date(2024, 1, 1), date(2024, 3, 31)) == "event_date_initiated:[20240101 TO 20240331]"


# Parse the server's next link and verify missing Link headers produce no cursor.
def test_search_after_uses_only_api_continuation_link():
    header = '<https://api.fda.gov/device/event.json?skip=0&search_after=opaque>; rel="next"'
    assert _next_link(header) == "https://api.fda.gov/device/event.json?skip=0&search_after=opaque"
    assert _next_link("") is None


# Compare complete, duplicate, and short identifier lists against API totals.
def test_duplicate_and_count_reconciliation():
    good = reconcile(2, ["a", "b"], {"missing": 0, "invalid": 0, "outside_window": 0})
    assert good["complete"] is True
    duplicate = reconcile(2, ["a", "a"], {"missing": 0, "invalid": 0, "outside_window": 0})
    assert duplicate["duplicate_identifier_count"] == 1
    assert duplicate["complete"] is False
    short = reconcile(3, ["a", "b"], {"missing": 0, "invalid": 0, "outside_window": 0})
    assert short["complete"] is False


# Mix valid, missing, invalid, and outside dates and verify each category is counted.
def test_missing_invalid_and_out_of_window_dates():
    rows = [{"date": "20190101"}, {"date": "bad"}, {"date": ""}, {"date": "20200101"}]
    result = _date_status(rows, "date", date(2019, 1, 1), date(2019, 3, 31))
    assert result == {"missing": 1, "invalid": 1, "outside_window": 1}


# Exhaust request and record caps separately and confirm extraction stops before exceeding them.
def test_request_and_record_safety_budgets():
    budget = Budget(Limits(max_requests=2, max_records=1), max_retries=1)
    budget.attempts = 1
    with pytest.raises(SafetyLimit, match="request-attempt"):
        budget.check()
    budget = Budget(Limits(max_requests=5, max_records=1), max_retries=0)
    budget.records = 1
    with pytest.raises(SafetyLimit, match="record budget"):
        budget.check(reserve_records=1)


# Write two manifests to one path and verify the second safely replaces the first.
def test_atomic_manifest_is_valid_and_restart_does_not_append(tmp_path):
    output = tmp_path / "experiment.json"
    write_atomic_json({"run": 1}, output)
    write_atomic_json({"run": 2}, output)
    assert json.loads(output.read_text()) == {"run": 2}
    assert not output.with_suffix(".json.tmp").exists()
