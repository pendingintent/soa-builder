# SoA Builder API & UI Endpoints

This document enumerates all public (JSON) API endpoints and key UI (HTML/HTMX) endpoints provided by `soa_builder.web.app`. It groups them by domain with concise purpose, parameters, sample requests, and typical responses.

> Conventions
> - `{soa_id}` etc. denote path parameters.
> - Unless noted, JSON endpoints return `application/json`.
> - Time values are ISO-8601 UTC.
> - All IDs are integers unless stated otherwise.
> - Errors use FastAPI default error model: `{"detail": "message"}`.
>
> Authentication: Not implemented (all endpoints open). Add auth (API keys / OAuth2) before production use.

---
## Health / Metadata

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/` | Index HTML (lists studies & create form) |
| GET | `/concepts/status` | Diagnostic info about biomedical concepts cache |

---
## SoA (Study Container)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/soa` | Create new SoA container. Body: `{ "name": str, optional study fields }` |
| GET | `/soa/{soa_id}` | Summary (visits, activities counts, etc.) |
| POST | `/soa/{soa_id}/metadata` | Update study metadata fields (study_id, label, description) |
| GET | `/soa/{soa_id}/normalized` | Normalized SoA JSON (post-processing pipeline) |
| GET | `/soa/{soa_id}/matrix` | Raw matrix: visits, activities, cells |
| POST | `/soa/{soa_id}/matrix/import` | Bulk import matrix (payload structure TBD) |
| GET | `/soa/{soa_id}/export/xlsx` | Download Excel workbook (binary) |

### Sample: Create SoA
```bash
curl -X POST http://localhost:8000/soa -H 'Content-Type: application/json' \
  -d '{"name":"Phase I Study","study_id":"STUDY-001"}'
```
Response:
```json
{ "id": 3, "name": "Phase I Study" }
```

---
## Visits

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/soa/{soa_id}/visits` | Create visit `{ name, raw_header?, epoch_id? }` |
| PATCH | `/soa/{soa_id}/visits/{visit_id}` | Update visit (partial) returns `updated_fields` |
| DELETE | `/soa/{soa_id}/visits/{visit_id}` | Delete visit (and its cells) |
| GET | `/soa/{soa_id}/visits/{visit_id}` | Fetch visit detail |
| (UI) POST | `/ui/soa/{soa_id}/add_visit` | Form submission create visit |
| (UI) POST | `/ui/soa/{soa_id}/reorder_visits` | Drag reorder (form field `order`) |
| (UI) POST | `/ui/soa/{soa_id}/delete_visit` | Delete via HTMX |
| (UI) POST | `/ui/soa/{soa_id}/set_visit_epoch` | Assign / clear epoch |

Reorder API (JSON) not implemented for visits yet (only form version).

---
## Activities

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/soa/{soa_id}/activities` | Create activity `{ name }` |
| PATCH | `/soa/{soa_id}/activities/{activity_id}` | Update activity (partial) returns `updated_fields` |
| DELETE | `/soa/{soa_id}/activities/{activity_id}` | Delete activity (and its cells & concepts) |
| POST | `/soa/{soa_id}/activities/{activity_id}/concepts` | Set concepts list `{ concept_codes: [...] }` |
| POST | `/soa/{soa_id}/activities/bulk` | Bulk add activities (payload defined in code) |
| GET | `/soa/{soa_id}/activities/{activity_id}` | Fetch activity detail |
| (UI) POST | `/ui/soa/{soa_id}/add_activity` | Form create |
| (UI) POST | `/ui/soa/{soa_id}/reorder_activities` | Drag reorder |
| (UI) POST | `/ui/soa/{soa_id}/delete_activity` | Delete via HTMX |

---
## Epochs

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/soa/{soa_id}/epochs` | Create epoch `{ name }` (sequence auto-assigned) |
| GET | `/soa/{soa_id}/epochs` | List epochs (ordered) |
| GET | `/soa/{soa_id}/epochs/{epoch_id}` | Fetch epoch detail |
| POST | `/soa/{soa_id}/epochs/{epoch_id}/metadata` | Update name/label/description (returns `updated_fields`) |
| DELETE | `/soa/{soa_id}/epochs/{epoch_id}` | Delete epoch |
| (UI) POST | `/ui/soa/{soa_id}/add_epoch` | Form create |
| (UI) POST | `/ui/soa/{soa_id}/update_epoch` | Update via form |
| (UI) POST | `/ui/soa/{soa_id}/reorder_epochs` | Reorder |
| (UI) POST | `/ui/soa/{soa_id}/delete_epoch` | Delete |

---
## Elements (New)
## Arms (New)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/soa/{soa_id}/arms` | List arms (ordered) |
| GET | `/soa/{soa_id}/arms/{arm_id}` | Fetch arm detail (includes immutable `arm_uid`) |
| POST | `/soa/{soa_id}/arms` | Create arm `{ name, label?, description? }` (auto assigns next `arm_uid` = `StudyArm_<n>`) |
| PATCH | `/soa/{soa_id}/arms/{arm_id}` | Update arm (partial) returns `updated_fields` (arm_uid immutable) |
| DELETE | `/soa/{soa_id}/arms/{arm_id}` | Delete arm |
| POST | `/soa/{soa_id}/arms/reorder` | Reorder (body JSON array of IDs) |
| GET | `/soa/{soa_id}/arm_audit` | Arm audit log (create/update/delete/reorder entries) |
| (UI) POST | `/ui/soa/{soa_id}/add_arm` | Form create |
| (UI) POST | `/ui/soa/{soa_id}/update_arm` | Form update |
| (UI) POST | `/ui/soa/{soa_id}/delete_arm` | Form delete |
| (UI) POST | `/ui/soa/{soa_id}/reorder_arms` | Drag reorder (form) |

