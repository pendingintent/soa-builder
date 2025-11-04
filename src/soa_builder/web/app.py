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
import os, sqlite3, csv, tempfile, json
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from ..normalization import normalize_soa

DB_PATH = os.environ.get("SOA_BUILDER_DB", "soa_builder_web.db")
NORMALIZED_ROOT = os.environ.get("SOA_BUILDER_NORMALIZED_ROOT", "normalized")

app = FastAPI(title="SoA Builder API", version="0.1.0")
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
    conn.commit()
    conn.close()


_init_db()

# --------------------- Models ---------------------


class SOACreate(BaseModel):
    name: str


class VisitCreate(BaseModel):
    name: str
    raw_header: Optional[str] = None


class ActivityCreate(BaseModel):
    name: str


class CellCreate(BaseModel):
    visit_id: int
    activity_id: int
    status: str


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
    # Build cell lookup
    cell_map = {(c["visit_id"], c["activity_id"]): c["status"] for c in cells}
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "soa_id": soa_id,
            "visits": visits,
            "activities": activities,
            "cell_map": cell_map,
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


@app.post("/ui/soa/{soa_id}/set_cell", response_class=HTMLResponse)
def ui_set_cell(
    request: Request,
    soa_id: int,
    visit_id: int = Form(...),
    activity_id: int = Form(...),
    status: str = Form("X"),
):
    set_cell(
        soa_id, CellCreate(visit_id=visit_id, activity_id=activity_id, status=status)
    )
    return HTMLResponse(status)


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
