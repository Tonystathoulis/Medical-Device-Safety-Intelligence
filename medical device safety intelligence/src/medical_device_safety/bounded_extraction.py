# File purpose: tests bounded quarterly retrieval, paging, completeness checks, and restartable manifests.
"""Bounded, restartable extraction experiment for two fixed calendar quarters."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .api_client import APIError, OpenFDAClient
from .config import get_settings
from .validation import parse_date

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = ((date(2019, 1, 1), date(2019, 3, 31)), (date(2024, 1, 1), date(2024, 3, 31)))
FIELDS = {
    "event": ("date_of_event", "mdr_report_key"),
    "recall": ("event_date_initiated", "cfres_id"),
}


# Step 1: define safety caps and read optional overrides from the environment.
@dataclass(frozen=True)
class Limits:
    max_requests: int = 120
    max_records: int = 12000
    max_records_per_window: int = 4000
    page_size: int = 100
    max_runtime_seconds: int = 240

    @classmethod
    def from_env(cls) -> "Limits":
        return cls(
            max_requests=max(1, int(os.getenv("EXTRACTION_MAX_REQUESTS", "120"))),
            max_records=max(1, int(os.getenv("EXTRACTION_MAX_RECORDS", "12000"))),
            max_records_per_window=max(1, int(os.getenv("EXTRACTION_MAX_RECORDS_PER_WINDOW", "4000"))),
            page_size=min(1000, max(1, int(os.getenv("EXTRACTION_PAGE_SIZE", "100")))),
            max_runtime_seconds=max(1, int(os.getenv("EXTRACTION_MAX_RUNTIME_SECONDS", "240"))),
        )


# Step 2: stop cleanly when an extraction exceeds one of its configured caps.
class SafetyLimit(RuntimeError):
    pass


# Step 3: count requests, retries, elapsed time, and returned records across the run.
class Budget:
    def __init__(self, limits: Limits, max_retries: int):
        self.limits = limits
        self.max_retries = max_retries
        self.started = time.monotonic()
        self.requests = 0
        self.attempts = 0
        self.retries = 0
        self.records = 0
        self.events: list[dict[str, Any]] = []

    # Check remaining time, request attempts, and record capacity before each call.
    def check(self, reserve_records: int = 0) -> None:
        if time.monotonic() - self.started >= self.limits.max_runtime_seconds:
            raise SafetyLimit("maximum execution time reached")
        if self.attempts + self.max_retries + 1 > self.limits.max_requests:
            raise SafetyLimit("maximum HTTP request-attempt budget reached")
        if self.records + reserve_records > self.limits.max_records:
            raise SafetyLimit("maximum returned-record budget reached")

    # Send a standard request and save sanitized outcome details in the run log.
    def get(self, client: OpenFDAClient, endpoint: str, params: dict[str, Any],
            reserve_records: int = 0):
        self.check(reserve_records)
        self.requests += 1
        try:
            response = client.get(endpoint, params)
        except APIError as exc:
            self.attempts += exc.attempts
            self.retries += max(0, exc.attempts - 1)
            self.events.append({"endpoint": endpoint, "params": {k: v for k, v in params.items() if k != "api_key"},
                                "status": exc.status_code, "attempts": exc.attempts, "error": str(exc)[:250]})
            raise
        self.attempts += response.attempts
        self.retries += response.attempts - 1
        rows = response.payload.get("results", [])
        row_count = len(rows) if isinstance(rows, list) else 0
        self.records += row_count
        self.events.append({"endpoint": endpoint, "params": response.params,
                            "status": response.status_code, "elapsed_seconds": round(response.elapsed_seconds, 3),
                            "attempts": response.attempts, "rows": row_count})
        return response

    # Follow a server-provided continuation URL under the same safety budget.
    def get_url(self, client: OpenFDAClient, url: str, reserve_records: int = 0):
        self.check(reserve_records)
        self.requests += 1
        try:
            response = client.get_url(url)
        except APIError as exc:
            self.attempts += exc.attempts
            self.retries += max(0, exc.attempts - 1)
            self.events.append({"endpoint": "continuation", "params": "API Link header",
                                "status": exc.status_code, "attempts": exc.attempts,
                                "error": str(exc)[:250]})
            raise
        self.attempts += response.attempts
        self.retries += response.attempts - 1
        rows = response.payload.get("results", [])
        row_count = len(rows) if isinstance(rows, list) else 0
        self.records += row_count
        self.events.append({"endpoint": "continuation", "params": "API Link header",
                            "status": response.status_code, "elapsed_seconds": round(response.elapsed_seconds, 3),
                            "attempts": response.attempts, "rows": row_count})
        return response


# Step 4: extract only the API's own next-page URL from its Link header.
def _next_link(link_header: str) -> str | None:
    """Return the API-provided rel=next URL verbatim; never synthesize cursors."""
    match = re.search(r'<([^>]+)>\s*;\s*rel="?next"?', link_header, re.IGNORECASE)
    return match.group(1) if match else None


# Build an inclusive openFDA date query for one field and bounded period.
def _query(field: str, start: date, end: date) -> str:
    return f"{field}:[{start:%Y%m%d} TO {end:%Y%m%d}]"


# Count missing, unparseable, and out-of-window dates before accepting a page set.
def _date_status(rows: list[dict[str, Any]], field: str,
                 start: date, end: date) -> dict[str, int]:
    missing = invalid = outside = 0
    for row in rows:
        raw = row.get(field)
        parsed = parse_date(raw)
        if raw is None or str(raw).strip() == "":
            missing += 1
        elif parsed is None:
            invalid += 1
        elif not start <= parsed <= end:
            outside += 1
    return {"missing": missing, "invalid": invalid, "outside_window": outside}


# Read identifiers as strings so paging and reconciliation preserve exact source keys.
def _id_list(rows: list[dict[str, Any]], id_field: str) -> list[str | None]:
    return [str(row[id_field]) if row.get(id_field) not in (None, "") else None for row in rows]


# Compare API totals, identifier quality, and date quality to decide if a window is complete.
def reconcile(expected: int | None, ids: list[str | None], date_counts: dict[str, int]) -> dict[str, Any]:
    present = [value for value in ids if value is not None]
    duplicates = len(present) - len(set(present))
    complete = (expected is not None and len(ids) == expected and duplicates == 0
                and len(present) == len(ids) and not any(date_counts.values()))
    return {"retrieved_count": len(ids), "unique_identifier_count": len(set(present)),
            "missing_identifier_count": len(ids) - len(present),
            "duplicate_identifier_count": duplicates, "complete": complete}


# Write a temporary manifest and replace the prior file only after JSON is complete.
def write_atomic_json(payload: dict[str, Any], output_path: Path) -> None:
    """Replace the manifest atomically so interruption cannot leave partial JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(output_path)