Arm rows include immutable `arm_uid` (unique per study). Element linkage has been removed; a migration now physically drops the legacy `element_id` and `etcd` columns from existing databases. Fresh installs never create these columns.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/soa/{soa_id}/elements` | List elements (ordered) |
| GET | `/soa/{soa_id}/elements/{element_id}` | Fetch element detail |
| POST | `/soa/{soa_id}/elements` | Create element `{ name, label?, description?, testrl?, teenrl? }` |
| PATCH | `/soa/{soa_id}/elements/{element_id}` | Update (partial) |
| DELETE | `/soa/{soa_id}/elements/{element_id}` | Delete |
| POST | `/soa/{soa_id}/elements/reorder` | Reorder (body JSON array of IDs) |
| GET | `/soa/{soa_id}/element_audit` | Element audit log (create/update/delete/reorder entries) |
| (UI) POST | `/ui/soa/{soa_id}/add_element` | Form create |
| (UI) POST | `/ui/soa/{soa_id}/update_element` | Form update |
| (UI) POST | `/ui/soa/{soa_id}/delete_element` | Form delete |
| (UI) POST | `/ui/soa/{soa_id}/reorder_elements` | Drag reorder (form) |

### Element JSON Examples
Create:
```bash
curl -X POST http://localhost:8000/soa/5/elements \
  -H 'Content-Type: application/json' \
  -d '{"name":"Screening","label":"SCR","description":"Screening element"}'
```
Reorder:
```bash
curl -X POST http://localhost:8000/soa/5/elements/reorder \
  -H 'Content-Type: application/json' \
  -d '[3,1,2]'
```

Audit entry structure (GET `/soa/{soa_id}/element_audit`):
```json
{
  "id": 12,
  "element_id": 7,
  "action": "update",
  "before": {"id":7,"name":"Screening"},
  "after": {"id":7,"name":"Screening Updated"},
  "performed_at": "2025-11-07T12:34:56.123456+00:00"
}
```

---
## Cells (Matrix)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/soa/{soa_id}/cells` | Upsert a cell `{ visit_id, activity_id, status }` |
| (UI) POST | `/ui/soa/{soa_id}/toggle_cell` | Toggle cell (HTMX) |
| (UI) POST | `/ui/soa/{soa_id}/set_cell` | Explicit set (HTMX) |

Status typical values: "X" or empty (cleared).

---
## Biomedical Concepts

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/ui/soa/{soa_id}/concepts_refresh` | Force remote re-fetch + cache reset |
| GET | `/concepts/status` | Cache diagnostics (see above) |
| GET | `/ui/concepts` | HTML table listing biomedical concepts (code, title, API href) |
| GET | `/ui/concepts/{code}` | HTML detail page for a single concept (title, API href, parent concept/package links) |

Concept assignment happens via `POST /soa/{soa_id}/activities/{activity_id}/concepts`.

Payload:
```json
{ "concept_codes": ["C12345", "C67890"] }
```

---
## Freezes (Versioning) & Rollback

| Method | Path | Purpose |
| ------ | ---- | ------- |
| POST | `/ui/soa/{soa_id}/freeze` | Create new version (HTML) |
| GET | `/soa/{soa_id}/freeze/{freeze_id}` | Get freeze snapshot JSON |
| GET | `/ui/soa/{soa_id}/freeze/{freeze_id}/view` | Modal view of snapshot |
| GET | `/ui/soa/{soa_id}/freeze/diff` | HTML diff (query params: `left`, `right`) |
| GET | `/soa/{soa_id}/freeze/diff.json` | JSON diff (`?left=&right=`) |
| POST | `/ui/soa/{soa_id}/freeze/{freeze_id}/rollback` | Restore SoA to snapshot |

Snapshot includes keys: `epochs`, `elements`, `visits`, `activities`, `cells`, `activity_concepts`, metadata fields.

---
## Audits

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/soa/{soa_id}/rollback_audit` | JSON rollback audit log |
| GET | `/soa/{soa_id}/reorder_audit` | JSON reorder audit log (visits, activities, epochs, elements) |
| GET | `/ui/soa/{soa_id}/rollback_audit` | HTML modal rollback audit |
| GET | `/ui/soa/{soa_id}/reorder_audit` | HTML modal reorder audit |
| GET | `/soa/{soa_id}/rollback_audit/export/xlsx` | Excel export rollback audit |
| GET | `/soa/{soa_id}/reorder_audit/export/xlsx` | Excel export reorder audit |
| GET | `/soa/{soa_id}/reorder_audit/export/csv` | CSV export reorder audit |
| GET | `/soa/{soa_id}/element_audit` | Element audit (see Elements section) |
| GET | `/soa/{soa_id}/visit_audit` | Visit audit log (create/update/delete) |
| GET | `/soa/{soa_id}/activity_audit` | Activity audit log (create/update/delete) |
| GET | `/soa/{soa_id}/epoch_audit` | Epoch audit log (create/update/delete/reorder) |
| GET | `/soa/{soa_id}/arm_audit` | Arm audit log (create/update/delete/reorder) |

