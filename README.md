



# SoA Builder (Normalization, Expansion & Validation)

This workspace provides a Python package `soa_builder` with a CLI and APIs to:

1. Normalize a wide Schedule of Activities (SoA) matrix into relational tables.
2. Expand repeating schedule rules into projected calendar instances.
3. Validate imaging (and future) activity intervals.

Legacy standalone scripts (`normalize_soa.py`, `validate_soa.py`) remain for reference; new work should use the CLI.

## Source
Input format: first column `Activity`, subsequent columns are visit/timepoint headers. Cells contain markers like `X`, `Optional`, `If indicated`, or repeating patterns (`Every 2 cycles`, `q12w`).

## Output Artifacts
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

## Installation

Recommended: editable install for development.
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

This installs the console script `soa-builder`.
Example:
```bash
soa-builder normalize --input files/SoA_breast_cancer.csv --out-dir normalized
soa-builder expand --normalized-dir normalized --start-date 2025-01-01 --json-out normalized/schedule_instances.json
soa-builder validate --normalized-dir normalized
```

## CLI Usage

The CLI exposes three subcommands: `normalize`, `expand`, `validate`.

### Normalize
```bash
soa-builder normalize --input files/SoA_breast_cancer.csv --out-dir normalized --sqlite normalized/soa.db
```
Outputs written to `normalized/` (CSV and optional SQLite).

### Expand Schedule Rules
```bash
soa-builder expand --normalized-dir normalized --start-date 2025-01-01 \
	--cycle-length-days 21 --num-cycles 8 --followup-weeks 104 \
	--json-out normalized/schedule_instances.json
```
Options:
- `--filter-pattern PATTERN` (repeatable) to limit patterns (e.g. `--filter-pattern q12w`)
- `--cycle-lengths 21,21,28` for heterogeneous cycle lengths
- `--horizon-days DAYS` override default calculated horizon
- `--max-occurrences N` cap per-rule expansions

### Validate Imaging Intervals
```bash
soa-builder validate --normalized-dir normalized --expected-interval-weeks 6 --tolerance-days 4
```
Exit code non-zero indicates deviations; listed per interval.

## Python API
```python
from soa_builder import normalize_soa, expand_schedule_rules, validate_imaging_schedule
summary = normalize_oa('files/SoA_breast_cancer.csv', 'normalized')
# Load rules/visits then expand (see cli implementation for loaders)
```

## Development & Testing
Run unit tests:
```bash
pytest
```

## Roadmap
- Additional validators (PK sampling, PRO schedule completeness)
- Console script entry point publication via `pyproject.toml`
- Enriched rule grammar (e.g. conditional frequency changes)
- SDTM domain mapping utilities
 - Web application for interactive SoA authoring (FastAPI + HTMX) extended with biomedical concept browsing and stable activity UIDs

## Assumptions & Heuristics
- All non-first header columns are considered visits.
- Windows parsed from patterns like `(-28 to -1d)`, `(±7d)`, `30±7d`.
- Repeat patterns detected: `every 2 cycles`, `q12w`, `q3w`, `every 12 weeks`.
- Additional conditional text retained in `status`.

## Extending
- Refine category taxonomy with controlled terminology (CDISC)
- Richer recurrence parsing (e.g., bi-weekly then monthly transitions)
- Endpoint linkage & CRF mapping tables
- Additional validators (PK sampling alignment, PRO schedule completeness)
 - Web UI (React or HTMX) atop FastAPI backend for matrix editing

## Web Application (Experimental)
An initial FastAPI backend (`soa_builder.web.app`) allows creating an SoA interactively via REST:

> Full, continuously updated endpoint reference (including Elements, freezes, audits, JSON CRUD and UI helpers) lives in `README_endpoints.md`. Consult that file for detailed request/response examples, curl snippets, and future enhancement notes.

