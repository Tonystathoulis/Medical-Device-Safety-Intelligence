# File purpose: retrieves the selected API windows, validates them, and writes the five analysis CSVs.
"""Bounded extraction and normalization for Power BI CSV tables."""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .api_client import APIError, OpenFDAClient
from .config import get_settings
from .validation import parse_date

ROOT = Path(__file__).resolve().parents[2]
START, END = date(2026, 9, 1), date(2026, 10, 13)
PARTITIONS = (
    (date(2026, 9, 1), date(2026, 9, 3)),
    (date(2026, 9, 4), date(2026, 9, 7)),
    (date(2026, 9, 8), date(2026, 9, 14)),
    (date(2026, 9, 15), date(2026, 9, 21)),
    (date(2026, 9, 22), END),
)
SOURCES = {
    "event": {"date": "date_of_event", "id": "mdr_report_key"},
    "recall": {"date": "event_date_initiated", "id": "cfres_id"},
}
SCHEMAS = {
    "maude_reports": ["mdr_report_key", "report_number", "date_of_event_raw", "event_date", "date_received_raw", "date_received", "event_type", "manufacturer_name", "product_problems_raw"],
    "maude_devices": ["mdr_report_key", "device_ordinal", "brand_name", "generic_name", "manufacturer_d_name", "model_number", "device_report_product_code"],
    "maude_product_problems": ["mdr_report_key", "problem_ordinal", "product_problem"],
    "device_recalls": ["cfres_id", "product_res_number", "res_event_number", "event_date_initiated_raw", "event_date_initiated", "event_date_posted_raw", "event_date_posted", "termination_date_raw", "termination_date", "recall_status", "product_code", "product_description", "recalling_firm", "reason_for_recall"],
    "data_quality_summary": ["source", "selected_start", "selected_end", "api_expected_records", "retrieved_records", "unique_records", "duplicate_records", "missing_identifier_records", "missing_date_records_in_selected_rows", "invalid_date_records", "out_of_range_records", "source_missing_date_records_total", "requests", "elapsed_seconds", "status", "notes"],
}


# Step 1: define request, record, page, and runtime safety limits.
@dataclass(frozen=True)
class Limits:
    max_requests: int = 120
    max_records: int = 70000
    page_size: int = 1000
    max_runtime_seconds: int = 300

    @classmethod
    def from_env(cls) -> "Limits":
        return cls(
            max_requests=int(os.getenv("PREP_MAX_REQUESTS", "120")),
            max_records=int(os.getenv("PREP_MAX_RECORDS", "70000")),
            page_size=min(1000, max(1, int(os.getenv("PREP_PAGE_SIZE", "1000")))),
            max_runtime_seconds=int(os.getenv("PREP_MAX_RUNTIME_SECONDS", "300")),
        )


# Step 2: stop output generation when source data fail required checks.
class IncompleteExtraction(RuntimeError):
    pass


# Build a date-bounded query for one API date field.
def query(field: str, start: date, end: date) -> str:
    return f"{field}:[{start:%Y%m%d} TO {end:%Y%m%d}]"


# Convert nested problem values to a consistent list of text entries.
def normalize_problem_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    return [str(value)]


# Preserve identifiers and labels as text, leaving empty values as null.
def _as_text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    return None if value is None or value == "" else str(value)