Rollback audit row fields: `id, soa_id, freeze_id, performed_at, visits_restored, activities_restored, cells_restored, concepts_restored, elements_restored`.

Reorder audit row fields: `id, soa_id, entity_type, old_order_json, new_order_json, performed_at`.

### Audit Entry Shapes

Each per-entity audit endpoint (`element_audit`, `visit_audit`, `activity_audit`, `epoch_audit`) returns rows with a common structure:

```
{
  "id": 42,
  "<entity>_id": 7,
  "action": "create" | "update" | "delete" | "reorder",
  "before": { ... } | null,
  "after": { ... } | null,
  "performed_at": "2025-11-07T12:34:56.123456+00:00",
  "updated_fields": ["name","label"]  // present only for update actions
}
```

Notes:
- `before` is null for creates; `after` is null for deletes.
- `updated_fields` lists the keys that changed between `before` and `after` for update actions (omitted otherwise).
- Epoch reorder also creates an entry in `reorder_audit`; if epoch attributes (name/label/description) change, an `update` row appears in `epoch_audit` with `updated_fields`.
- Element reorder emits an `action":"reorder"` row in `element_audit` in addition to the global `reorder_audit` table.

---
## UI Editing Endpoints (HTMX Helpers)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/ui/soa/{soa_id}/edit` | Primary editing interface (HTML) |
| POST | `/ui/soa/{soa_id}/update_meta` | Update study metadata (form) |
| POST | `/ui/soa/create` | Create new study via form |

These endpoints render or redirect; they are not intended for API clients.

---
## Reordering (JSON vs UI)

| Domain | JSON Endpoint | UI Endpoint | Body / Form |
| ------ | ------------- | ----------- | ----------- |
| Elements | POST `/soa/{soa_id}/elements/reorder` | POST `/ui/soa/{soa_id}/reorder_elements` | JSON array / form `order` |
| Visits | POST `/soa/{soa_id}/visits/reorder` | POST `/ui/soa/{soa_id}/reorder_visits` | JSON array / form `order` |
| Activities | POST `/soa/{soa_id}/activities/reorder` | POST `/ui/soa/{soa_id}/reorder_activities` | JSON array / form `order` |
| Epochs | POST `/soa/{soa_id}/epochs/reorder` | POST `/ui/soa/{soa_id}/reorder_epochs` | JSON array / form `order` |
| Arms | POST `/soa/{soa_id}/arms/reorder` | POST `/ui/soa/{soa_id}/reorder_arms` | JSON array / form `order` |

---
## Error Handling
Typical errors:
- 400: Validation or duplicate version label.
- 404: Entity not found / SoA not found.
- 409: (Future) uniqueness conflicts (currently 400 for version label).

Example error:
```json
{"detail":"SOA not found"}
```

---
## Future Enhancements (Suggested)
- Add pagination / filtering for large audit logs.
- Introduce authentication & RBAC.
- Add OpenAPI tags grouping elements vs core vs audits.
- Rate limiting & conditional ETag caching for large snapshots.

---
## Quick Reference (Most Used)
```
Create SoA         POST /soa
Get Visit Detail   GET  /soa/{id}/visits/{visit_id}
Get Activity Detail GET /soa/{id}/activities/{activity_id}
Get Arm Detail     GET  /soa/{id}/arms/{arm_id}
List Elements      GET  /soa/{id}/elements
Create Element     POST /soa/{id}/elements
Update Element     PATCH /soa/{id}/elements/{element_id}
Reorder Elements   POST /soa/{id}/elements/reorder   (JSON array)
Reorder Visits     POST /soa/{id}/visits/reorder     (JSON array)
Reorder Activities POST /soa/{id}/activities/reorder (JSON array)
Reorder Epochs     POST /soa/{id}/epochs/reorder     (JSON array)
Reorder Arms       POST /soa/{id}/arms/reorder       (JSON array)
Freeze Version     POST /ui/soa/{id}/freeze (form)
Rollback           POST /ui/soa/{id}/freeze/{freeze_id}/rollback
Element Audit      GET  /soa/{id}/element_audit
Rollback Audit     GET  /soa/{id}/rollback_audit
Reorder Audit      GET  /soa/{id}/reorder_audit
Export Excel       GET  /soa/{id}/export/xlsx
Normalized View    GET  /soa/{id}/normalized
Concepts List      GET  /ui/concepts
```

