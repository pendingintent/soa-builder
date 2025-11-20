import json
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..audit import _record_element_audit
from ..db import _connect
from ..schemas import ElementCreate, ElementUpdate

router = APIRouter(prefix="/soa/{soa_id}")


def _soa_exists(soa_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM soa WHERE id=?", (soa_id,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


@router.get("/elements", response_class=JSONResponse)
def list_elements(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,label,description,testrl,teenrl,order_index,created_at FROM element WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    rows = [
        {
            "id": r[0],
            "name": r[1],
            "label": r[2],
            "description": r[3],
            "testrl": r[4],
            "teenrl": r[5],
            "order_index": r[6],
            "created_at": r[7],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return JSONResponse(rows)


@router.get("/elements/{element_id}", response_class=JSONResponse)
def get_element(soa_id: int, element_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,label,description,testrl,teenrl,order_index,created_at FROM element WHERE id=? AND soa_id=?",
        (element_id, soa_id),
    )
    r = cur.fetchone()
    conn.close()
    if not r:
        raise HTTPException(404, "Element not found")
    return {
        "id": r[0],
        "soa_id": soa_id,
        "name": r[1],
        "label": r[2],
        "description": r[3],
        "testrl": r[4],
        "teenrl": r[5],
        "order_index": r[6],
        "created_at": r[7],
    }


@router.get("/element_audit", response_class=JSONResponse)
def list_element_audit(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, element_id, action, before_json, after_json, performed_at FROM element_audit WHERE soa_id=? ORDER BY id DESC",
        (soa_id,),
    )
    rows = []
    for r in cur.fetchall():
        try:
            before = json.loads(r[3]) if r[3] else None
        except Exception:
            before = None
        try:
            after = json.loads(r[4]) if r[4] else None
        except Exception:
            after = None
        rows.append(
            {
                "id": r[0],
                "element_id": r[1],
                "action": r[2],
                "before": before,
                "after": after,
                "performed_at": r[5],
            }
        )
    conn.close()
    return JSONResponse(rows)


@router.post("/elements", response_class=JSONResponse, status_code=201)
def create_element(soa_id: int, payload: ElementCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(MAX(order_index),0) FROM element WHERE soa_id=?", (soa_id,)
    )
    next_ord = (cur.fetchone() or [0])[0] + 1
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """INSERT INTO element (soa_id,name,label,description,testrl,teenrl,order_index,created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            soa_id,
            name,
            (payload.label or "").strip() or None,
            (payload.description or "").strip() or None,
            (payload.testrl or "").strip() or None,
            (payload.teenrl or "").strip() or None,
            next_ord,
            now,
        ),
    )
    eid = cur.lastrowid
    conn.commit()
    conn.close()
    el = {
        "id": eid,
        "name": name,
        "label": (payload.label or "").strip() or None,
        "description": (payload.description or "").strip() or None,
        "testrl": (payload.testrl or "").strip() or None,
        "teenrl": (payload.teenrl or "").strip() or None,
        "order_index": next_ord,
        "created_at": now,
    }
    _record_element_audit(soa_id, "create", eid, before=None, after=el)
    return el


@router.patch("/elements/{element_id}", response_class=JSONResponse)
def update_element(soa_id: int, element_id: int, payload: ElementUpdate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,label,description,testrl,teenrl,order_index,created_at FROM element WHERE id=? AND soa_id=?",
        (element_id, soa_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Element not found")
    before = {
        "id": row[0],
        "name": row[1],
        "label": row[2],
        "description": row[3],
        "testrl": row[4],
        "teenrl": row[5],
        "order_index": row[6],
        "created_at": row[7],
    }
    new_name = (payload.name if payload.name is not None else before["name"]) or ""
    cur.execute(
        "UPDATE element SET name=?, label=?, description=?, testrl=?, teenrl=? WHERE id=?",
        (
            (new_name or "").strip() or None,
            (payload.label if payload.label is not None else before["label"]),
            (
                payload.description
                if payload.description is not None
                else before["description"]
            ),
            (payload.testrl if payload.testrl is not None else before["testrl"]),
            (payload.teenrl if payload.teenrl is not None else before["teenrl"]),
            element_id,
        ),
    )
    conn.commit()
    cur.execute(
        "SELECT id,name,label,description,testrl,teenrl,order_index,created_at FROM element WHERE id=?",
        (element_id,),
    )
    r = cur.fetchone()
    conn.close()
    after = {
        "id": r[0],
        "name": r[1],
        "label": r[2],
        "description": r[3],
        "testrl": r[4],
        "teenrl": r[5],
        "order_index": r[6],
        "created_at": r[7],
    }
    mutable_fields = ["name", "label", "description", "testrl", "teenrl"]
    updated_fields = [f for f in mutable_fields if before.get(f) != after.get(f)]
    _record_element_audit(
        soa_id,
        "update",
        element_id,
        before=before,
        after={**after, "updated_fields": updated_fields},
    )
    return JSONResponse({**after, "updated_fields": updated_fields})


@router.delete("/elements/{element_id}", response_class=JSONResponse)
def delete_element(soa_id: int, element_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,label,description,testrl,teenrl,order_index,created_at FROM element WHERE id=? AND soa_id=?",
        (element_id, soa_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Element not found")
    before = {
        "id": row[0],
        "name": row[1],
        "label": row[2],
        "description": row[3],
        "testrl": row[4],
        "teenrl": row[5],
        "order_index": row[6],
        "created_at": row[7],
    }
    cur.execute("DELETE FROM element WHERE id=?", (element_id,))
    conn.commit()
    conn.close()
    _record_element_audit(soa_id, "delete", element_id, before=before, after=None)
    return JSONResponse({"deleted": True, "id": element_id})


@router.post("/elements/reorder", response_class=JSONResponse)
def reorder_elements_api(soa_id: int, order: List[int]):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    if not order:
        raise HTTPException(400, "Order list required")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM element WHERE soa_id=? ORDER BY order_index", (soa_id,))
    old_order = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM element WHERE soa_id=?", (soa_id,))
    existing = {r[0] for r in cur.fetchall()}
    if set(order) - existing:
        conn.close()
        raise HTTPException(400, "Order contains invalid element id")
    for idx, eid in enumerate(order, start=1):
        cur.execute("UPDATE element SET order_index=? WHERE id=?", (idx, eid))
    conn.commit()
    conn.close()
    _record_element_audit(
        soa_id,
        "reorder",
        element_id=None,
        before={"old_order": old_order},
        after={"new_order": order},
    )
    return JSONResponse({"ok": True, "old_order": old_order, "new_order": order})