# Step 5: inspect counts, page behavior, dates, and identifiers for one source window.
def _window_result(client: OpenFDAClient, endpoint: str, start: date, end: date,
                   limits: Limits, budget: Budget) -> dict[str, Any]:
    request_start = len(budget.events)
    date_field, id_field = FIELDS[endpoint]
    query = _query(date_field, start, end)
    # Read the API's expected count before retrieving or probing records.
    params = {"search": query, "limit": 1}
    t0 = time.monotonic()
    count_response = budget.get(client, endpoint, params, reserve_records=1)
    count_meta = count_response.payload.get("meta", {}).get("results", {})
    expected = count_meta.get("total")
    expected = int(expected) if expected is not None else None

    # Probe the first and last day so an empty edge is visible in the report.
    boundaries = {}
    for edge_name, edge in (("start", start), ("end", end)):
        day_query = _query(date_field, edge, edge)
        try:
            response = budget.get(client, endpoint, {"search": day_query, "limit": 10}, reserve_records=10)
            rows = response.payload.get("results", [])
            values = [parse_date(row.get(date_field)) for row in rows]
            boundaries[edge_name] = {
                "params": {"search": day_query, "limit": 10},
                "status": response.status_code,
                "rows": len(rows),
                "all_returned_dates_equal_boundary": bool(rows) and all(v == edge for v in values),
            }
        except APIError as exc:
            if exc.status_code != 404:
                raise
            boundaries[edge_name] = {"params": {"search": day_query, "limit": 10},
                                     "status": 404, "rows": 0,
                                     "all_returned_dates_equal_boundary": False,
                                     "meaning": "API returned no matching boundary-date records"}

    # Fetch the first pages and compare ordinary skip with any supplied cursor link.
    query_params = {"search": query, "limit": min(limits.page_size, max(1, expected or 1)),
                    "sort": f"{date_field}:asc"}
    first = budget.get(client, endpoint, query_params,
                       reserve_records=query_params["limit"])
    first_rows = first.payload.get("results", [])
    extracted = list(first_rows)
    continuation = _next_link(first.headers.get("Link", ""))
    link_probe = {"supported": continuation is not None, "http_status": None,
                  "rows": 0, "matches_skip_page": None, "error": None}

    # Test ordinary skip against the API-returned search-after link on the next page.
    skip_second = None
    if expected and expected > len(first_rows):
        second_params = {"search": query, "limit": limits.page_size,
                         "skip": len(first_rows), "sort": f"{date_field}:asc"}
        second = budget.get(client, endpoint, second_params,
                            reserve_records=second_params["limit"])
        skip_second = second.payload.get("results", [])
        extracted.extend(skip_second)
        if continuation:
            try:
                linked = budget.get_url(client, continuation, reserve_records=limits.page_size)
                linked_rows = linked.payload.get("results", [])
                link_probe.update({"http_status": linked.status_code, "rows": len(linked_rows),
                                   "matches_skip_page": _id_list(linked_rows, id_field) == _id_list(skip_second, id_field)})
            except (APIError, SafetyLimit) as exc:
                link_probe["error"] = str(exc)
    elif continuation:
        # Still verify that this endpoint honors the generated continuation URL.
        try:
            linked = budget.get_url(client, continuation, reserve_records=limits.page_size)
            linked_rows = linked.payload.get("results", [])
            link_probe.update({"http_status": linked.status_code, "rows": len(linked_rows)})
        except (APIError, SafetyLimit) as exc:
            link_probe["error"] = str(exc)

    # Continue ordinary skip only when the quarter is within the documented ceiling
    # and the configurable record budget. Stop at the cap and mark the result partial.
    window_cap = min(limits.max_records_per_window, max(0, limits.max_records - budget.records + len(extracted)))
    can_complete = expected is not None and expected <= 26000 and expected <= window_cap
    completion_reason = None
    if not can_complete:
        completion_reason = ("API count unavailable" if expected is None else
                             "partition exceeds ordinary skip ceiling or configured record budget")
    while can_complete and len(extracted) < expected:
        budget.check(reserve_records=min(limits.page_size, expected - len(extracted)))
        skip = len(extracted)
        page_params = {"search": query, "limit": min(limits.page_size, expected - skip),
                       "skip": skip, "sort": f"{date_field}:asc"}
        page = budget.get(client, endpoint, page_params,
                          reserve_records=page_params["limit"])
        page_rows = page.payload.get("results", [])
        if not page_rows:
            break
        extracted.extend(page_rows)

    # Reconcile the retrieved sample against the expected count and data quality.
    ids = _id_list(extracted, id_field)
    date_counts = _date_status(extracted, date_field, start, end)
    reconciliation = reconcile(expected, ids, date_counts)
    outcome = reconciliation["complete"]
    return {
        "endpoint": endpoint,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "inclusive": True},
        "date_field": date_field,
        "identifier_field": id_field,
        "count_query_params": params,
        "retrieval_query_params": query_params,
        "api_expected_count": expected,
        "retrieved_count": reconciliation["retrieved_count"],
        "unique_identifier_count": reconciliation["unique_identifier_count"],
        "missing_identifier_count": reconciliation["missing_identifier_count"],
        "duplicate_identifier_count": reconciliation["duplicate_identifier_count"],
        "date_quality": date_counts,
        "boundary_probes": boundaries,
        "pagination": {"ordinary_skip_page_rows": len(skip_second or []),
                       "search_after_link": link_probe,
                       "ordinary_skip_documented_ceiling": 26000},
        "complete_and_reconciled": outcome,
        "completion_reason": "count, unique IDs, and date checks reconciled" if outcome else completion_reason or "retrieved rows did not reconcile to API count or quality checks",
        "status": "complete" if outcome else "incomplete_or_unverified",
        "elapsed_seconds": round(time.monotonic() - t0, 3),
        "requests": budget.events[request_start:],
        "record_identifiers": ids,
    }


