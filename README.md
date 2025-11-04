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

## License
Internal use; extend as needed.
