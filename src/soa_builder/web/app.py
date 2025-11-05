"""FastAPI web application for interactive Schedule of Activities creation.

Endpoints:
  POST /soa {name} -> create SOA container
  GET /soa/{id} -> summary
  POST /soa/{id}/visits {name, raw_header} -> add visit
  POST /soa/{id}/activities {name} -> add activity
  POST /soa/{id}/cells {visit_id, activity_id, status} -> set cell value
  GET /soa/{id}/matrix -> returns visits, activities, cells matrix
  GET /soa/{id}/normalized -> run normalization pipeline and return summary

Data persisted in SQLite (file: soa_builder_web.db by default).
"""

from __future__ import annotations

PAGE_SIZE_ACTIVITIES = 10
import os, sqlite3, csv, tempfile, json
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
import io
import pandas as pd
from ..normalization import normalize_soa
import requests, time, logging
from dotenv import load_dotenv

load_dotenv()  # must come BEFORE reading env-based configuration so values are populated
DB_PATH = os.environ.get("SOA_BUILDER_DB", "soa_builder_web.db")
NORMALIZED_ROOT = os.environ.get("SOA_BUILDER_NORMALIZED_ROOT", "normalized")


def _get_cdisc_api_key():
    return os.environ.get("CDISC_API_KEY")


def _get_concepts_override():
    return os.environ.get("CDISC_CONCEPTS_JSON")


_concept_cache = {"data": None, "fetched_at": 0}
_CONCEPT_CACHE_TTL = 60 * 60  # 1 hour TTL
app = FastAPI(title="SoA Builder API", version="0.1.0")
logger = logging.getLogger("soa_builder.concepts")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# --------------------- DB bootstrap ---------------------


def _connect():
    return sqlite3.connect(DB_PATH)


def _init_db():
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS soa (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, created_at TEXT)"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS visit (id INTEGER PRIMARY KEY AUTOINCREMENT, soa_id INTEGER, name TEXT, raw_header TEXT, order_index INTEGER)"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, soa_id INTEGER, name TEXT, order_index INTEGER)"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS cell (id INTEGER PRIMARY KEY AUTOINCREMENT, soa_id INTEGER, visit_id INTEGER, activity_id INTEGER, status TEXT)"""
    )
    # Mapping table linking activities to biomedical concepts (concept_code + title stored for snapshot purposes)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS activity_concept (id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id INTEGER, concept_code TEXT, concept_title TEXT)"""
    )
    conn.commit()
    conn.close()


_init_db()

# --------------------- Migrations ---------------------

def _drop_unused_override_table():
    """Drop legacy activity_concept_override table if it still exists.
    This table supported mutable concept titles which are no longer allowed.
    Safe to run repeatedly; will no-op if table absent."""
    try:
        conn = _connect(); cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_concept_override'")
        if cur.fetchone():
            try:
                cur.execute("DROP TABLE activity_concept_override")
                conn.commit()
                logger.info("Dropped obsolete table activity_concept_override")
            except Exception as e:
                logger.warning("Failed to drop obsolete table activity_concept_override: %s", e)
        conn.close()
    except Exception as e:
        logger.warning("Migration check for activity_concept_override failed: %s", e)

_drop_unused_override_table()

# --------------------- Models ---------------------


class SOACreate(BaseModel):
    name: str


class VisitCreate(BaseModel):
    name: str
    raw_header: Optional[str] = None


class ActivityCreate(BaseModel):
    name: str


class ConceptsUpdate(BaseModel):
    concept_codes: List[str]


class CellCreate(BaseModel):
    visit_id: int
    activity_id: int
    status: str


class BulkActivities(BaseModel):
    names: List[str]


class MatrixVisit(BaseModel):
    name: str
    raw_header: Optional[str] = None


class MatrixActivity(BaseModel):
    name: str
    statuses: List[str]


class MatrixImport(BaseModel):
    visits: List[MatrixVisit]
    activities: List[MatrixActivity]
    reset: bool = True


# --------------------- Helpers ---------------------