Endpoints:
- POST /soa {"name": "Breast Cancer Phase 2"}
- POST /soa/{id}/visits {"name": "C1D1", "raw_header": "Cycle 1 Day 1 (C1D1)"}
- POST /soa/{id}/activities {"name": "Hematology"}
- POST /soa/{id}/cells {"visit_id": 1, "activity_id": 1, "status": "X"}
- GET  /soa/{id}/matrix  -> JSON matrix
- GET  /soa/{id}/normalized -> Runs normalization pipeline; returns summary
 - DELETE /soa/{id}/visits/{visit_id} -> Remove a visit and all its cells; remaining visits reindexed
 - DELETE /soa/{id}/activities/{activity_id} -> Remove an activity and all its cells; remaining activities reindexed
 - POST /soa/{id}/activities/bulk {"names": ["Hematology", "Chemistry", "ECG"]} -> create multiple activities (skips duplicates & blanks)
 - POST /soa/{id}/matrix/import -> Ingest wide matrix JSON body
 - GET  /soa/{id}/export/xlsx -> Download current matrix as Excel workbook (sheet: SoA)
 - GET  /soa/{id}/export/pdf  -> Download current matrix as PDF table

### Wide Matrix Import Format
`POST /soa/{id}/matrix/import`
```jsonc
{
	"visits": [
		{"name": "C1D1", "raw_header": "Cycle 1 Day 1 (C1D1)"},
		{"name": "C1D8"},
		{"name": "C1D15"}
	],
	"activities": [
		{"name": "Hematology", "statuses": ["X", "X", "O"]},
		{"name": "Chemistry", "statuses": ["", "X", ""]},
		{"name": "ECG", "statuses": ["O", "", "O"]}
	],
	"reset": true
}
```
Rules:
- `statuses` array length must equal number of `visits`.
- Blank / empty status strings are ignored (no cell row created).
- When `reset` is true existing visits, activities, and cells for the SoA are cleared first.
- All inserts preserve provided order for indexing.

Run server:
```bash
soa-builder-web  # starts uvicorn on 0.0.0.0:8000 with reload
```

Or manually:
```bash
uvicorn soa_builder.web.app:app --reload --port 8000
```

After populating data, retrieve normalized artifacts:
```bash
curl http://localhost:8000/soa/1/normalized
```

HTML UI:
- Open http://localhost:8000/ in a browser.
- Add visits and activities; click cells to toggle status (blank -> X -> blank). 'O' values are not surfaced in the UI; clearing removes the cell row.
- Use "Generate Normalized Summary" link to produce artifacts.
 - Use export buttons (to be added) or hit endpoints directly for XLSX/PDF output.
 - Delete a visit or activity using the ✕ button next to its name (confirmation dialog). Deletion cascades to associated cells and automatically reorders remaining items.
 - (Upcoming) Bulk add activities and matrix import could be surfaced via a textarea or JSON upload panel.
 - View biomedical concepts via the "Concepts" navigation link (`GET /ui/concepts`): renders a table of concept codes, titles and API links (cached; force refresh per study using `POST /ui/soa/{id}/concepts_refresh`).

Activity Identifiers:
- Each activity now has a stable `activity_uid` (format `Activity_<n>` unique within a study) maintained during reorder using a two-phase temporary renaming to avoid uniqueness collisions.
- Unique index `(soa_id, activity_uid)` enforces stability for exports, snapshots and audit trails.
 
Biomedical Concepts API Access:
- The concepts list and detail pages call the CDISC Library API.
- Set one (or both) of: `CDISC_SUBSCRIPTION_KEY`, `CDISC_API_KEY`.
- The server will send all of these headers when possible:
	- `Ocp-Apim-Subscription-Key: <key>`
	- `Authorization: Bearer <key>` (when `CDISC_API_KEY` provided)
	- `api-key: <key>` (legacy fallback)
- If only one key is defined it is reused across header variants.
- Directly opening the API URL in the browser will 401 because the browser does not attach the required headers; use the internal detail page or an API client (curl/Postman) with the headers above.

Notes:
- HTMX is loaded via CDN; no build step required.
- For production, configure a persistent DB path via SOA_BUILDER_DB env variable.

Artifacts stored under `normalized/soa_{id}/`.

## License
Internal use; extend as needed.
