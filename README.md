# Medical Device Safety Intelligence

An exploratory project using openFDA reports of suspected medical-device events (MAUDE) and device recalls. It prepares a bounded dataset, validates it, explores it in Python, and presents it in Power BI.

## Dataset

The included snapshot covers **September 1–October 13, 2026**:

| Table | Rows |
|---|---:|
| MAUDE reports | 62,533 |
| Device entries | 62,533 |
| Product-problem entries | 72,473 |
| Recall records | 11 |

The five CSVs are in [`data/processed/`](data/processed/) and total about 18 MB. The data come from the [openFDA device event API](https://open.fda.gov/apis/device/event/) and [device recall API](https://open.fda.gov/apis/device/recall/). These are a saved snapshot; later API refreshes may differ.

## Validation

The five table schemas and partition totals were validated. The snapshot has no duplicate identifiers or orphan child rows; its selected rows have no missing, invalid, or out-of-range dates. Actual MAUDE event dates run through October 5, although the fixed query bound is October 13. The source-wide MAUDE missing-event-date count is 3,699,144; those undated records cannot be assigned to this window. The offline test suite passed **27 tests**.

## Navigate the files

The five CSVs are in [`data/processed/`](data/processed/), and the exploratory notebook is [`notebooks/data_exploration.ipynb`](notebooks/data_exploration.ipynb). The application code is in [`src/medical_device_safety/`](src/medical_device_safety/), with automated tests in [`tests/`](tests/). Open [`BI VIsualization.pbix`](BI%20VIsualization.pbix) for the report and [`docs/powerbi_guide.md`](docs/powerbi_guide.md) for its model, measures, and setup guidance.

## Run checks or refresh data

Install dependencies with `python -m pip install -r requirements.txt`, then run tests with `python -m pytest`. To refresh the dataset, set `OPENFDA_API_KEY` in a private root `.env` file (see [`.env.example`](.env.example)), then run `python -m medical_device_safety.prepare_data` with `PYTHONPATH=src` set. A refresh can change the snapshot. The notebook can be opened in Jupyter to inspect the included data without making API requests.

## Interpretation

MAUDE reports are suspected associations, not proof of causation, device failure, incidence, or comparative safety. The counts are not rates because suitable exposure denominators are unavailable. Device and problem rows are entries, not unique incidents. Recall records describe a separate process and are not linked to individual MAUDE reports. The narrow date window is not suited to long-term trend claims. This project is exploratory, not clinical or regulatory advice.
