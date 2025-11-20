from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..audit import _record_reorder_audit, _record_visit_audit
from ..db import _connect
from ..schemas import VisitCreate, VisitUpdate

router = APIRouter(prefix="/soa/{soa_id}")


def _soa_exists(soa_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM soa WHERE id=?", (soa_id,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


@router.get("/visits", response_class=JSONResponse)
def list_visits(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,raw_header,order_index,epoch_id FROM visit WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    rows = [
        {
            "id": r[0],
            "name": r[1],
            "raw_header": r[2],
            "order_index": r[3],
            "epoch_id": r[4],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return JSONResponse(rows)


@router.get("/visits/{visit_id}", response_class=JSONResponse)
def get_visit(soa_id: int, visit_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,raw_header,order_index,epoch_id FROM visit WHERE id=? AND soa_id=?",
        (visit_id, soa_id),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Visit not found")
    return {
        "id": row[0],
        "soa_id": soa_id,
        "name": row[1],
        "raw_header": row[2],
        "order_index": row[3],
        "epoch_id": row[4],
    }


@router.post("/visits", response_class=JSONResponse)
def add_visit(soa_id: int, payload: VisitCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM visit WHERE soa_id=?", (soa_id,))
    order_index = cur.fetchone()[0] + 1
    if payload.epoch_id is not None:
        cur.execute(
            "SELECT 1 FROM epoch WHERE id=? AND soa_id=?", (payload.epoch_id, soa_id)
        )
        if not cur.fetchone():
            conn.close()
            raise HTTPException(400, "Invalid epoch_id for this SOA")
    cur.execute(
        "INSERT INTO visit (soa_id,name,raw_header,order_index,epoch_id) VALUES (?,?,?,?,?)",
        (
            soa_id,
            payload.name,
            payload.raw_header or payload.name,
            order_index,
            payload.epoch_id,
        ),
    )
    vid = cur.lastrowid
    conn.commit()
    conn.close()
    after = {
        "id": vid,
        "name": payload.name,
        "raw_header": payload.raw_header or payload.name,
        "order_index": order_index,
        "epoch_id": payload.epoch_id,
    }
    _record_visit_audit(soa_id, "create", vid, before=None, after=after)
    return {"visit_id": vid, "order_index": order_index}


@router.patch("/visits/{visit_id}", response_class=JSONResponse)
def update_visit(soa_id: int, visit_id: int, payload: VisitUpdate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,raw_header,order_index,epoch_id FROM visit WHERE id=? AND soa_id=?",
        (visit_id, soa_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Visit not found")
    before = {
        "id": row[0],
        "name": row[1],
        "raw_header": row[2],
        "order_index": row[3],
        "epoch_id": row[4],
    }
    if payload.epoch_id is not None:
        cur.execute(
            "SELECT 1 FROM epoch WHERE id=? AND soa_id=?", (payload.epoch_id, soa_id)
        )
        if not cur.fetchone():
            conn.close()
            raise HTTPException(400, "Invalid epoch_id for this SOA")
    new_name = (payload.name if payload.name is not None else before["name"]) or ""
    new_name = new_name.strip()
    new_raw_header = (
        (payload.raw_header if payload.raw_header is not None else before["raw_header"])
        or new_name
        or ""
    )
    new_raw_header = new_raw_header.strip()
    new_epoch_id = (
        payload.epoch_id if payload.epoch_id is not None else before["epoch_id"]
    )
    cur.execute(
        "UPDATE visit SET name=?, raw_header=?, epoch_id=? WHERE id=?",
        (new_name or None, new_raw_header or None, new_epoch_id, visit_id),
    )
    conn.commit()
    cur.execute(
        "SELECT id,name,raw_header,order_index,epoch_id FROM visit WHERE id=?",
        (visit_id,),
    )
    r = cur.fetchone()
    conn.close()
    after = {
        "id": r[0],
        "name": r[1],
        "raw_header": r[2],
        "order_index": r[3],
        "epoch_id": r[4],
    }
    updated_fields = [
        f for f in ["name", "raw_header", "epoch_id"] if before.get(f) != after.get(f)
    ]
    _record_visit_audit(
        soa_id,
        "update",
        visit_id,
        before=before,
        after={**after, "updated_fields": updated_fields},
    )
    return JSONResponse({**after, "updated_fields": updated_fields})


@router.post("/visits/reorder", response_class=JSONResponse)
def reorder_visits_api(soa_id: int, order: List[int]):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    if not order:
        raise HTTPException(400, "Order list required")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM visit WHERE soa_id=? ORDER BY order_index", (soa_id,))
    old_order = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM visit WHERE soa_id=?", (soa_id,))
    existing = {r[0] for r in cur.fetchall()}
    if set(order) - existing:
        conn.close()
        raise HTTPException(400, "Order contains invalid visit id")
    for idx, vid in enumerate(order, start=1):
        cur.execute("UPDATE visit SET order_index=? WHERE id=?", (idx, vid))
    conn.commit()
    conn.close()
    _record_reorder_audit(soa_id, "visit", old_order, order)
    _record_visit_audit(
        soa_id,
        "reorder",
        visit_id=None,
        before={"old_order": old_order},
        after={"new_order": order},
    )
    return JSONResponse({"ok": True, "old_order": old_order, "new_order": order})
