# File purpose: tests extraction partitions, source normalization, completeness checks, and output schemas.
from datetime import date
from types import SimpleNamespace

import pytest

from medical_device_safety import prepare_data as prep
from medical_device_safety.api_client import APIError


# Inspect fixed partitions and verify they are contiguous and cover the inclusive window.
def test_partitions_are_contiguous_and_cover_selected_inclusive_window():
    assert prep.PARTITIONS[0][0] == prep.START
    assert prep.PARTITIONS[-1][1] == prep.END
    for left, right in zip(prep.PARTITIONS, prep.PARTITIONS[1:]):
        assert (right[0] - left[1]).days == 1
    assert all((end - start).days >= 0 for start, end in prep.PARTITIONS)
    assert all(start >= prep.START and end <= prep.END for start, end in prep.PARTITIONS)


# Normalize one nested report and confirm device and problem entries become keyed child rows.
def test_normalize_maude_keeps_report_grain_and_expands_nested_rows():
    rows = [{
        "mdr_report_key": "r1", "report_number": "n1", "date_of_event": "20260901",
        "date_received": "20261001", "event_type": "Injury", "device": [
            {"brand_name": "A", "device_report_product_code": "ABC"},
            {"brand_name": "B"}], "product_problems": ["Break", "Leak"],
    }]
    result = prep.normalize_maude(rows)
    assert len(result["maude_reports"]) == 1
    assert result["maude_reports"][0]["event_date"] == "2026-09-01"
    assert [row["device_ordinal"] for row in result["maude_devices"]] == [1, 2]
    assert [row["problem_ordinal"] for row in result["maude_product_problems"]] == [1, 2]


# Normalize missing and malformed dates, checking raw source values and parsed nulls are preserved.
def test_invalid_and_missing_dates_are_retained_as_null_with_raw_value():
    result = prep.normalize_maude([
        {"mdr_report_key": "missing", "date_of_event": None},
        {"mdr_report_key": "invalid", "date_of_event": "not-a-date"},
    ])["maude_reports"]
    assert result[0]["event_date"] is None and result[0]["date_of_event_raw"] is None
    assert result[1]["event_date"] is None and result[1]["date_of_event_raw"] == "not-a-date"


# Supply unique, duplicate, and missing identifiers and verify deduplication fails safely.
def test_unique_rejects_missing_id_and_reports_duplicate_count():
    rows, duplicates = prep._unique([{"id": "a"}, {"id": "b"}, {"id": "a"}], "id")
    assert [row["id"] for row in rows] == ["a", "b"]
    assert duplicates == 1
    with pytest.raises(prep.IncompleteExtraction):
        prep._unique([{"id": ""}], "id")


# Fake client: returns predictable count and page responses without contacting openFDA.
class FakeClient:
    def get(self, endpoint, params):
        search = params["search"]
        if search.startswith("_missing_"):
            payload = {"meta": {"results": {"total": 9}}, "results": []}
        elif "20260901 TO 20260901" in search:
            if "skip" not in params:
                payload = {"meta": {"results": {"total": 2}}, "results": []}
            elif params["skip"] == 0:
                payload = {"results": [
                    {"mdr_report_key": "a", "date_of_event": "20260901"},
                    {"mdr_report_key": "b", "date_of_event": "20260901"},
                ]}
            else:
                payload = {"results": []}
        else:
            payload = {"meta": {"results": {"total": 2}}, "results": []}
        return SimpleNamespace(payload=payload, attempts=1)


# Shrink the window, run fake retrieval, and verify counts and date-quality summaries reconcile.
def test_extraction_reconciles_partitions_with_parent_and_reports_missing_dates(monkeypatch):
    monkeypatch.setattr(prep, "PARTITIONS", ((date(2026, 9, 1), date(2026, 9, 1)),))
    monkeypatch.setattr(prep, "START", date(2026, 9, 1))
    monkeypatch.setattr(prep, "END", date(2026, 9, 1))
    budget = {"started": __import__("time").monotonic(), "requests": 0, "attempts": 0, "retries": 0, "records": 0}
    rows, quality = prep.extract_source(FakeClient(), "event", prep.Limits(), budget)
    assert len(rows) == quality["expected"] == 2
    assert quality["complete"] is True
    assert quality["source_missing_date_records_total"] == 9
    assert quality["missing_dates_in_selected_rows"] == quality["invalid_dates"] == quality["outside_range"] == 0


# Return fewer records than the API count and confirm incomplete retrieval raises an error.
def test_extraction_fails_closed_on_incomplete_page(monkeypatch):
    class ShortClient(FakeClient):
        def get(self, endpoint, params):
            if "skip" in params:
                return SimpleNamespace(payload={"results": [{"mdr_report_key": "a", "date_of_event": "20260901"}]}, attempts=1)
            return super().get(endpoint, params)

    monkeypatch.setattr(prep, "PARTITIONS", ((date(2026, 9, 1), date(2026, 9, 1)),))
    monkeypatch.setattr(prep, "START", date(2026, 9, 1))
    monkeypatch.setattr(prep, "END", date(2026, 9, 1))
    budget = {"started": __import__("time").monotonic(), "requests": 0, "attempts": 0, "retries": 0, "records": 0}
    with pytest.raises(prep.IncompleteExtraction, match="did not reconcile"):
        prep.extract_source(ShortClient(), "event", prep.Limits(), budget)


# Raise a fake HTTP failure during paging and verify the extraction is not accepted as complete.
def test_extraction_rejects_api_error_without_marking_complete(monkeypatch):
    class ErrorClient(FakeClient):
        def get(self, endpoint, params):
            if "skip" in params:
                raise APIError("forbidden", status_code=403)
            return super().get(endpoint, params)

    monkeypatch.setattr(prep, "PARTITIONS", ((date(2026, 9, 1), date(2026, 9, 1)),))
    monkeypatch.setattr(prep, "START", date(2026, 9, 1))
    monkeypatch.setattr(prep, "END", date(2026, 9, 1))
    budget = {"started": __import__("time").monotonic(), "requests": 0, "attempts": 0, "retries": 0, "records": 0}
    with pytest.raises(APIError):
        prep.extract_source(ErrorClient(), "event", prep.Limits(), budget)
    assert budget["requests"] == 2
    assert budget["attempts"] == 2  # one successful count request and one failed page attempt


# Inspect declared CSV schemas and verify columns are present and unique.
def test_output_schemas_are_stable_and_have_unique_columns():
    for name, columns in prep.SCHEMAS.items():
        assert columns
        assert len(columns) == len(set(columns)), name
    assert "event_date" in prep.SCHEMAS["maude_reports"]
    assert "event_date_initiated" in prep.SCHEMAS["device_recalls"]