# Step 6: inspect both endpoints and save a sanitized manifest for review.
def run() -> int:
    settings = get_settings()
    limits = Limits.from_env()
    budget = Budget(limits, settings.max_retries)
    client = OpenFDAClient(settings.api_key, settings.timeout_seconds, settings.max_retries)
    results = []
    errors = []
    for endpoint in ("event", "recall"):
        for start, end in WINDOWS:
            try:
                results.append(_window_result(client, endpoint, start, end, limits, budget))
            except (APIError, SafetyLimit, ValueError) as exc:
                errors.append({"endpoint": endpoint, "start": start.isoformat(),
                               "end": end.isoformat(), "error": f"{type(exc).__name__}: {exc}"})
                # Continue to other windows only if resource limits still permit it.
                if isinstance(exc, SafetyLimit) and ("request" in str(exc) or "execution" in str(exc)):
                    break
    payload = {
        "extraction_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": "inclusive date filter; sorted limit/skip; compare generated search-after Link where present",
        "limits": asdict(limits),
        "totals": {"logical_requests": budget.requests, "http_attempts": budget.attempts,
                   "retries": budget.retries, "returned_records_including_probes": budget.records,
                   "elapsed_seconds": round(time.monotonic() - budget.started, 3)},
        "windows": results,
        "errors": errors,
    }
    output_path = ROOT / "reports" / "extraction_experiment.json"
    write_atomic_json(payload, output_path)
    print(json.dumps({"saved": str(output_path.relative_to(ROOT)),
                      "totals": payload["totals"],
                      "windows": [{k: w[k] for k in ("endpoint", "window", "api_expected_count", "retrieved_count", "unique_identifier_count", "duplicate_identifier_count", "status", "pagination")}
                                  for w in results], "errors": errors}, indent=2))
    return 0 if len(results) == 4 and not errors else 1


if __name__ == "__main__":
    sys.exit(run())