# Step 3: keep one report row and expand each nested device/problem into keyed child rows.
def normalize_maude(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    reports, devices, problems = [], [], []
    for row in rows:
        report_id = _as_text(row, "mdr_report_key")
        if report_id is None:
            raise IncompleteExtraction("MAUDE row has no mdr_report_key")
        event_raw, received_raw = _as_text(row, "date_of_event"), _as_text(row, "date_received")
        event_date, received_date = parse_date(event_raw), parse_date(received_raw)
        nested_problems = normalize_problem_list(row.get("product_problems"))
        reports.append({
            "mdr_report_key": report_id, "report_number": _as_text(row, "report_number"),
            "date_of_event_raw": event_raw, "event_date": event_date.isoformat() if event_date else None,
            "date_received_raw": received_raw, "date_received": received_date.isoformat() if received_date else None,
            "event_type": _as_text(row, "event_type"), "manufacturer_name": _as_text(row, "manufacturer_d_name"),
            "product_problems_raw": json.dumps(row.get("product_problems"), ensure_ascii=False) if row.get("product_problems") is not None else None,
        })
        nested_devices = row.get("device") or []
        if isinstance(nested_devices, dict):
            nested_devices = [nested_devices]
        for ordinal, device in enumerate(nested_devices, 1):
            if not isinstance(device, dict):
                continue
            devices.append({"mdr_report_key": report_id, "device_ordinal": ordinal,
                            **{field: _as_text(device, field) for field in ("brand_name", "generic_name", "manufacturer_d_name", "model_number", "device_report_product_code")}})
        for ordinal, problem in enumerate(nested_problems, 1):
            problems.append({"mdr_report_key": report_id, "problem_ordinal": ordinal, "product_problem": problem})
    return {"maude_reports": reports, "maude_devices": devices, "maude_product_problems": problems}


# Step 4: flatten recall dates and descriptive fields while retaining raw date text.
def normalize_recalls(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    date_fields = ("event_date_initiated", "event_date_posted", "termination_date")
    for row in rows:
        recall_id = _as_text(row, "cfres_id")
        if recall_id is None:
            raise IncompleteExtraction("Recall row has no cfres_id")
        output = {"cfres_id": recall_id, "product_res_number": _as_text(row, "product_res_number"), "res_event_number": _as_text(row, "res_event_number")}
        for field in date_fields:
            raw = _as_text(row, field)
            parsed = parse_date(raw)
            output[f"{field}_raw"] = raw
            output[field] = parsed.isoformat() if parsed else None
        for field in ("recall_status", "product_code", "product_description", "recalling_firm", "reason_for_recall"):
            output[field] = _as_text(row, field)
        result.append(output)
    return result


# Reject missing keys and remove repeated source identifiers while counting duplicates.
def _unique(rows: list[dict[str, Any]], field: str) -> tuple[list[dict[str, Any]], int]:
    result, seen = [], set()
    for row in rows:
        value = row.get(field)
        if not value:
            raise IncompleteExtraction(f"Missing required identifier: {field}")
        if value in seen:
            continue
        seen.add(value)
        result.append(row)
    return result, len(rows) - len(result)


# Write a schema-ordered temporary CSV and replace the output only when complete.
def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


# Step 5: retrieve and reconcile every bounded partition against its API count and date quality.
def extract_source(client: OpenFDAClient, endpoint: str, limits: Limits, budget: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    spec = SOURCES[endpoint]
    field, id_field = spec["date"], spec["id"]
    started = time.monotonic()
    source_request_start = budget["requests"]
    all_rows: list[dict[str, Any]] = []
    expected_total = 0
    partition_results = []

    def get(params: dict[str, Any], reserve: int = 0):
        if budget["requests"] >= limits.max_requests:
            raise IncompleteExtraction("request budget reached")
        if budget["records"] + reserve > limits.max_records:
            raise IncompleteExtraction("record budget reached")
        if time.monotonic() - budget["started"] >= limits.max_runtime_seconds:
            raise IncompleteExtraction("runtime budget reached")
        budget["requests"] += 1
        try:
            response = client.get(endpoint, params)
        except APIError as exc:
            budget["attempts"] += exc.attempts
            budget["retries"] += max(0, exc.attempts - 1)
            raise
        budget["attempts"] += response.attempts
        budget["retries"] += response.attempts - 1
        results = response.payload.get("results", [])
        rows = results if isinstance(results, list) else []
        budget["records"] += len(rows)
        return response, rows

    # Fetch each contiguous partition, enforcing request, record, time, key, and page limits.
    for part_start, part_end in PARTITIONS:
        search = query(field, part_start, part_end)
        try:
            count_response, _ = get({"search": search, "limit": 1}, 1)
            expected = count_response.payload.get("meta", {}).get("results", {}).get("total")
            if expected is None:
                raise IncompleteExtraction(f"No API count for {endpoint} {part_start}–{part_end}")
            expected = int(expected)
        except APIError as exc:
            if exc.status_code == 404:
                expected = 0
            else:
                raise
        expected_total += expected
        if expected > 25000:
            raise IncompleteExtraction(f"Partition {part_start}–{part_end} exceeds the 25,000 skip ceiling")
        received: list[dict[str, Any]] = []
        offset = 0
        while offset < expected:
            page_limit = min(limits.page_size, expected - offset)
            _, page_rows = get({"search": search, "limit": page_limit, "skip": offset}, page_limit)
            if not page_rows:
                break
            received.extend(page_rows)
            offset += len(page_rows)
        ids = [str(row.get(id_field, "")) for row in received]
        if len(received) != expected or any(not x for x in ids) or len(ids) != len(set(ids)):
            raise IncompleteExtraction(f"{endpoint} partition {part_start}–{part_end} did not reconcile: expected={expected}, retrieved={len(received)}, unique={len(set(ids))}")
        all_rows.extend(received)
        partition_results.append({"start": part_start.isoformat(), "end": part_end.isoformat(), "expected": expected, "retrieved": len(received), "unique": len(set(ids)), "complete": True})

    # Confirm partition counts match the whole window and measure undated source records separately.
    parent_search = query(field, START, END)
    parent, _ = get({"search": parent_search, "limit": 1}, 1)
    parent_expected = int(parent.payload.get("meta", {}).get("results", {}).get("total", -1))
    if parent_expected != expected_total:
        raise IncompleteExtraction(f"partition totals {expected_total} do not equal whole-window total {parent_expected} for {endpoint}")

    # This independent count makes the unavoidable exclusion of undated records visible.
    try:
        missing_response, _ = get({"search": f"_missing_:{field}", "limit": 1}, 1)
        missing_date_total = int(missing_response.payload.get("meta", {}).get("results", {}).get("total", 0))
    except APIError as exc:
        if exc.status_code != 404:
            raise
        missing_date_total = 0
    missing_dates = sum(1 for row in all_rows if not str(row.get(field) or "").strip())
    invalid_dates = sum(1 for row in all_rows if str(row.get(field) or "").strip() and parse_date(row.get(field)) is None)
    outside = sum(1 for row in all_rows if parse_date(row.get(field)) is not None and not START <= parse_date(row.get(field)) <= END)
    if missing_dates or invalid_dates or outside:
        raise IncompleteExtraction(f"Unexpected date quality in filtered {endpoint} rows: missing={missing_dates}, invalid={invalid_dates}, outside={outside}")
    return all_rows, {"source": endpoint, "expected": expected_total, "parent_count": parent_expected,
                      "partitions": partition_results, "source_missing_date_records_total": missing_date_total,
                      "missing_dates_in_selected_rows": missing_dates, "invalid_dates": invalid_dates,
                      "outside_range": outside, "requests": budget["requests"] - source_request_start,
                      "elapsed_seconds": round(time.monotonic() - started, 2), "complete": True}


# Step 6: fetch both sources, normalize them, validate output keys, and atomically save results.
def run() -> int:
    limits = Limits.from_env()
    settings = get_settings()
    client = OpenFDAClient(settings.api_key, settings.timeout_seconds, settings.max_retries)
    budget = {"started": time.monotonic(), "requests": 0, "attempts": 0, "retries": 0, "records": 0}
    try:
        # Retrieve and validate both source sets before writing any processed tables.
        event_rows, event_quality = extract_source(client, "event", limits, budget)
        recall_rows, recall_quality = extract_source(client, "recall", limits, budget)
        normalized = normalize_maude(event_rows)
        normalized["maude_reports"], event_duplicates = _unique(normalized["maude_reports"], "mdr_report_key")
        normalized["device_recalls"] = normalize_recalls(recall_rows)
        normalized["device_recalls"], recall_duplicates = _unique(normalized["device_recalls"], "cfres_id")
        if event_duplicates or recall_duplicates:
            raise IncompleteExtraction(f"Duplicate IDs detected: MAUDE={event_duplicates}, recalls={recall_duplicates}")
        # Save the four analysis tables and then the compact quality-summary table.
        for key in ("maude_reports", "maude_devices", "maude_product_problems", "device_recalls"):
            _write_csv(ROOT / "data" / "processed" / f"{key}.csv", SCHEMAS[key], normalized[key])
        quality_rows = []
        for source, quality, dupes in (("MAUDE", event_quality, event_duplicates), ("recall", recall_quality, recall_duplicates)):
            quality_rows.append({"source": source, "selected_start": START.isoformat(), "selected_end": END.isoformat(),
                "api_expected_records": quality["expected"], "retrieved_records": quality["expected"],
                "unique_records": quality["expected"] - dupes, "duplicate_records": dupes,
                "missing_identifier_records": 0, "missing_date_records_in_selected_rows": quality["missing_dates_in_selected_rows"],
                "invalid_date_records": quality["invalid_dates"], "out_of_range_records": quality["outside_range"],
                "source_missing_date_records_total": quality["source_missing_date_records_total"],
                "requests": quality["requests"], "elapsed_seconds": quality["elapsed_seconds"],
                "status": "complete for date-filtered scope", "notes": "Undated source records are excluded by date filtering; count shown for transparency."})
        _write_csv(ROOT / "data" / "processed" / "data_quality_summary.csv", SCHEMAS["data_quality_summary"], quality_rows)
        manifest = {"run_utc": datetime.now(timezone.utc).isoformat(), "window": {"start": START.isoformat(), "end": END.isoformat()},
                    "limits": asdict(limits), "request_totals": {k: v for k, v in budget.items() if k != "started"},
                    "elapsed_seconds": round(time.monotonic() - budget["started"], 2),
                    "maude": event_quality, "recalls": recall_quality, "duplicates": {"maude": event_duplicates, "recalls": recall_duplicates},
                    "status": "complete for date-filtered scope"}
        report_path = ROOT / "reports" / "phase3_data_quality.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temp = report_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temp.replace(report_path)
        print(json.dumps({"processed_dir": "data/processed", "request_totals": manifest["request_totals"],
                          "elapsed_seconds": manifest["elapsed_seconds"], "maude_records": len(event_rows),
                          "recall_records": len(recall_rows), "status": manifest["status"]}, indent=2))
        return 0
    except Exception as exc:
        failure = {"run_utc": datetime.now(timezone.utc).isoformat(), "status": "incomplete; no complete handoff", "error": f"{type(exc).__name__}: {exc}",
                   "request_totals": {k: v for k, v in budget.items() if k != "started"}}
        report_path = ROOT / "reports" / "phase3_data_quality.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(failure, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    run()
