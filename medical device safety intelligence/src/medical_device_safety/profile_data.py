# File purpose: summarizes identifiers and product-code coverage in a saved inspection artifact.
"""Offline profiling of the compact inspection artifact."""
import json
from pathlib import Path


# Step 1: load the artifact and calculate small endpoint-level profile counts.
def profile_artifact(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    output = {}
    for endpoint, result in payload.get("results", {}).items():
        rows = result.get("filtered_records", [])
        if endpoint == "event":
            codes = {d.get("device_report_product_code") for row in rows for d in (row.get("device") or []) if d.get("device_report_product_code")}
            id_field = "mdr_report_key"
        else:
            codes = {row.get("product_code") for row in rows if row.get("product_code")}
            id_field = "cfres_id"
        output[endpoint] = {"rows": len(rows), "distinct_product_codes": len(codes),
                            "sampled_identifiers": len({str(r.get(id_field)) for r in rows if r.get(id_field)})}
    return output


if __name__ == "__main__":
    path = Path("reports/inspection_summary.json")
    print(json.dumps(profile_artifact(path), indent=2))
