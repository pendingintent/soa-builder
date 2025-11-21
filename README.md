[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)



# SoA Workbench

This workspace provides a Python package `soa_builder` with APIs to:

1. Normalize a wide Schedule of Activities (SoA) matrix into relational tables.
2. Expand repeating schedule rules into projected calendar instances.


## Installation
Recommended: editable install for development.
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pre-commit install
```

## Start web server
```bash
soa-builder-web  # starts uvicorn on 0.0.0.0:8000 with reload
```

Or manually:
```bash
uvicorn soa_builder.web.app:app --reload --port 8000
```
HTML UI:
- Open http://localhost:8000/ in a browser.
- Add visits and activities; click cells to toggle status (blank -> X -> blank). 'O' values are not surfaced in the UI; clearing removes the cell row.
- Use "Generate Normalized Summary" link to produce artifacts.
 - Use export buttons (to be added) or hit endpoints directly for XLSX output.
 - Delete a visit or activity using the ✕ button next to its name (confirmation dialog). Deletion cascades to associated cells and automatically reorders remaining items.
 - View biomedical concepts via the "Concepts" navigation link (`GET /ui/concepts`): renders a table of concept codes, titles and API links (cached; force refresh per study using `POST /ui/soa/{id}/concepts_refresh`).
 
Biomedical Concepts API Access:
- The concepts list and detail pages call the CDISC Library API.
- Set one (or both) of: `CDISC_SUBSCRIPTION_KEY`, `CDISC_API_KEY`.
- The server will send all of these headers when possible:
	- `Ocp-Apim-Subscription-Key: <key>`
	- `Authorization: Bearer <key>` (when `CDISC_API_KEY` provided)
	- `api-key: <key>` (legacy fallback)
- If only one key is defined it is reused across header variants.
- Directly opening the API URL in the browser will 401 because the browser does not attach the required headers; use the internal detail page or an API client (curl/Postman) with the headers above.

## Development & Testing
Run unit tests:
```bash
pytest
```

> Full, updated endpoint reference (including Elements, freezes, audits, JSON CRUD and UI helpers) lives in `README_endpoints.md`. Consult that file for detailed request/response examples, curl snippets, and future enhancement notes.

Endpoints:

See **docs/api_endpoints.xlsx**

## Experimental (not yet supported)
After populating data, retrieve normalized artifacts:
```bash
curl http://localhost:8000/soa/1/normalized
```
### Source
Input format: first column `Activity`, subsequent columns are visit/timepoint headers. Cells contain markers `X`, `Optional`, `If indicated`, or repeating patterns (`Every 2 cycles`, `q12w`).

### Output Artifacts
Running the script produces (in `--out-dir`):
- `visits.csv` — One row per visit/timepoint with parsed window info, inferred category, repeat pattern.
- `activities.csv` — Unique activities (one per original row).
- `visit_activities.csv` — Junction table mapping activities to visits with status and flags.
- `activity_categories.csv` — Heuristic classification of each activity (labs, imaging, dosing, admin, etc.).
- `schedule_rules.csv` — Extracted repeating schedule logic from headers and cells (e.g., `q12w`, `Every 2 cycles`).
- Optional: SQLite database (`--sqlite path`) containing all tables.

### visits.csv Columns
- `visit_id`: Sequential numeric id.
- `raw_header`: Original header text.
- `visit_name`: Header stripped of parenthetical codes.
- `visit_code`: Code extracted from parentheses (e.g., `C1D1`, `EOT`).
- `sequence_index`: Positional order.
- `window_lower` / `window_upper`: Parsed day offsets if available.
- `repeat_pattern`: Detected repeating pattern (e.g., `every 2 cycles`).
- `category`: Heuristic classification (screening, baseline, treatment, follow_up, eot).

### activities.csv Columns
- `activity_id`: Sequential id.
- `activity_name`: Name from first column.

### visit_activities.csv Columns
- `id`: Junction id.
- `visit_id`: FK to visits.
- `activity_id`: FK to activities.
- `status`: Raw cell content.
- `required_flag`: 1 if cell starts with `X`.
- `conditional_flag`: 1 if cell contains `Optional` or `If indicated`.

### activity_categories.csv Columns
- `activity_id`: FK to activities.
- `category`: Assigned heuristic category label.

### schedule_rules.csv Columns
- `rule_id`: Unique rule id.
- `pattern`: Normalized repeating pattern token (e.g., `q12w`).
- `description`: Human readable description of pattern source.
- `source_type`: `header` or `cell` origin.
- `activity_id`: Populated if pattern came from a cell (else null).
- `visit_id`: Populated if pattern came from a header.
- `raw_text`: Original text fragment containing the pattern.



# Notes:
- HTMX is loaded via CDN; no build step required.
- For production, configure a persistent DB path via SOA_BUILDER_DB env variable.

Artifacts stored under `normalized/soa_{id}/`.

