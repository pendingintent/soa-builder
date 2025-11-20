import json
import os
import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

DB_PATH = os.environ.get("SOA_BUILDER_DB", "soa_builder_web.db")
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter()


def _connect():
    return sqlite3.connect(DB_PATH)


def _soa_exists(soa_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM soa WHERE id=?", (soa_id,))
    r = cur.fetchone()
    conn.close()
    return r is not None


# Dynamic helper imports inside endpoint bodies avoid circular import at module load.


@router.post("/ui/soa/{soa_id}/freeze", response_class=HTMLResponse)
def ui_freeze_soa(request: Request, soa_id: int, version_label: str = Form("")):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    try:
        from ..app import _create_freeze  # type: ignore

        _fid, _vlabel = _create_freeze(soa_id, version_label or None)
    except HTTPException as he:
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse(
                f"<div class='error' style='color:#c62828;font-size:0.7em;'>Error: {he.detail}</div>"
            )
        return HTMLResponse(
            f"<script>alert('Error: {he.detail}');window.location='/ui/soa/{soa_id}/edit';</script>"
        )
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": f"/ui/soa/{soa_id}/edit"})
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@router.get("/soa/{soa_id}/freeze/{freeze_id}")
def get_freeze(soa_id: int, freeze_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT snapshot_json FROM soa_freeze WHERE id=? AND soa_id=?",
        (freeze_id, soa_id),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Freeze not found")
    try:
        data = json.loads(row[0])
    except Exception:
        data = {"error": "Corrupt snapshot"}
    return JSONResponse(data)


@router.get("/ui/soa/{soa_id}/freeze/{freeze_id}/view", response_class=HTMLResponse)
def ui_freeze_view(request: Request, soa_id: int, freeze_id: int):
    from ..app import _get_freeze  # type: ignore

    freeze = _get_freeze(soa_id, freeze_id)
    if not freeze:
        raise HTTPException(404, "Freeze not found")
    return templates.TemplateResponse(
        request,
        "freeze_modal.html",
        {"mode": "view", "freeze": freeze, "soa_id": soa_id},
    )


@router.get("/ui/soa/{soa_id}/freeze/diff", response_class=HTMLResponse)
def ui_freeze_diff(request: Request, soa_id: int, left: int, right: int, full: int = 0):
    from ..app import _diff_freezes_limited  # type: ignore

    limit = None if full == 1 else 50
    diff = _diff_freezes_limited(soa_id, left, right, limit=limit)
    return templates.TemplateResponse(
        request,
        "freeze_modal.html",
        {"mode": "diff", "diff": diff, "soa_id": soa_id},
    )


@router.post(
    "/ui/soa/{soa_id}/freeze/{freeze_id}/rollback", response_class=HTMLResponse
)
def ui_freeze_rollback(request: Request, soa_id: int, freeze_id: int):
    from ..app import _record_rollback_audit, _rollback_freeze  # type: ignore

    result = _rollback_freeze(soa_id, freeze_id)
    _record_rollback_audit(
        soa_id,
        freeze_id,
        {
            "visits_restored": result["visits_restored"],
            "activities_restored": result["activities_restored"],
            "cells_restored": result["cells_restored"],
            "concept_mappings_restored": result["concept_mappings_restored"],
            "elements_restored": result.get("elements_restored"),
        },
    )
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": f"/ui/soa/{soa_id}/edit"})
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@router.get(
    "/ui/soa/{soa_id}/freeze/{freeze_id}/rollback_preview", response_class=HTMLResponse
)
def ui_freeze_rollback_preview(request: Request, soa_id: int, freeze_id: int):
    from ..app import _get_freeze, _rollback_preview  # type: ignore

    preview = _rollback_preview(soa_id, freeze_id)
    freeze = _get_freeze(soa_id, freeze_id)
    return templates.TemplateResponse(
        request,
        "freeze_modal.html",
        {
            "mode": "rollback_preview",
            "preview": preview,
            "freeze": freeze,
            "soa_id": soa_id,
        },
    )


@router.get("/soa/{soa_id}/freeze/diff.json")
def get_freeze_diff_json(soa_id: int, left: int, right: int, full: int = 0):
    from ..app import _diff_freezes_limited  # type: ignore

    limit = None if full == 1 else 1000
    diff = _diff_freezes_limited(soa_id, left, right, limit=limit)
    return JSONResponse(diff)