def _soa_exists(soa_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM soa WHERE id=?", (soa_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def _fetch_matrix(soa_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,raw_header,order_index FROM visit WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    visits = [
        dict(id=r[0], name=r[1], raw_header=r[2], order_index=r[3])
        for r in cur.fetchall()
    ]
    cur.execute(
        "SELECT id,name,order_index FROM activity WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    activities = [dict(id=r[0], name=r[1], order_index=r[2]) for r in cur.fetchall()]
    cur.execute(
        "SELECT visit_id, activity_id, status FROM cell WHERE soa_id=?", (soa_id,)
    )
    cells = [dict(visit_id=r[0], activity_id=r[1], status=r[2]) for r in cur.fetchall()]
    conn.close()
    return visits, activities, cells


def fetch_biomedical_concepts(force: bool = False):
    """Return list of biomedical concepts as [{'code':..., 'title':...}].
    Precedence: CDISC_CONCEPTS_JSON env override (for tests/offline) > cached remote fetch > empty list.
    Remote fetch uses CDISC_API_KEY header if present. Caches for TTL duration.
    """
    now = time.time()
    if (
        not force
        and _concept_cache["data"]
        and now - _concept_cache["fetched_at"] < _CONCEPT_CACHE_TTL
    ):
        return _concept_cache["data"]
    # Environment override
    override_json = _get_concepts_override()
    if override_json:
        try:
            raw = json.loads(override_json)
            items = (
                raw.get("items") if isinstance(raw, dict) and "items" in raw else raw
            )
            concepts = []
            for it in items:
                code = (
                    it.get("concept_code")
                    or it.get("code")
                    or it.get("conceptId")
                    or it.get("id")
                    or it.get("identifier")
                )
                title = it.get("title") or it.get("name") or it.get("label") or code
                if code:
                    concepts.append({"code": str(code), "title": str(title)})
            concepts.sort(key=lambda c: c["title"].lower())
            _concept_cache.update(data=concepts, fetched_at=now)
            logger.info("Loaded %d concepts from env override", len(concepts))
            return concepts
        except Exception:
            pass
    # Remote
    if os.environ.get("CDISC_SKIP_REMOTE") == "1":
        _concept_cache.update(data=[], fetched_at=now)
        logger.warning("CDISC_SKIP_REMOTE=1; concept list empty")
        return []
    url = "https://api.library.cdisc.org/api/cosmos/v2/mdr/bc/biomedicalconcepts"
    headers = {"Accept": "application/json"}
    api_key = _get_cdisc_api_key()
    if api_key:
        headers["api-key"] = api_key  # primary documented header
        # also include Authorization variant in case gateway expects it
        headers["Authorization"] = f"ApiKey {api_key}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        _concept_cache["last_status"] = resp.status_code
        _concept_cache["last_url"] = url
        _concept_cache["last_error"] = None
        _concept_cache["raw_snippet"] = resp.text[:400]
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                # Not JSON, likely HTML error despite 200
                _concept_cache["last_error"] = "200 but non-JSON response"
                logger.error(
                    "Concept fetch 200 but non-JSON body (snippet: %s)", resp.text[:200]
                )
                return []

            # If JSON is a string, attempt second decode
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    _concept_cache["last_error"] = (
                        "JSON value was a raw string; secondary parse failed"
                    )
                    logger.error(
                        "Concept fetch raw string JSON secondary parse failed (snippet: %s)",
                        str(data)[:200],
                    )
                    return []

            # Normalize possible shapes
            # Primary shapes: list of concept objects, dict with 'items', or HAL-style _links
            if (
                isinstance(data, dict)
                and "items" in data
                and isinstance(data["items"], list)
            ):
                items = data["items"]
            elif (
                isinstance(data, dict)
                and "_links" in data
                and isinstance(data["_links"], dict)
            ):
                # Extract from biomedicalConcepts links list
                links_list = data["_links"].get("biomedicalConcepts") or []
                items = []
                for link in links_list:
                    if not isinstance(link, dict):
                        continue
                    href = link.get("href")
                    title = link.get("title") or href
                    # Concept code may be last path segment
                    code = None
                    if href:
                        code = href.strip("/").split("/")[-1]
                    if code:
                        items.append({"concept_code": code, "title": title})
            elif isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                # single concept object
                items = [data]
            else:
                _concept_cache["last_error"] = (
                    f"Unexpected JSON root type: {type(data).__name__}"
                )
                logger.error("Concept fetch unexpected JSON root type: %s", type(data))
                return []

            concepts = []
            for it in items:
                if not isinstance(it, dict):
                    continue  # skip non-dict entries
                code = (
                    it.get("concept_code")
                    or it.get("code")
                    or it.get("conceptId")
                    or it.get("id")
                    or it.get("identifier")
                )
                title = it.get("title") or it.get("name") or it.get("label") or code
                if code:
                    concepts.append({"code": str(code), "title": str(title)})
            concepts.sort(key=lambda c: c["title"].lower())
            _concept_cache.update(data=concepts, fetched_at=now)
            logger.info("Fetched %d concepts from remote API", len(concepts))
            return concepts
        else:
            _concept_cache["last_error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        logger.error("Concept fetch error: %s", e)
        _concept_cache["last_error"] = str(e)
    _concept_cache.update(data=[], fetched_at=now)
    logger.warning("Concept list empty after fetch attempts")
    return []


@app.on_event("startup")
def preload_concepts():  # pragma: no cover (covered indirectly via tests reload)
    try:
        concepts = fetch_biomedical_concepts(force=True)
        logger.info("Startup preload concepts count=%d", len(concepts))
    except Exception as e:
        logger.error("Startup concept preload failed: %s", e)


@app.post("/ui/soa/{soa_id}/concepts_refresh", response_class=HTMLResponse)
def ui_refresh_concepts(request: Request, soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    fetch_biomedical_concepts(force=True)
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.get("/concepts/status")
def concepts_status():
    return {
        "count": len(_concept_cache.get("data") or []),
        "fetched_at": _concept_cache.get("fetched_at"),
        "cache_age_sec": (
            (time.time() - _concept_cache.get("fetched_at", 0))
            if _concept_cache.get("fetched_at")
            else None
        ),
        "last_status": _concept_cache.get("last_status"),
        "last_error": _concept_cache.get("last_error"),
        "raw_snippet": _concept_cache.get("raw_snippet"),
        "api_key_present": bool(_get_cdisc_api_key()),
        "override_present": bool(_get_concepts_override()),
        "skip_remote": os.environ.get("CDISC_SKIP_REMOTE") == "1",
    }


def _wide_csv_path(soa_id: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"soa_{soa_id}_wide.csv")


def _generate_wide_csv(soa_id: int) -> str:
    visits, activities, cells = _fetch_matrix(soa_id)
    if not visits or not activities:
        raise ValueError(
            "Cannot generate CSV: need at least one visit and one activity"
        )
    # Build matrix with first column Activity, subsequent visit headers using raw_header or name
    visit_headers = [v["raw_header"] or v["name"] for v in visits]
    matrix = []
    for a in activities:
        row = [a["name"]]
        for v in visits:
            match = next(
                (
                    c["status"]
                    for c in cells
                    if c["visit_id"] == v["id"] and c["activity_id"] == a["id"]
                ),
                "",
            )
            row.append(match)
        matrix.append(row)
    path = _wide_csv_path(soa_id)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Activity"] + visit_headers)
        writer.writerows(matrix)
    return path


def _matrix_arrays(soa_id: int):
    """Return visit headers list and rows (activity name + statuses)."""
    visits, activities, cells = _fetch_matrix(soa_id)
    visit_headers = [v["raw_header"] or v["name"] for v in visits]
    cell_lookup = {(c["visit_id"], c["activity_id"]): c["status"] for c in cells}
    rows = []
    for a in activities:
        row = [a["name"]]
        for v in visits:
            row.append(cell_lookup.get((v["id"], a["id"]), ""))
        rows.append(row)
    return visit_headers, rows


# --------------------- API Endpoints ---------------------


@app.post("/soa")
def create_soa(payload: SOACreate):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO soa (name, created_at) VALUES (?, ?)",
        (payload.name, datetime.now(timezone.utc).isoformat()),
    )
    soa_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"id": soa_id, "name": payload.name}


@app.get("/soa/{soa_id}")
def get_soa(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    visits, activities, cells = _fetch_matrix(soa_id)
    return {"id": soa_id, "visits": visits, "activities": activities, "cells": cells}


@app.post("/soa/{soa_id}/visits")
def add_visit(soa_id: int, payload: VisitCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM visit WHERE soa_id=?", (soa_id,))
    order_index = cur.fetchone()[0] + 1
    cur.execute(
        "INSERT INTO visit (soa_id,name,raw_header,order_index) VALUES (?,?,?,?)",
        (soa_id, payload.name, payload.raw_header or payload.name, order_index),
    )
    vid = cur.lastrowid
    conn.commit()
    conn.close()
    return {"visit_id": vid, "order_index": order_index}


@app.post("/soa/{soa_id}/activities")
def add_activity(soa_id: int, payload: ActivityCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM activity WHERE soa_id=?", (soa_id,))
    order_index = cur.fetchone()[0] + 1
    cur.execute(
        "INSERT INTO activity (soa_id,name,order_index) VALUES (?,?,?)",
        (soa_id, payload.name, order_index),
    )
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    return {"activity_id": aid, "order_index": order_index}


@app.post("/soa/{soa_id}/activities/{activity_id}/concepts")
def set_activity_concepts(soa_id: int, activity_id: int, payload: ConceptsUpdate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM activity WHERE id=? AND soa_id=?", (activity_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Activity not found")
    # Clear existing mappings
    cur.execute("DELETE FROM activity_concept WHERE activity_id=?", (activity_id,))
    concepts = fetch_biomedical_concepts()
    lookup = {c["code"]: c["title"] for c in concepts}
    inserted = 0
    for code in payload.concept_codes:
        ccode = code.strip()
        if not ccode:
            continue
        title = lookup.get(ccode, ccode)
        cur.execute(
            "INSERT INTO activity_concept (activity_id, concept_code, concept_title) VALUES (?,?,?)",
            (activity_id, ccode, title),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {"activity_id": activity_id, "concepts_set": inserted}


def _get_activity_concepts(activity_id: int):
    """Return list of concepts (immutable: stored snapshot)."""
    conn = _connect(); cur = conn.cursor()
    cur.execute("SELECT concept_code, concept_title FROM activity_concept WHERE activity_id=?", (activity_id,))
    rows = [{"code": c, "title": t} for c, t in cur.fetchall()]
    conn.close()
    return rows


@app.post("/ui/soa/{soa_id}/activity/{activity_id}/concepts/add", response_class=HTMLResponse)
def ui_add_activity_concept(request: Request, soa_id: int, activity_id: int, concept_code: str = Form(...)):
    if not activity_id:
        raise HTTPException(400, "Missing activity_id")
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    code = concept_code.strip()
    if not code:
        raise HTTPException(400, "Empty concept_code")
    concepts = fetch_biomedical_concepts()
    lookup = {c["code"]: c["title"] for c in concepts}
    title = lookup.get(code, code)
    conn = _connect(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM activity WHERE id=? AND soa_id=?", (activity_id, soa_id))
    if not cur.fetchone():
        conn.close(); raise HTTPException(404, "Activity not found")
    cur.execute("SELECT 1 FROM activity_concept WHERE activity_id=? AND concept_code=?", (activity_id, code))
    if not cur.fetchone():
        cur.execute("INSERT INTO activity_concept (activity_id, concept_code, concept_title) VALUES (?,?,?)", (activity_id, code, title))
        conn.commit()
    conn.close()
    selected = _get_activity_concepts(activity_id)
    html = templates.get_template("concepts_cell.html").render(
        request=request,
        soa_id=soa_id,
        activity_id=activity_id,
        concepts=concepts,
        selected_codes=[s["code"] for s in selected],
        selected_list=selected,
        edit=False,
    )
    return HTMLResponse(html)


@app.post("/ui/soa/{soa_id}/activity/{activity_id}/concepts/remove", response_class=HTMLResponse)
def ui_remove_activity_concept(request: Request, soa_id: int, activity_id: int, concept_code: str = Form(...)):
    if not activity_id:
        raise HTTPException(400, "Missing activity_id")
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    code = concept_code.strip()
    if not code:
        raise HTTPException(400, "Empty concept_code")
    conn = _connect(); cur = conn.cursor()
    cur.execute("DELETE FROM activity_concept WHERE activity_id=? AND concept_code=?", (activity_id, code))
    conn.commit(); conn.close()
    concepts = fetch_biomedical_concepts(); selected = _get_activity_concepts(activity_id)
    html = templates.get_template("concepts_cell.html").render(
        request=request,
        soa_id=soa_id,
        activity_id=activity_id,
        concepts=concepts,
        selected_codes=[s["code"] for s in selected],
        selected_list=selected,
        edit=False,
    )
    return HTMLResponse(html)




@app.post("/soa/{soa_id}/activities/bulk")
def add_activities_bulk(soa_id: int, payload: BulkActivities):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    names = [n.strip() for n in payload.names if n and n.strip()]
    if not names:
        return {"added": 0, "skipped": 0, "details": []}
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT name FROM activity WHERE soa_id=?", (soa_id,))
    existing = set(r[0].lower() for r in cur.fetchall())
    added = []
    skipped = []
    # get current count for order_index start
    cur.execute("SELECT COUNT(*) FROM activity WHERE soa_id=?", (soa_id,))
    count = cur.fetchone()[0]
    order_index = count
    for name in names:
        lname = name.lower()
        if lname in existing:
            skipped.append(name)
            continue
        order_index += 1
        cur.execute(
            "INSERT INTO activity (soa_id,name,order_index) VALUES (?,?,?)",
            (soa_id, name, order_index),
        )
        added.append(name)
        existing.add(lname)
    conn.commit()
    conn.close()
    return {
        "added": len(added),
        "skipped": len(skipped),
        "details": {"added": added, "skipped": skipped},
    }


@app.post("/soa/{soa_id}/cells")
def set_cell(soa_id: int, payload: CellCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    # Upsert semantics: find existing
    cur.execute(
        "SELECT id FROM cell WHERE soa_id=? AND visit_id=? AND activity_id=?",
        (soa_id, payload.visit_id, payload.activity_id),
    )
    row = cur.fetchone()
    # If blank status => delete existing cell (clear) and do not create new row
    if payload.status.strip() == "":
        if row:
            cur.execute("DELETE FROM cell WHERE id=?", (row[0],))
            cid = row[0]
            conn.commit()
            conn.close()
            return {"cell_id": cid, "status": "", "deleted": True}
        conn.close()
        return {"cell_id": None, "status": "", "deleted": False}
    if row:
        cur.execute("UPDATE cell SET status=? WHERE id=?", (payload.status, row[0]))
        cid = row[0]
    else:
        cur.execute(
            "INSERT INTO cell (soa_id, visit_id, activity_id, status) VALUES (?,?,?,?)",
            (soa_id, payload.visit_id, payload.activity_id, payload.status),
        )
        cid = cur.lastrowid
    conn.commit()
    conn.close()
    return {"cell_id": cid, "status": payload.status}


@app.get("/soa/{soa_id}/matrix")
def get_matrix(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    visits, activities, cells = _fetch_matrix(soa_id)
    return {"visits": visits, "activities": activities, "cells": cells}


@app.get("/soa/{soa_id}/export/xlsx")
def export_xlsx(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    visits, activities, cells = _fetch_matrix(soa_id)
    if not visits or not activities:
        raise HTTPException(
            400, "Cannot export empty matrix (need visits and activities)"
        )
    headers, rows = _matrix_arrays(soa_id)
    # Build DataFrame, then inject Concepts column (second position)
    df = pd.DataFrame(rows, columns=["Activity"] + headers)
    # Fetch concepts only (immutable snapshot titles)
    conn = _connect(); cur = conn.cursor()
    cur.execute("SELECT activity_id, concept_code, concept_title FROM activity_concept")
    concepts_map = {}
    for aid, code, title in cur.fetchall():
        concepts_map.setdefault(aid, {})[code] = title
    conn.close()
    visits, activities, _cells = _fetch_matrix(soa_id)
    activity_ids_in_order = [a['id'] for a in activities]
    # Build display strings using EffectiveTitle (override if present) and show code in parentheses
    concepts_strings = []
    for aid in activity_ids_in_order:
        cmap = concepts_map.get(aid, {})
        if not cmap:
            concepts_strings.append("")
            continue
        items = sorted(cmap.items(), key=lambda kv: kv[1].lower())
        concepts_strings.append("; ".join([f"{title} ({code})" for code, title in items]))
    if len(concepts_strings) == len(df):
        df.insert(1, "Concepts", concepts_strings)
    # Build concept mappings sheet data
    mapping_rows = []
    for a in activities:
        aid = a['id']; cmap = concepts_map.get(aid, {})
        for code, title in cmap.items():
            mapping_rows.append([aid, a['name'], code, title])
    mapping_df = pd.DataFrame(mapping_rows, columns=["ActivityID","ActivityName","ConceptCode","ConceptTitle"]) 
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="SoA")
        mapping_df.to_excel(writer, index=False, sheet_name="ConceptMappings")
    bio.seek(0)
    filename = f"soa_{soa_id}_matrix.xlsx"
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )




@app.get("/soa/{soa_id}/normalized")
def get_normalized(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    csv_path = _generate_wide_csv(soa_id)
    out_dir = os.path.join(NORMALIZED_ROOT, f"soa_{soa_id}")
    os.makedirs(out_dir, exist_ok=True)
    summary = normalize_soa(
        csv_path, out_dir, sqlite_path=os.path.join(out_dir, "soa.db")
    )
    return {"summary": summary, "artifacts_dir": out_dir}


@app.post("/soa/{soa_id}/matrix/import")
def import_matrix(soa_id: int, payload: MatrixImport):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    if not payload.visits:
        raise HTTPException(400, "visits list empty")
    if not payload.activities:
        raise HTTPException(400, "activities list empty")
    visit_count = len(payload.visits)
    # Validate statuses length for each activity
    for act in payload.activities:
        if len(act.statuses) != visit_count:
            raise HTTPException(
                400,
                f"Activity '{act.name}' statuses length {len(act.statuses)} != visits length {visit_count}",
            )
    conn = _connect()
    cur = conn.cursor()
    if payload.reset:
        cur.execute("DELETE FROM cell WHERE soa_id=?", (soa_id,))
        cur.execute("DELETE FROM visit WHERE soa_id=?", (soa_id,))
        cur.execute("DELETE FROM activity WHERE soa_id=?", (soa_id,))
    # Insert visits respecting order
    cur.execute("SELECT COUNT(*) FROM visit WHERE soa_id=?", (soa_id,))
    vstart = cur.fetchone()[0]
    v_index = vstart
    visit_id_map = []
    for v in payload.visits:
        v_index += 1
        cur.execute(
            "INSERT INTO visit (soa_id,name,raw_header,order_index) VALUES (?,?,?,?)",
            (soa_id, v.name, v.raw_header or v.name, v_index),
        )
        visit_id_map.append(cur.lastrowid)
    # Insert activities
    cur.execute("SELECT COUNT(*) FROM activity WHERE soa_id=?", (soa_id,))
    astart = cur.fetchone()[0]
    a_index = astart
    activity_id_map = []
    for a in payload.activities:
        a_index += 1
        cur.execute(
            "INSERT INTO activity (soa_id,name,order_index) VALUES (?,?,?)",
            (soa_id, a.name, a_index),
        )
        activity_id_map.append(cur.lastrowid)
    # Insert cells
    for a_idx, a in enumerate(payload.activities):
        aid = activity_id_map[a_idx]
        for v_idx, status in enumerate(a.statuses):
            if status is None:
                status = ""
            status_str = str(status).strip()
            if status_str == "":
                continue
            vid = visit_id_map[v_idx]
            cur.execute(
                "INSERT INTO cell (soa_id, visit_id, activity_id, status) VALUES (?,?,?,?)",
                (soa_id, vid, aid, status_str),
            )
    conn.commit()
    conn.close()
    return {
        "visits_added": len(payload.visits),
        "activities_added": len(payload.activities),
        "cells_inserted": sum(
            1 for a in payload.activities for s in a.statuses if str(s).strip() != ""
        ),
    }


# --------------------- Deletion API Endpoints ---------------------


def _reindex(table: str, soa_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        f"SELECT id FROM {table} WHERE soa_id=? ORDER BY order_index", (soa_id,)
    )
    ids = [r[0] for r in cur.fetchall()]
    for idx, _id in enumerate(ids, start=1):
        cur.execute(f"UPDATE {table} SET order_index=? WHERE id=?", (idx, _id))
    conn.commit()
    conn.close()


@app.delete("/soa/{soa_id}/visits/{visit_id}")
def delete_visit(soa_id: int, visit_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM visit WHERE id=? AND soa_id=?", (visit_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Visit not found")
    # cascade cells
    cur.execute("DELETE FROM cell WHERE soa_id=? AND visit_id=?", (soa_id, visit_id))
    cur.execute("DELETE FROM visit WHERE id=?", (visit_id,))
    conn.commit()
    conn.close()
    _reindex("visit", soa_id)
    return {"deleted_visit_id": visit_id}


@app.delete("/soa/{soa_id}/activities/{activity_id}")
def delete_activity(soa_id: int, activity_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM activity WHERE id=? AND soa_id=?", (activity_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Activity not found")
    cur.execute(
        "DELETE FROM cell WHERE soa_id=? AND activity_id=?", (soa_id, activity_id)
    )
    cur.execute("DELETE FROM activity WHERE id=?", (activity_id,))
    conn.commit()
    conn.close()
    _reindex("activity", soa_id)
    return {"deleted_activity_id": activity_id}


# --------------------- HTML UI Endpoints ---------------------


@app.get("/", response_class=HTMLResponse)
def ui_index(request: Request):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id,name,created_at FROM soa ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "soas": [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows],
        },
    )


@app.post("/ui/soa/create", response_class=HTMLResponse)
def ui_create_soa(request: Request, name: str = Form(...)):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO soa (name, created_at) VALUES (?,?)",
        (name, datetime.now(timezone.utc).isoformat()),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return HTMLResponse(f"<script>window.location='/ui/soa/{sid}/edit';</script>")


@app.get("/ui/soa/{soa_id}/edit", response_class=HTMLResponse)
def ui_edit(request: Request, soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    visits, activities, cells = _fetch_matrix(soa_id)
    # No pagination: use all activities
    activities_page = activities
    # Build cell lookup
    cell_map = {(c["visit_id"], c["activity_id"]): c["status"] for c in cells}
    concepts = fetch_biomedical_concepts()
    activity_ids = [a["id"] for a in activities_page]
    activity_concepts = {}
    if activity_ids:
        conn = _connect()
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in activity_ids)
        cur.execute(
            f"SELECT activity_id, concept_code, concept_title FROM activity_concept WHERE activity_id IN ({placeholders})",
            activity_ids,
        )
        for aid, code, title in cur.fetchall():
            activity_concepts.setdefault(aid, []).append({"code": code, "title": title})
        conn.close()
    concepts_diag = {
        "count": len(_concept_cache.get("data") or []),
        "last_status": _concept_cache.get("last_status"),
        "last_error": _concept_cache.get("last_error"),
        "api_key_present": bool(_get_cdisc_api_key()),
        "override_present": bool(_get_concepts_override()),
        "skip_remote": os.environ.get("CDISC_SKIP_REMOTE") == "1",
    }
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "soa_id": soa_id,
            "visits": visits,
            "activities": activities_page,
            "cell_map": cell_map,
            "concepts": concepts,
            "activity_concepts": activity_concepts,
            "concepts_empty": len(concepts) == 0,
            "concepts_diag": concepts_diag,
        },
    )


@app.post("/ui/soa/{soa_id}/add_visit", response_class=HTMLResponse)
def ui_add_visit(
    request: Request, soa_id: int, name: str = Form(...), raw_header: str = Form("")
):
    add_visit(soa_id, VisitCreate(name=name, raw_header=raw_header or name))
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/add_activity", response_class=HTMLResponse)
def ui_add_activity(request: Request, soa_id: int, name: str = Form(...)):
    add_activity(soa_id, ActivityCreate(name=name))
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/activity/{activity_id}/concepts", response_class=HTMLResponse)
def ui_set_activity_concepts(
    request: Request,
    soa_id: int,
    activity_id: int,
    concept_codes: List[str] = Form([]),
):
    payload = ConceptsUpdate(concept_codes=list(dict.fromkeys(concept_codes)))
    set_activity_concepts(soa_id, activity_id, payload)
    # HTMX inline update support
    if request.headers.get("HX-Request") == "true":
        concepts = fetch_biomedical_concepts()
        conn = _connect(); cur = conn.cursor()
        cur.execute("SELECT concept_code, concept_title FROM activity_concept WHERE activity_id=?", (activity_id,))
        selected = [{"code": c, "title": t} for c, t in cur.fetchall()]
        conn.close()
        html = templates.get_template("concepts_cell.html").render(
            request=request,
            soa_id=soa_id,
            activity_id=activity_id,
            concepts=concepts,
            selected_codes=[s["code"] for s in selected],
            selected_list=selected,
            edit=False,
        )
        return HTMLResponse(html)
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")

@app.get("/ui/soa/{soa_id}/activity/{activity_id}/concepts_cell", response_class=HTMLResponse)
def ui_activity_concepts_cell(request: Request, soa_id: int, activity_id: int, edit: int = 0):
    # Defensive guard: if activity_id is somehow falsy (should not happen for valid int path param)
    # surface a clear 400 error rather than proceeding and causing confusing downstream behavior.
    if not activity_id:
        raise HTTPException(status_code=400, detail="Missing activity_id")
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    concepts = fetch_biomedical_concepts()
    conn = _connect(); cur = conn.cursor()
    cur.execute("SELECT concept_code, concept_title FROM activity_concept WHERE activity_id=?", (activity_id,))
    selected = [{"code": c, "title": t} for c, t in cur.fetchall()]
    conn.close()
    return HTMLResponse(templates.get_template("concepts_cell.html").render(
        request=request,
        soa_id=soa_id,
        activity_id=activity_id,
        concepts=concepts,
        selected_codes=[s["code"] for s in selected],
        selected_list=selected,
        edit=bool(edit),
    ))


@app.post("/ui/soa/{soa_id}/set_cell", response_class=HTMLResponse)
def ui_set_cell(
    request: Request,
    soa_id: int,
    visit_id: int = Form(...),
    activity_id: int = Form(...),
    status: str = Form("X"),
):
    result = set_cell(
        soa_id, CellCreate(visit_id=visit_id, activity_id=activity_id, status=status)
    )
    return HTMLResponse(result.get("status", ""))


@app.post("/ui/soa/{soa_id}/toggle_cell", response_class=HTMLResponse)
def ui_toggle_cell(
    request: Request,
    soa_id: int,
    visit_id: int = Form(...),
    activity_id: int = Form(...),
):
    """Toggle logic: blank -> X, X -> blank (delete row). Returns updated <td> snippet with next action encoded.
    This avoids stale hx-vals attributes after a partial swap."""
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    # Determine current status
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT status,id FROM cell WHERE soa_id=? AND visit_id=? AND activity_id=?",
        (soa_id, visit_id, activity_id),
    )
    row = cur.fetchone()
    if row and row[0] == "X":
        # clear
        cur.execute("DELETE FROM cell WHERE id=?", (row[1],))
        conn.commit()
        conn.close()
        current = ""
    elif row:
        # Any non-blank treated as blank visually, remove
        cur.execute("DELETE FROM cell WHERE id=?", (row[1],))
        conn.commit()
        conn.close()
        current = ""
    else:
        # create X
        cur.execute(
            "INSERT INTO cell (soa_id, visit_id, activity_id, status) VALUES (?,?,?,?)",
            (soa_id, visit_id, activity_id, "X"),
        )
        conn.commit()
        conn.close()
        current = "X"
    # Next status (for hx-vals) depends on current
    next_status = "X" if current == "" else ""
    cell_html = f'<td hx-post="/ui/soa/{soa_id}/toggle_cell" hx-vals=\'{{"visit_id": {visit_id}, "activity_id": {activity_id}}}\' hx-swap="outerHTML" class="cell">{current}</td>'
    return HTMLResponse(cell_html)


@app.post("/ui/soa/{soa_id}/delete_visit", response_class=HTMLResponse)
def ui_delete_visit(request: Request, soa_id: int, visit_id: int = Form(...)):
    delete_visit(soa_id, visit_id)
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/delete_activity", response_class=HTMLResponse)
def ui_delete_activity(request: Request, soa_id: int, activity_id: int = Form(...)):
    delete_activity(soa_id, activity_id)
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


# --------------------- Entry ---------------------


def main():  # pragma: no cover
    import uvicorn

    uvicorn.run("soa_builder.web.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":  # pragma: no cover
    main()
