# Power BI guide

## Report and dataset

The supplied [`BI VIsualization.pbix`](../BI%20VIsualization.pbix) contains three pages: **Safety Overview**, **Detailed Analysis**, and **Recall Details**. The report definition was inspected, but the PBIX has not been opened and refreshed in Power BI Desktop during this work. Its local data-source paths and displayed values therefore still need checking.

The companion CSV snapshot covers **September 1–October 13, 2026**. It has 62,533 MAUDE reports, 62,533 device entries, 72,473 product-problem entries, and 11 recall records. It is a short bounded slice, not a long-term trend dataset. The table grains and primary keys are listed below.

| Table | One row represents | Key fields |
|---|---|---|
| `maude_reports` | One MAUDE report | `mdr_report_key` |
| `maude_devices` | One device entry in a report | `mdr_report_key`, `device_ordinal` |
| `maude_product_problems` | One reported problem in a report | `mdr_report_key`, `problem_ordinal` |
| `device_recalls` | One recall record | `cfres_id` |
| `data_quality_summary` | One retrieval summary per source | `source` (`MAUDE`, `recall`) |

All five schemas matched the declared output schemas and the source totals reconciled across partitions. The identifiers are unique, there are no duplicate child keys or orphan report keys, and the selected rows have no missing, invalid, or out-of-range parsed dates. Actual MAUDE event dates run from September 1 through October 5; October 13 is the fixed query bound. The offline test suite passed 27 tests.

## Model

Create these two active relationships, with single-direction filtering from the report table to the child table:

| One side | Many side | Cardinality |
|---|---|---|
| `maude_reports[mdr_report_key]` | `maude_devices[mdr_report_key]` | One-to-many |
| `maude_reports[mdr_report_key]` | `maude_product_problems[mdr_report_key]` | One-to-many |

Keep `device_recalls` and `data_quality_summary` disconnected. Do not join the two child tables to each other; that can multiply rows. There is no validated MAUDE-to-recall identifier. Comparing `device_report_product_code` with `product_code` is category-level only and does not associate an individual report with a recall.

Use `maude_reports` for report totals. `maude_devices` and `maude_product_problems` count entries, not unique incidents. For report timing use `event_date`; `date_received` is FDA receipt timing. For recalls use `event_date_initiated`; `event_date_posted` and `termination_date` describe later lifecycle dates. Keep raw date columns as text for source review.

## Suggested measures

The following DAX uses the CSV column names above. **These measures are proposals and have not been tested in Power BI Desktop.**

```DAX
MAUDE Report Count =
DISTINCTCOUNT(maude_reports[mdr_report_key])

Device Entry Count =
COUNTROWS(maude_devices)

Product-Problem Entry Count =
COUNTROWS(maude_product_problems)

Recall Record Count =
DISTINCTCOUNT(device_recalls[cfres_id])
```

Use `TREATAS` to count distinct reports for a category selected in a child table while retaining single-direction relationships:

```DAX
Reports with Selected Product Problem =
VAR SelectedReportKeys = VALUES(maude_product_problems[mdr_report_key])
RETURN
    CALCULATE(
        [MAUDE Report Count],
        TREATAS(SelectedReportKeys, maude_reports[mdr_report_key])
    )

Reports with Selected Device Entry =
VAR SelectedReportKeys = VALUES(maude_devices[mdr_report_key])
RETURN
    CALCULATE(
        [MAUDE Report Count],
        TREATAS(SelectedReportKeys, maude_reports[mdr_report_key])
    )
```

```DAX
Event-Type Share of Reports =
DIVIDE(
    [MAUDE Report Count],
    CALCULATE(
        [MAUDE Report Count],
        REMOVEFILTERS(maude_reports[event_type])
    )
)
```

Format the share as a percentage. It retains other filters, including date filters.

Filtered date coverage reflects the rows currently visible under filters; fixed extraction bounds come from the quality table. Keep those meanings distinct:

```DAX
MAUDE Event Coverage Start = MIN(maude_reports[event_date])
MAUDE Event Coverage End = MAX(maude_reports[event_date])
Recall Initiation Coverage Start = MIN(device_recalls[event_date_initiated])
Recall Initiation Coverage End = MAX(device_recalls[event_date_initiated])

MAUDE Extraction Start Bound =
CALCULATE(
    MIN(data_quality_summary[selected_start]),
    data_quality_summary[source] = "MAUDE"
)

MAUDE Extraction End Bound =
CALCULATE(
    MAX(data_quality_summary[selected_end]),
    data_quality_summary[source] = "MAUDE"
)
```

The fixed MAUDE bounds are September 1 and October 13, 2026. Actual MAUDE event dates in the selected records run from September 1 through October 5, 2026; a bound is not proof that records exist on every date through October 13.

The source-wide missing-event-date total is 3,699,144. These records cannot be assigned to the selected event-date window:

```DAX
MAUDE Source-Wide Missing Event-Date Records =
CALCULATE(
    MAX(data_quality_summary[source_missing_date_records_total]),
    data_quality_summary[source] = "MAUDE"
)
```

Do not treat zero missing dates in the extracted rows as source-wide completeness. The MAUDE extraction filters by event date, so records without that date may be excluded before the selected extract is built. The selected extract's date-quality checks are separate: it has zero missing, invalid, and out-of-range event dates.

## Proposed report pages

The existing PBIX has three pages listed above. The following four-page layout is a proposed design for continued development; it does not describe a verified layout of the PBIX.

1. **Safety Intelligence Overview:** distinct MAUDE report count, device/problem entry counts, recall count, event-type composition, and a short-window event-date view. Display the date scope prominently; do not suggest a long-term trend.
2. **Event and Device Analysis:** reported product-problem categories and device product codes, with report-level drill-through. Distinguish child entries from distinct reports.
3. **Recall Intelligence:** a small, searchable table of the 11 recalls with status, initiation date, product code, firm, and description. Avoid broad comparisons or trend claims from this small count.
4. **Data Quality and Methodology:** source row counts, fixed bounds, actual date coverage, reconciliation and completeness indicators, plus the source-wide missing-date caveat.

## Open, refresh, and check the report

1. Open the PBIX in Power BI Desktop. If its source paths are unavailable, use **File > Options and settings > Data source settings > Change Source** or edit the Power Query source steps to point to `<ProjectRoot>/data/processed/`.
2. Confirm the five table names match the CSV filenames. Set identifiers, product codes, labels, and raw date fields to **Text**; set parsed date fields to **Date**; set counts and ordinals to **Whole number**.
3. Create the two one-to-many report-to-child relationships above. Remove or avoid automatic relationships from recalls or quality metadata.
4. Refresh the data and check the row counts against the table above before interpreting visuals. Review the data window and caveats on the report pages.
5. Validate all measures and visuals in Desktop before presenting or publishing. Do not imply causality, risk rates, or comparative safety: suitable exposure denominators are not available.

MAUDE reports represent suspected device-associated events, not verified causal events or device failures. Recall records describe a separate process and are not linked to specific MAUDE reports.