---
## Curl Cheat-Sheet
```bash
# Create a study
curl -s -X POST localhost:8000/soa -H 'Content-Type: application/json' -d '{"name":"Demo"}'

# Add element
curl -s -X POST localhost:8000/soa/1/elements -H 'Content-Type: application/json' -d '{"name":"Screening"}'

# List elements
curl -s localhost:8000/soa/1/elements | jq

# Update element
curl -s -X PATCH localhost:8000/soa/1/elements/2 -H 'Content-Type: application/json' -d '{"label":"SCR"}'

# Reorder elements
curl -s -X POST localhost:8000/soa/1/elements/reorder -H 'Content-Type: application/json' -d '[2,1]'

# Freeze
curl -s -X POST localhost:8000/ui/soa/1/freeze -d 'version_label=v1'

# Diff two freezes
curl -s 'localhost:8000/soa/1/freeze/diff.json?left=5&right=7' | jq
```

---
---
## Terminology (DDF & Protocol)

Two parallel terminology domains are supported: DDF Terminology and Protocol Terminology. Each provides identical capabilities: load Excel sheet, query with filters, upload new sheet via UI, audit loads (with filtering and CSV/JSON export).

| Domain | Method | Path | Purpose |
|--------|--------|------|---------|
| DDF | POST | `/admin/load_ddf_terminology` | (Re)load default DDF Excel from `files/DDF_Terminology_2025-09-26.xls` (captures `dataset_date=2025-09-26`) |
| DDF | GET | `/ddf/terminology` | Query rows (filters: `search`, `code`, `codelist_name`, `codelist_code`, pagination `limit`,`offset`). Rows include synthetic `dataset_date` extracted only from the sheet name. |
| DDF | GET | `/ui/ddf/terminology` | HTML UI page (same filters + upload status) including `dataset_date`. |
| DDF | POST | `/ui/ddf/terminology/upload` | Upload Excel (.xls/.xlsx) and reload table; `dataset_date` auto-derived strictly from sheet name (file name ignored). |
| DDF | GET | `/ddf/terminology/audit` | List audit entries (filters: `source`, `start`, `end`) each with `dataset_date`. |
| DDF | GET | `/ddf/terminology/audit/export.csv` | Export filtered audit rows (includes `dataset_date`) as CSV |
| DDF | GET | `/ddf/terminology/audit/export.json` | Export filtered audit rows (includes `dataset_date`) as JSON |
| DDF | GET | `/ui/ddf/terminology/audit` | HTML audit listing with filters + export links showing `dataset_date`. |
| Protocol | POST | `/admin/load_protocol_terminology` | (Re)load default Protocol Excel from `files/Protocol_Terminology_2025-09-26.xls` (captures `dataset_date=2025-09-26`) |
| Protocol | GET | `/protocol/terminology` | Query rows (filters: `search`, `code`, `codelist_name`, `codelist_code`, pagination). Rows include `dataset_date` extracted only from the sheet name. |
| Protocol | GET | `/ui/protocol/terminology` | HTML UI page (same filters + upload status) including `dataset_date`. |
| Protocol | POST | `/ui/protocol/terminology/upload` | Upload & reload Protocol terminology; `dataset_date` auto-derived strictly from sheet name. |
| Protocol | GET | `/protocol/terminology/audit` | Audit entries (filters: `source`, `start`, `end`) each with `dataset_date`. |
| Protocol | GET | `/protocol/terminology/audit/export.csv` | CSV export (includes `dataset_date`). |
| Protocol | GET | `/protocol/terminology/audit/export.json` | JSON export (includes `dataset_date`). |
| Protocol | GET | `/ui/protocol/terminology/audit` | HTML audit listing + export links showing `dataset_date`. |

### Terminology Query Parameters
`search` performs case-insensitive substring across key text columns (`code`, `cdisc_submission_value`, `cdisc_definition`, `cdisc_synonym_s`, `nci_preferred_term`, `codelist_name`, `codelist_code`). Exact-match filters (`code`, `codelist_name`, `codelist_code`) narrow before search is applied. Pagination: `limit` (1–200), `offset` (>=0).

### Audit Entry Fields
`id, loaded_at (UTC ISO), file_path, original_filename, sheet_name, row_count, column_count, columns_json, source (admin|upload), file_hash (sha256), error (nullable), dataset_date`

`dataset_date` is extracted via regex `YYYY-MM-DD` only from the sheet name. If the sheet name does not contain a date substring (e.g. `DDF Terminology 2025-09-26`) the load will fail with HTTP 400. The file name is intentionally ignored to enforce explicit versioning in worksheet naming. An index exists on this field (`idx_ddf_audit_dataset_date`, `idx_protocol_audit_dataset_date`) enabling efficient future filtering/grouping.

Error rows have `row_count=0` and `error` populated (e.g. read or missing file). Successful loads have `error=null`.

