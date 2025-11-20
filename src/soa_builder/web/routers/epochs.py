import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..schemas import EpochCreate, EpochUpdate

DB_PATH = os.environ.get("SOA_BUILDER_DB", "soa_builder_web.db")

router = APIRouter()


def _connect():
    return sqlite3.connect(DB_PATH)


def _soa_exists(soa_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM soa WHERE id=?", (soa_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def _record_epoch_audit(
    soa_id: int,
    action: str,
    epoch_id: Optional[int],
    before: Optional[dict] = None,
    after: Optional[dict] = None,
):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO epoch_audit (soa_id, epoch_id, action, before_json, after_json, performed_at) VALUES (?,?,?,?,?,?)",
            (
                soa_id,
                epoch_id,
                action,
                json.dumps(before) if before else None,
                json.dumps(after) if after else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


@router.post("/soa/{soa_id}/epochs")
def add_epoch(soa_id: int, payload: EpochCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM epoch WHERE soa_id=?", (soa_id,))
    order_index = cur.fetchone()[0] + 1
    cur.execute("SELECT MAX(epoch_seq) FROM epoch WHERE soa_id=?", (soa_id,))
    row = cur.fetchone()
    next_seq = (row[0] or 0) + 1
    cur.execute(
        "INSERT INTO epoch (soa_id,name,order_index,epoch_seq,epoch_label,epoch_description) VALUES (?,?,?,?,?,?)",
        (
            soa_id,
            payload.name,
            order_index,
            next_seq,
            (payload.epoch_label or "").strip() or None,
            (payload.epoch_description or "").strip() or None,
        ),
    )
    eid = cur.lastrowid
    conn.commit()
    conn.close()
    result = {"epoch_id": eid, "order_index": order_index, "epoch_seq": next_seq}
    _record_epoch_audit(
        soa_id,
        "create",
        eid,
        before=None,
        after={
            "id": eid,
            "name": payload.name,
            "order_index": order_index,
            "epoch_seq": next_seq,
            "epoch_label": (payload.epoch_label or "").strip() or None,
            "epoch_description": (payload.epoch_description or "").strip() or None,
        },
    )
    return result


@router.get("/soa/{soa_id}/epochs")
def list_epochs(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,order_index,epoch_seq,epoch_label,epoch_description FROM epoch WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    rows = [
        {
            "id": r[0],
            "name": r[1],
            "order_index": r[2],
            "epoch_seq": r[3],
            "epoch_label": r[4],
            "epoch_description": r[5],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return {"soa_id": soa_id, "epochs": rows}


@router.get("/soa/{soa_id}/epochs/{epoch_id}")
def get_epoch(soa_id: int, epoch_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,order_index,epoch_seq,epoch_label,epoch_description FROM epoch WHERE id=? AND soa_id=?",
        (epoch_id, soa_id),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Epoch not found")
    return {
        "id": row[0],
        "soa_id": soa_id,
        "name": row[1],
        "order_index": row[2],
        "epoch_seq": row[3],
        "epoch_label": row[4],
        "epoch_description": row[5],
    }


@router.post("/soa/{soa_id}/epochs/{epoch_id}/metadata")
def update_epoch_metadata(soa_id: int, epoch_id: int, payload: EpochUpdate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM epoch WHERE id=? AND soa_id=?", (epoch_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Epoch not found")
    cur.execute(
        "SELECT id,name,order_index,epoch_seq,epoch_label,epoch_description FROM epoch WHERE id=?",
        (epoch_id,),
    )
    b = cur.fetchone()
    before = None
    if b:
        before = {
            "id": b[0],
            "name": b[1],
            "order_index": b[2],
            "epoch_seq": b[3],
            "epoch_label": b[4],
            "epoch_description": b[5],
        }
    sets = []
    vals = []
    if payload.name is not None:
        sets.append("name=?")
        vals.append((payload.name or "").strip() or None)
    if payload.epoch_label is not None:
        sets.append("epoch_label=?")
        vals.append((payload.epoch_label or "").strip() or None)
    if payload.epoch_description is not None:
        sets.append("epoch_description=?")
        vals.append((payload.epoch_description or "").strip() or None)
    if sets:
        vals.append(epoch_id)
        cur.execute(f"UPDATE epoch SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
    cur.execute(
        "SELECT id,name,order_index,epoch_seq,epoch_label,epoch_description FROM epoch WHERE id=?",
        (epoch_id,),
    )
    row = cur.fetchone()
    conn.close()
    after = {
        "id": row[0],
        "name": row[1],
        "order_index": row[2],
        "epoch_seq": row[3],
        "epoch_label": row[4],
        "epoch_description": row[5],
    }
    mutable = ["name", "epoch_label", "epoch_description"]
    updated_fields = [f for f in mutable if before and before.get(f) != after.get(f)]
    _record_epoch_audit(
        soa_id,
        "update",
        epoch_id,
        before=before,
        after={**after, "updated_fields": updated_fields},
    )
    return {**after, "updated_fields": updated_fields}


@router.post("/soa/{soa_id}/epochs/reorder", response_class=JSONResponse)
def reorder_epochs_api(soa_id: int, order: List[int]):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    if not order:
        raise HTTPException(400, "Order list required")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM epoch WHERE soa_id=? ORDER BY order_index", (soa_id,))
    old_order = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM epoch WHERE soa_id=?", (soa_id,))
    existing = {r[0] for r in cur.fetchall()}
    if set(order) - existing:
        conn.close()
        raise HTTPException(400, "Order contains invalid epoch id")
    for idx, eid in enumerate(order, start=1):
        cur.execute("UPDATE epoch SET order_index=? WHERE id=?", (idx, eid))
    conn.commit()
    conn.close()
    _record_epoch_audit(
        soa_id,
        "reorder",
        epoch_id=None,
        before={"old_order": old_order},
        after={"new_order": order},
    )
    return JSONResponse({"ok": True, "old_order": old_order, "new_order": order})
