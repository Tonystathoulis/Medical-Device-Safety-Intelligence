# File purpose: probes openFDA schemas and paging with small samples, then saves sanitized evidence.
"""Run bounded live source inspections and save compact, sanitized summaries."""
from __future__ import annotations
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any
from .api_client import APIError, OpenFDAClient
from .config import get_settings
from .validation import parse_date

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
EVENT_FIELDS = ["mdr_report_key", "report_number", "date_of_event", "date_received", "event_type", "device", "product_problems"]
RECALL_FIELDS = ["cfres_id", "product_res_number", "res_event_number", "event_date_initiated", "product_code", "product_description", "recalling_firm", "reason_for_recall", "recall_status"]


# Step 1: describe field types without printing the full source record.
def field_types(record: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for name, value in record.items():
        if isinstance(value, list):
            result[name] = {"type": "array", "item_types": sorted({type(x).__name__ for x in value})}
        elif isinstance(value, dict):
            result[name] = {"type": "object", "fields": {k: type(v).__name__ for k, v in value.items()}}
        else:
            result[name] = type(value).__name__
    return result


# Step 2: retain analytical fields and discard reporter, contact, and address details.
def sanitize_record(record: dict[str, Any], endpoint: str) -> dict[str, Any]:
    """Keep only analytical fields; omit reporter and contact/address details."""
    if endpoint == "event":
        allowed = {"mdr_report_key", "report_number", "date_of_event", "date_received", "event_type",
                   "manufacturer_name", "product_problems", "device"}
        device_allowed = {"device_event_key", "device_sequence_number", "brand_name", "generic_name",
                          "manufacturer_d_name", "model_number", "device_report_product_code", "openfda"}
        clean = {k: v for k, v in record.items() if k in allowed}
        if isinstance(clean.get("device"), list):
            clean["device"] = [{k: v for k, v in d.items() if k in device_allowed}
                               for d in clean["device"] if isinstance(d, dict)]
        return clean
    allowed = {"cfres_id", "product_res_number", "res_event_number", "event_date_initiated",
               "event_date_created", "event_date_posted", "event_date_terminated", "recall_status",
               "product_code", "product_description", "recalling_firm", "reason_for_recall",
               "root_cause_description", "action", "k_numbers", "openfda"}
    return {k: v for k, v in record.items() if k in allowed}


# Step 3: sample one endpoint, check its date filter and paging, and summarize field coverage.
def inspect_endpoint(client: OpenFDAClient, endpoint: str, start: date, end: date,
                     sample_size: int) -> dict[str, Any]:
    fields = EVENT_FIELDS if endpoint == "event" else RECALL_FIELDS
    # First request is intentionally unfiltered and minimal to observe the live schema.
    minimal = client.get(endpoint, {"limit": 1})
    recs = minimal.payload.get("results", [])
    if not recs:
        raise APIError(f"{endpoint}: response did not contain a representative result")
    date_field = "date_of_event" if endpoint == "event" else "event_date_initiated"
    unfiltered = client.get(endpoint, {"limit": min(sample_size, 100)})
    unfiltered_records = unfiltered.payload.get("results", [])
    search = f"{date_field}:[{start:%Y%m%d} TO {end:%Y%m%d}]"
    filtered = client.get(endpoint, {"search": search, "limit": min(sample_size, 100)})
    records = filtered.payload.get("results", [])
    # Bounded page-continuation probe only; this is not a historical extraction.
    page_two = client.get(endpoint, {"search": search, "limit": min(sample_size, 100), "skip": min(sample_size, 100)})
    page_two_records = page_two.payload.get("results", [])
    try:
        count_resp = client.get(endpoint, {"search": search, "limit": 1})
        count = count_resp.payload.get("meta", {}).get("results", {}).get("total")
    except APIError as exc:
        count = None
        LOG.warning("Count query unavailable for %s: %s", endpoint, exc)
    # Bundle request metadata, counts, field profiles, and sanitized sample records.
    return {
        "endpoint": endpoint,
        "requests": [
            {"params": minimal.params, "status": minimal.status_code, "elapsed_seconds": round(minimal.elapsed_seconds, 3),
             "row_count": len(recs), "meta": minimal.payload.get("meta")},
            {"params": unfiltered.params, "status": unfiltered.status_code, "elapsed_seconds": round(unfiltered.elapsed_seconds, 3),
             "row_count": len(unfiltered_records), "meta": unfiltered.payload.get("meta")},
            {"params": filtered.params, "status": filtered.status_code, "elapsed_seconds": round(filtered.elapsed_seconds, 3),
             "row_count": len(records), "meta": filtered.payload.get("meta")},
            {"params": page_two.params, "status": page_two.status_code, "elapsed_seconds": round(page_two.elapsed_seconds, 3),
             "row_count": len(page_two_records), "meta": page_two.payload.get("meta")},
        ],
        "date_filter": search,
        "count_estimate": count,
        "pagination_probe": {
            "second_page_rows": len(page_two_records),
            "duplicate_identifiers_between_pages": len(set(str(r.get("mdr_report_key" if endpoint == "event" else "cfres_id")) for r in records) & set(str(r.get("mdr_report_key" if endpoint == "event" else "cfres_id")) for r in page_two_records)),
        },
        "sample_profile": {
            "sample_rows": len(records),
            "fields": {f: {"present": sum(bool(r.get(f)) for r in records), "missing": sum(not bool(r.get(f)) for r in records)} for f in fields},
        },
        "unfiltered_sample_profile": {
            "sample_rows": len(unfiltered_records),
            "date_field": date_field,
            "date_present": sum(bool(r.get(date_field)) for r in unfiltered_records),
            "date_missing_or_empty": sum(not bool(r.get(date_field)) for r in unfiltered_records),
            "date_invalid_among_present": sum(bool(r.get(date_field)) and parse_date(r.get(date_field)) is None for r in unfiltered_records),
            "identifier_fields": ("mdr_report_key", "report_number") if endpoint == "event" else ("cfres_id", "product_res_number", "res_event_number"),
            "identifier_missing_or_empty": {field: sum(not bool(r.get(field)) for r in unfiltered_records)
                                             for field in (("mdr_report_key", "report_number") if endpoint == "event" else ("cfres_id", "product_res_number", "res_event_number"))},
            "maude_device_entries": sum(len(r.get("device") or []) for r in unfiltered_records) if endpoint == "event" else None,
            "maude_device_field_missing_or_empty": ({field: sum(not bool(d.get(field)) for r in unfiltered_records for d in (r.get("device") or []))
                                                      for field in ("brand_name", "generic_name", "manufacturer_d_name", "model_number", "device_report_product_code")} if endpoint == "event" else None),
        },
        "representative_field_types": field_types(recs[0]),
        "representative_nested_types": {
            "device": [field_types(x) for x in recs[0].get("device", [])] if endpoint == "event" else None,
            "product_problems": type(recs[0].get("product_problems")).__name__ if endpoint == "event" else None,
            "openfda": field_types(recs[0].get("openfda", {})) if endpoint == "recall" else None,
        },
        "representative_record": recs[0],
        "filtered_records": records,
    }


# Step 4: inspect both APIs, test exact identifier overlap, and write the summary artifact.
def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    client = OpenFDAClient(settings.api_key, settings.timeout_seconds, settings.max_retries)
    results = {}
    errors = {}
    for endpoint in ("event", "recall"):
        try:
            results[endpoint] = inspect_endpoint(client, endpoint, settings.start_date, settings.end_date, settings.sample_size)
        except Exception as exc:
            errors[endpoint] = f"{type(exc).__name__}: {exc}"
            LOG.error("Live inspection failed for %s: %s", endpoint, exc)
    # Compare identifier fields only when both endpoint samples were retrieved.
    linkage = {"status": "not_tested"}
    if "event" in results and "recall" in results:
        event_ids = {str(row.get("removal_correction_number")).strip() for row in results["event"].get("filtered_records", [])
                     if row.get("removal_correction_number") and str(row.get("removal_correction_number")).strip()}
        recall_fields = ("cfres_id", "product_res_number", "res_event_number")
        matches = {}
        for field in recall_fields:
            recall_ids = {str(row.get(field)).strip() for row in results["recall"].get("filtered_records", [])
                          if row.get(field) and str(row.get(field)).strip()}
            matches[field] = sorted(event_ids & recall_ids)
        linkage = {"maude_nonempty_removal_correction_numbers": len(event_ids),
                   "recall_identifier_fields_tested": list(recall_fields),
                   "exact_string_matches_by_recall_field": {field: {"count": len(values), "values": values[:20]} for field, values in matches.items()},
                   "note": "Sample string equality only; no semantic linkage validation."}
    out = ROOT / "reports" / "inspection_summary.json"
    out.parent.mkdir(exist_ok=True)
    # Save only source fields needed for analysis; drop reporter/contact/address information.
    for result in results.values():
        endpoint = result["endpoint"]
        result["representative_record"] = sanitize_record(result.get("representative_record", {}), endpoint)
        result["filtered_records"] = [sanitize_record(record, endpoint) for record in result.get("filtered_records", [])]
    out.write_text(json.dumps({"results": results, "linkage_probe": linkage, "errors": errors}, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"saved": str(out.relative_to(ROOT)), "results": {k: {"requests": v["requests"], "count_estimate": v["count_estimate"], "sample_profile": v["sample_profile"], "unfiltered_sample_profile": v["unfiltered_sample_profile"], "pagination_probe": v["pagination_probe"]} for k,v in results.items()}, "linkage_probe": linkage, "errors": errors}, indent=2, default=str))
    return 0 if len(results) == 2 else 1


if __name__ == "__main__":
    sys.exit(run())
