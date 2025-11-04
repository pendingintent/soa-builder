# SoA Normalization

This workspace contains a script `normalize_soa.py` to transform a wide "Schedule of Activities" (SoA) CSV into a simple relational representation.

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

## Usage
```bash
python normalize_soa.py --input files/SoA_breast_cancer.csv --out-dir normalized --sqlite normalized/soa.db
```

### Validation
After normalization you can run automated checks:
```bash
python validate_soa.py --dir normalized
```
This currently validates imaging schedule intervals (baseline → Week 6 → Week 12 → Week 18). Exit code 0 indicates success.

## Assumptions & Heuristics
- All non-first header columns are considered visits.
- Windows parsed from patterns like `(-28 to -1d)`, `(±7d)`, `30±7d`.
- Repeat patterns detected: `every 2 cycles`, `q12w`, `q3w`, `every 12 weeks`.
- Additional conditional text retained in `status`.

## Extending
- Refine category taxonomy with controlled terminology (CDISC).
- Expand `schedule_rules` to generate concrete future occurrences (date simulation).
- Add endpoint linkage & CRF mapping tables.
- Additional validators (PK sampling alignment, PRO schedule completeness).

## License
Internal use; extend as needed.