### Sample Terminology Queries
```bash
# DDF: find by codelist_code
curl -s --get 'http://localhost:8000/ddf/terminology' \
  --data-urlencode 'codelist_code=C139020' | jq '.matched_count'

# Protocol: search within definition text
curl -s --get 'http://localhost:8000/protocol/terminology' \
  --data-urlencode 'search=trial' \
  --data-urlencode 'limit=5' | jq '.rows[].code'

# Audit export (Protocol, last 7 days)
curl -s --get 'http://localhost:8000/protocol/terminology/audit/export.csv' \
  --data-urlencode 'start=2025-11-05' \
  --data-urlencode 'end=2025-11-12' > protocol_audit.csv
# Show dataset_date values (Protocol audit)
curl -s --get 'http://localhost:8000/protocol/terminology/audit' | jq '.rows[].dataset_date' | head
```

### Upload via UI Forms
- DDF: `/ui/ddf/terminology` (sheet name default: `DDF Terminology 2025-09-26`)
- Protocol: `/ui/protocol/terminology` (sheet name default: `Protocol Terminology 2025-09-26`)

Both accept `.xls` or `.xlsx`. A SHA-256 hash is computed and stored in audit for integrity tracking.

---
Generated on: 2025-11-12

## Full Endpoint Inventory (Auto-Generated 2025-11-12)

Below is a consolidated list of all FastAPI routes currently defined in `src/soa_builder/web/app.py`. "Type" reflects typical response kind (JSON, HTML, Binary, CSV). UI/Form endpoints are primarily for browser interaction (HTMX/HTML forms) and may redirect.

