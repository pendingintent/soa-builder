import io
import os

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ..db import _connect

DB_PATH = os.environ.get("SOA_BUILDER_DB", "soa_builder_web.db")
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter()


def _soa_exists(soa_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM soa WHERE id=?", (soa_id,))
    r = cur.fetchone()
    conn.close()
    return r is not None


@router.get("/soa/{soa_id}/rollback_audit")
def get_rollback_audit_json(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    from ..app import _list_rollback_audit  # type: ignore

    return {"audit": _list_rollback_audit(soa_id)}


@router.get("/soa/{soa_id}/reorder_audit")
def get_reorder_audit_json(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    from ..app import _list_reorder_audit  # type: ignore

    return {"audit": _list_reorder_audit(soa_id)}


@router.get("/ui/soa/{soa_id}/rollback_audit", response_class=HTMLResponse)
def ui_rollback_audit(request: Request, soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    from ..app import _list_rollback_audit  # type: ignore

    return templates.TemplateResponse(
        request,
        "rollback_audit_modal.html",
        {"soa_id": soa_id, "audit": _list_rollback_audit(soa_id)},
    )


@router.get("/ui/soa/{soa_id}/reorder_audit", response_class=HTMLResponse)
def ui_reorder_audit(request: Request, soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    from ..app import _list_reorder_audit  # type: ignore

    return templates.TemplateResponse(
        request,
        "reorder_audit_modal.html",
        {"soa_id": soa_id, "audit": _list_reorder_audit(soa_id)},
    )


@router.get("/soa/{soa_id}/rollback_audit/export/xlsx")
def export_rollback_audit_xlsx(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    from ..app import _list_rollback_audit  # type: ignore

    rows = _list_rollback_audit(soa_id)
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "id",
                "freeze_id",
                "performed_at",
                "visits_restored",
                "activities_restored",
                "cells_restored",
                "concepts_restored",
                "elements_restored",
            ]
        )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="RollbackAudit")
    bio.seek(0)
    filename = f"soa_{soa_id}_rollback_audit.xlsx"
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/soa/{soa_id}/reorder_audit/export/xlsx")
def export_reorder_audit_xlsx(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    from ..app import _list_reorder_audit  # type: ignore

    rows = _list_reorder_audit(soa_id)
    flat = []
    for r in rows:
        moves = []
        old_pos = {vid: idx + 1 for idx, vid in enumerate(r.get("old_order", []))}
        new_order = r.get("new_order", [])
        for idx, vid in enumerate(new_order, start=1):
            op = old_pos.get(vid)
            if op and op != idx:
                moves.append(f"{vid}:{op}->{idx}")
        flat.append(
            {
                "id": r.get("id"),
                "entity_type": r.get("entity_type"),
                "performed_at": r.get("performed_at"),
                "old_order": ",".join(map(str, r.get("old_order", []))),
                "new_order": ",".join(map(str, new_order)),
                "moves": "; ".join(moves) if moves else "",
            }
        )
    df = pd.DataFrame(flat)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "id",
                "entity_type",
                "performed_at",
                "old_order",
                "new_order",
                "moves",
            ]
        )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="ReorderAudit")
    bio.seek(0)
    filename = f"soa_{soa_id}_reorder_audit.xlsx"
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
