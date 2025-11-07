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
List Elements      GET  /soa/{id}/elements
Create Element     POST /soa/{id}/elements
Update Element     PATCH /soa/{id}/elements/{element_id}
Reorder Elements   POST /soa/{id}/elements/reorder   (JSON array)
Reorder Visits     POST /soa/{id}/visits/reorder     (JSON array)
Reorder Activities POST /soa/{id}/activities/reorder (JSON array)
Reorder Epochs     POST /soa/{id}/epochs/reorder     (JSON array)
Freeze Version     POST /ui/soa/{id}/freeze (form)
Rollback           POST /ui/soa/{id}/freeze/{freeze_id}/rollback
Element Audit      GET  /soa/{id}/element_audit
Rollback Audit     GET  /soa/{id}/rollback_audit
Reorder Audit      GET  /soa/{id}/reorder_audit
Export Excel       GET  /soa/{id}/export/xlsx
Normalized View    GET  /soa/{id}/normalized
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
Generated on: 2025-11-07