| Method | Path | Type | Notes |
|--------|------|------|-------|
| GET | `/` | HTML | Index & study creation form |
| GET | `/concepts/status` | JSON | Biomedical concepts cache diagnostics |
| GET | `/sdtm/specializations/status` | JSON | SDTM dataset specializations cache diagnostics |
| GET | `/soa/{soa_id}` | JSON | Study summary (counts, metadata) |
| GET | `/soa/{soa_id}/elements` | JSON | List elements |
| GET | `/soa/{soa_id}/elements/{element_id}` | JSON | Element detail |
| GET | `/soa/{soa_id}/element_audit` | JSON | Element audit log |
| GET | `/soa/{soa_id}/arms` | JSON | List arms |
| GET | `/soa/{soa_id}/arm_audit` | JSON | Arm audit log |
| GET | `/soa/{soa_id}/freeze/{freeze_id}` | JSON | Freeze snapshot JSON |
| GET | `/ui/soa/{soa_id}/freeze/{freeze_id}/view` | HTML | Freeze snapshot modal |
| GET | `/ui/soa/{soa_id}/freeze/diff` | HTML | Diff view (query `left`,`right`) |
| GET | `/soa/{soa_id}/freeze/diff.json` | JSON | Diff JSON (`left`,`right`) |
| GET | `/soa/{soa_id}/rollback_audit` | JSON | Rollback audit log |
| GET | `/soa/{soa_id}/reorder_audit` | JSON | Reorder audit log |
| GET | `/ui/soa/{soa_id}/rollback_audit` | HTML | Rollback audit modal |
| GET | `/ui/soa/{soa_id}/reorder_audit` | HTML | Reorder audit modal |
| GET | `/soa/{soa_id}/rollback_audit/export/xlsx` | Binary | Excel export rollback audit |
| GET | `/soa/{soa_id}/reorder_audit/export/xlsx` | Binary | Excel export reorder audit |
| GET | `/soa/{soa_id}/reorder_audit/export/csv` | CSV | CSV export reorder audit |
| GET | `/soa/{soa_id}/visits/{visit_id}` | JSON | Visit detail |
| GET | `/soa/{soa_id}/activities/{activity_id}` | JSON | Activity detail |
| GET | `/soa/{soa_id}/epochs` | JSON | List epochs (ordered) |
| GET | `/soa/{soa_id}/epochs/{epoch_id}` | JSON | Epoch detail |
| GET | `/soa/{soa_id}/matrix` | JSON | Matrix (visits, activities, cells) |
| GET | `/soa/{soa_id}/export/xlsx` | Binary | Excel workbook export |
| GET | `/soa/{soa_id}/normalized` | JSON | Normalized representation |
| GET | `/ui/soa/{soa_id}/edit` | HTML | Main editing UI |
| GET | `/ui/concepts` | HTML | Concepts listing |
| GET | `/ui/concepts/{code}` | HTML | Concept detail (parent links) |
| GET | `/ui/sdtm/specializations` | HTML | SDTM dataset specializations list |
| GET | `/ui/sdtm/specializations/{idx}` | HTML | SDTM specialization raw JSON detail |
| GET | `/ddf/terminology` | JSON | Query DDF terminology rows |
| GET | `/ui/ddf/terminology` | HTML | DDF terminology UI page |
| GET | `/ddf/terminology/audit` | JSON | DDF audit entries |
| GET | `/ddf/terminology/audit/export.csv` | CSV | DDF audit export CSV |
| GET | `/ddf/terminology/audit/export.json` | JSON | DDF audit export JSON |
| GET | `/ui/ddf/terminology/audit` | HTML | DDF audit UI page |
| GET | `/protocol/terminology` | JSON | Query Protocol terminology rows |
| GET | `/ui/protocol/terminology` | HTML | Protocol terminology UI page |
| GET | `/protocol/terminology/audit` | JSON | Protocol audit entries |
| GET | `/protocol/terminology/audit/export.csv` | CSV | Protocol audit export CSV |
| GET | `/protocol/terminology/audit/export.json` | JSON | Protocol audit export JSON |
| GET | `/ui/protocol/terminology/audit` | HTML | Protocol audit UI page |
| POST | `/soa` | JSON | Create study container |
| POST | `/soa/{soa_id}/metadata` | JSON | Update study metadata fields |
| POST | `/soa/{soa_id}/elements` | JSON | Create element |
| POST | `/soa/{soa_id}/elements/reorder` | JSON | Reorder elements |
| POST | `/soa/{soa_id}/arms` | JSON | Create arm (assigns `arm_uid`) |
| POST | `/soa/{soa_id}/arms/reorder` | JSON | Reorder arms |
| POST | `/soa/{soa_id}/visits` | JSON | Create visit |
| POST | `/soa/{soa_id}/visits/reorder` | JSON | Reorder visits |
| POST | `/soa/{soa_id}/activities` | JSON | Create activity |
| POST | `/soa/{soa_id}/activities/reorder` | JSON | Reorder activities |
| POST | `/soa/{soa_id}/epochs` | JSON | Create epoch |
| POST | `/soa/{soa_id}/epochs/reorder` | JSON | Reorder epochs |
| POST | `/soa/{soa_id}/epochs/{epoch_id}/metadata` | JSON | Update epoch metadata |
| POST | `/soa/{soa_id}/activities/{activity_id}/concepts` | JSON | Set concepts list |
| POST | `/soa/{soa_id}/activities/bulk` | JSON | Bulk create activities |
| POST | `/soa/{soa_id}/cells` | JSON | Upsert cell |
| POST | `/soa/{soa_id}/matrix/import` | JSON | Bulk matrix import |
| POST | `/ui/soa/create` | HTML | Form create study |
| POST | `/ui/soa/{soa_id}/update_meta` | HTML | Update metadata form |
| POST | `/ui/soa/{soa_id}/concepts_refresh` | HTML | Force concepts cache refresh |
| POST | `/ui/soa/{soa_id}/freeze` | HTML | Create freeze version |
| POST | `/ui/soa/{soa_id}/freeze/{freeze_id}/rollback` | HTML | Roll back to freeze |
| POST | `/ui/sdtm/specializations/refresh` | HTML | Force specializations refresh |
| POST | `/ui/soa/{soa_id}/add_visit` | HTML | Add visit form |
| POST | `/ui/soa/{soa_id}/add_arm` | HTML | Add arm form |
| POST | `/ui/soa/{soa_id}/update_arm` | HTML | Update arm form |
| POST | `/ui/soa/{soa_id}/delete_arm` | HTML | Delete arm form |
| POST | `/ui/soa/{soa_id}/reorder_arms` | HTML | Reorder arms form |
| POST | `/ui/soa/{soa_id}/add_element` | HTML | Add element form |
| POST | `/ui/soa/{soa_id}/update_element` | HTML | Update element form |
| POST | `/ui/soa/{soa_id}/delete_element` | HTML | Delete element form |
| POST | `/ui/soa/{soa_id}/reorder_elements` | HTML | Reorder elements form |
| POST | `/ui/soa/{soa_id}/add_activity` | HTML | Add activity form |
| POST | `/ui/soa/{soa_id}/add_epoch` | HTML | Add epoch form |
| POST | `/ui/soa/{soa_id}/update_epoch` | HTML | Update epoch form |
| POST | `/ui/soa/{soa_id}/set_cell` | HTML | Set cell (HTMX) |
| POST | `/ui/soa/{soa_id}/toggle_cell` | HTML | Toggle cell (HTMX) |
| POST | `/ui/soa/{soa_id}/delete_visit` | HTML | Delete visit form |
| POST | `/ui/soa/{soa_id}/set_visit_epoch` | HTML | Assign/clear visit epoch |
| POST | `/ui/soa/{soa_id}/delete_activity` | HTML | Delete activity form |
| POST | `/ui/soa/{soa_id}/delete_epoch` | HTML | Delete epoch form |
| POST | `/ui/soa/{soa_id}/reorder_visits` | HTML | Reorder visits form |
| POST | `/ui/soa/{soa_id}/reorder_activities` | HTML | Reorder activities form |
| POST | `/ui/soa/{soa_id}/reorder_epochs` | HTML | Reorder epochs form |
| POST | `/admin/load_ddf_terminology` | JSON | Reload DDF terminology sheet |
| POST | `/ui/ddf/terminology/upload` | HTML | Upload DDF terminology sheet |
| POST | `/admin/load_protocol_terminology` | JSON | Reload Protocol terminology sheet |
| POST | `/ui/protocol/terminology/upload` | HTML | Upload Protocol terminology sheet |
| PATCH | `/soa/{soa_id}/elements/{element_id}` | JSON | Partial update element |
| PATCH | `/soa/{soa_id}/arms/{arm_id}` | JSON | Partial update arm |
| PATCH | `/soa/{soa_id}/visits/{visit_id}` | JSON | Partial update visit |
| PATCH | `/soa/{soa_id}/activities/{activity_id}` | JSON | Partial update activity |
| DELETE | `/soa/{soa_id}/elements/{element_id}` | JSON | Delete element |
| DELETE | `/soa/{soa_id}/arms/{arm_id}` | JSON | Delete arm |
| DELETE | `/soa/{soa_id}/visits/{visit_id}` | JSON | Delete visit |
| DELETE | `/soa/{soa_id}/activities/{activity_id}` | JSON | Delete activity |
| DELETE | `/soa/{soa_id}/epochs/{epoch_id}` | JSON | Delete epoch |

If any endpoint returns a non-JSON payload (Excel, CSV, HTML), error responses still use the FastAPI JSON error model. Consider adding explicit OpenAPI tags & descriptions for improved generated docs.
| GET | `/soa/{soa_id}/matrix` | JSON | Raw matrix (visits/activities/cells) |
| POST | `/soa/{soa_id}/matrix/import` | JSON | Bulk import matrix |
| GET | `/soa/{soa_id}/export/xlsx` | Binary | Excel workbook export |
| POST | `/soa/{soa_id}/visits` | JSON | Create visit |
| PATCH | `/soa/{soa_id}/visits/{visit_id}` | JSON | Update visit |
| GET | `/soa/{soa_id}/visits/{visit_id}` | JSON | Visit detail |
| DELETE | `/soa/{soa_id}/visits/{visit_id}` | JSON | Delete visit |
| POST | `/soa/{soa_id}/activities` | JSON | Create activity |
| PATCH | `/soa/{soa_id}/activities/{activity_id}` | JSON | Update activity |
| GET | `/soa/{soa_id}/activities/{activity_id}` | JSON | Activity detail |
| DELETE | `/soa/{soa_id}/activities/{activity_id}` | JSON | Delete activity |
| POST | `/soa/{soa_id}/activities/{activity_id}/concepts` | JSON | Assign concepts to activity |
| POST | `/soa/{soa_id}/activities/bulk` | JSON | Bulk add activities |
| POST | `/soa/{soa_id}/activities/reorder` | JSON | Reorder activities (global audit) |
| POST | `/soa/{soa_id}/epochs` | JSON | Create epoch |
| GET | `/soa/{soa_id}/epochs` | JSON | List epochs |
| GET | `/soa/{soa_id}/epochs/{epoch_id}` | JSON | Epoch detail |
| POST | `/soa/{soa_id}/epochs/{epoch_id}/metadata` | JSON | Update epoch metadata |
| DELETE | `/soa/{soa_id}/epochs/{epoch_id}` | JSON | Delete epoch |
| POST | `/soa/{soa_id}/epochs/reorder` | JSON | Reorder epochs |
| GET | `/soa/{soa_id}/elements` | JSON | List elements |
| GET | `/soa/{soa_id}/elements/{element_id}` | JSON | Element detail |
| POST | `/soa/{soa_id}/elements` | JSON | Create element |
| PATCH | `/soa/{soa_id}/elements/{element_id}` | JSON | Update element |
| DELETE | `/soa/{soa_id}/elements/{element_id}` | JSON | Delete element |
| POST | `/soa/{soa_id}/elements/reorder` | JSON | Reorder elements |
| GET | `/soa/{soa_id}/element_audit` | JSON | Element audit log |
| GET | `/soa/{soa_id}/arms` | JSON | List arms |
| POST | `/soa/{soa_id}/arms` | JSON | Create arm |
| PATCH | `/soa/{soa_id}/arms/{arm_id}` | JSON | Update arm |
| DELETE | `/soa/{soa_id}/arms/{arm_id}` | JSON | Delete arm |
| POST | `/soa/{soa_id}/arms/reorder` | JSON | Reorder arms |
| GET | `/soa/{soa_id}/arm_audit` | JSON | Arm audit log |
| POST | `/soa/{soa_id}/visits/reorder` | JSON | Reorder visits |
| POST | `/soa/{soa_id}/activities/reorder` | JSON | Reorder activities |
| POST | `/soa/{soa_id}/epochs/reorder` | JSON | Reorder epochs |
| GET | `/soa/{soa_id}/rollback_audit` | JSON | Rollback audit log |
| GET | `/soa/{soa_id}/reorder_audit` | JSON | Global reorder audit log |
| GET | `/soa/{soa_id}/rollback_audit/export/xlsx` | Binary | Rollback audit Excel |
| GET | `/soa/{soa_id}/reorder_audit/export/xlsx` | Binary | Reorder audit Excel |
| GET | `/soa/{soa_id}/reorder_audit/export/csv` | CSV | Reorder audit CSV |
| POST | `/soa/{soa_id}/cells` | JSON | Upsert cell |
| GET | `/soa/{soa_id}/matrix` | JSON | (Duplicate listing for completeness) |
| GET | `/soa/{soa_id}/normalized` | JSON | (Duplicate listing for completeness) |
| POST | `/soa/{soa_id}/freeze/{freeze_id}/rollback` | HTML | Rollback via UI |
| GET | `/soa/{soa_id}/freeze/{freeze_id}` | JSON | Freeze snapshot |
| GET | `/ui/soa/{soa_id}/freeze/{freeze_id}/view` | HTML | Modal freeze view |
| GET | `/ui/soa/{soa_id}/freeze/diff` | HTML | Freeze diff view |
| GET | `/soa/{soa_id}/freeze/diff.json` | JSON | Freeze diff JSON |
| POST | `/ui/soa/{soa_id}/freeze` | HTML | Create freeze (form) |
| POST | `/ui/soa/{soa_id}/concepts_refresh` | HTML | Force concepts refresh |
| GET | `/ui/concepts` | HTML | Concepts list |
| GET | `/ui/concepts/{code}` | HTML | Concept detail |
| GET | `/ddf/terminology` | JSON | DDF terminology query |
| POST | `/admin/load_ddf_terminology` | JSON | Load DDF terminology |
| GET | `/ui/ddf/terminology` | HTML | DDF terminology UI |
| POST | `/ui/ddf/terminology/upload` | HTML | Upload DDF terminology |
| GET | `/ddf/terminology/audit` | JSON | DDF audit list |
| GET | `/ddf/terminology/audit/export.csv` | CSV | DDF audit CSV export |
| GET | `/ddf/terminology/audit/export.json` | JSON | DDF audit JSON export |
| GET | `/ui/ddf/terminology/audit` | HTML | DDF audit UI |
| GET | `/protocol/terminology` | JSON | Protocol terminology query |
| POST | `/admin/load_protocol_terminology` | JSON | Load Protocol terminology |
| GET | `/ui/protocol/terminology` | HTML | Protocol terminology UI |
| POST | `/ui/protocol/terminology/upload` | HTML | Upload Protocol terminology |
| GET | `/protocol/terminology/audit` | JSON | Protocol audit list |
| GET | `/protocol/terminology/audit/export.csv` | CSV | Protocol audit CSV export |
| GET | `/protocol/terminology/audit/export.json` | JSON | Protocol audit JSON export |
| GET | `/ui/protocol/terminology/audit` | HTML | Protocol audit UI |
| POST | `/ui/soa/create` | HTML | Create study (form) |
| POST | `/ui/soa/{soa_id}/update_meta` | HTML | Update metadata (form) |
| GET | `/ui/soa/{soa_id}/edit` | HTML | Editing interface |
| POST | `/ui/soa/{soa_id}/add_visit` | HTML | Add visit (form) |
| POST | `/ui/soa/{soa_id}/delete_visit` | HTML | Delete visit (HTMX) |
| POST | `/ui/soa/{soa_id}/reorder_visits` | HTML | Reorder visits (form) |
| POST | `/ui/soa/{soa_id}/set_visit_epoch` | HTML | Assign epoch to visit |
| POST | `/ui/soa/{soa_id}/add_activity` | HTML | Add activity (form) |
| POST | `/ui/soa/{soa_id}/delete_activity` | HTML | Delete activity (HTMX) |
| POST | `/ui/soa/{soa_id}/reorder_activities` | HTML | Reorder activities (form) |
| POST | `/ui/soa/{soa_id}/add_epoch` | HTML | Add epoch (form) |
| POST | `/ui/soa/{soa_id}/update_epoch` | HTML | Update epoch (form) |
| POST | `/ui/soa/{soa_id}/delete_epoch` | HTML | Delete epoch (form) |
| POST | `/ui/soa/{soa_id}/reorder_epochs` | HTML | Reorder epochs (form) |
| POST | `/ui/soa/{soa_id}/add_element` | HTML | Add element (form) |
| POST | `/ui/soa/{soa_id}/update_element` | HTML | Update element (form) |
| POST | `/ui/soa/{soa_id}/delete_element` | HTML | Delete element (form) |
| POST | `/ui/soa/{soa_id}/reorder_elements` | HTML | Reorder elements (form) |
| POST | `/ui/soa/{soa_id}/add_arm` | HTML | Add arm (form) |
| POST | `/ui/soa/{soa_id}/update_arm` | HTML | Update arm (form) |
| POST | `/ui/soa/{soa_id}/delete_arm` | HTML | Delete arm (form) |
| POST | `/ui/soa/{soa_id}/reorder_arms` | HTML | Reorder arms (form) |
| POST | `/ui/soa/{soa_id}/toggle_cell` | HTML | Toggle cell (HTMX) |
| POST | `/ui/soa/{soa_id}/set_cell` | HTML | Set cell status (HTMX) |

> Not Implemented Endpoints (listed earlier conceptually): per-entity JSON audit endpoints for visits, activities, and epochs (`/soa/{soa_id}/visit_audit`, `/soa/{soa_id}/activity_audit`, `/soa/{soa_id}/epoch_audit`) were described but are not present in code as of this generation.

