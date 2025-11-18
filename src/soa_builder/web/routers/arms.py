from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from typing import List
import json
from datetime import datetime
import logging

from ..db import _connect
from ..schemas import ArmCreate, ArmUpdate
from ..audit import _record_arm_audit, _record_reorder_audit

router = APIRouter(prefix="/soa/{soa_id}")


def _soa_exists(soa_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM soa WHERE id=?", (soa_id,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


@router.get("/arms", response_class=JSONResponse)
def list_arms(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,label,description,type,data_origin_type,order_index,arm_uid FROM arm WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    rows = [
        {
            "id": r[0],
            "name": r[1],
            "label": r[2],
            "description": r[3],
            "type": r[4],
            "data_origin_type": r[5],
            "order_index": r[6],
            "arm_uid": r[7],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return rows


@router.post("/arms", response_class=JSONResponse, status_code=201)
def create_arm(soa_id: int, payload: ArmCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(MAX(order_index),0) FROM arm WHERE soa_id=?", (soa_id,)
    )
    next_ord = (cur.fetchone() or [0])[0] + 1
    cur.execute(
        "SELECT arm_uid FROM arm WHERE soa_id=? AND arm_uid LIKE 'StudyArm_%'",
        (soa_id,),
    )
    existing_uids = [r[0] for r in cur.fetchall() if r[0]]
    used_nums = set()
    for uid in existing_uids:
        if uid.startswith("StudyArm_"):
            tail = uid[len("StudyArm_") :]
            if tail.isdigit():
                used_nums.add(int(tail))
            else:
                logging.getLogger("soa_builder.concepts").warning(
                    "Invalid arm_uid format encountered (ignored for numbering): %s",
                    uid,
                )
    next_n = 1
    while next_n in used_nums:
        next_n += 1
    new_uid = f"StudyArm_{next_n}"
    cur.execute(
        """INSERT INTO arm (soa_id,name,label,description,type,data_origin_type,order_index,arm_uid)
            VALUES (?,?,?,?,?,?,?,?)""",
        (
            soa_id,
            name,
            (payload.label or "").strip() or None,
            (payload.description or "").strip() or None,
            (payload.type or "").strip() or None,
            (payload.data_origin_type or "").strip() or None,
            next_ord,
            new_uid,
        ),
    )
    arm_id = cur.lastrowid
    conn.commit()
    conn.close()
    row = {
        "id": arm_id,
        "name": name,
        "label": (payload.label or "").strip() or None,
        "description": (payload.description or "").strip() or None,
        "type": (payload.type or "").strip() or None,
        "data_origin_type": (payload.data_origin_type or "").strip() or None,
        "order_index": next_ord,
        "arm_uid": new_uid,
    }
    _record_arm_audit(soa_id, "create", arm_id, before=None, after=row)
    return row


@router.patch("/arms/{arm_id}", response_class=JSONResponse)
def update_arm(soa_id: int, arm_id: int, payload: ArmUpdate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,label,description,type,data_origin_type,order_index,arm_uid FROM arm WHERE id=? AND soa_id=?",
        (arm_id, soa_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Arm not found")
    before = {
        "id": row[0],
        "name": row[1],
        "label": row[2],
        "description": row[3],
        "type": row[4],
        "data_origin_type": row[5],
        "order_index": row[6],
        "arm_uid": row[7],
    }
    new_name = (payload.name if payload.name is not None else before["name"]) or ""
    new_label = payload.label if payload.label is not None else before["label"]
    new_desc = (
        payload.description
        if payload.description is not None
        else before["description"]
    )
    new_type = payload.type if payload.type is not None else before["type"]
    new_origin = (
        payload.data_origin_type
        if payload.data_origin_type is not None
        else before["data_origin_type"]
    )
    cur.execute(
        "UPDATE arm SET name=?, label=?, description=?, type=?, data_origin_type=? WHERE id=?",
        (
            (new_name or "").strip() or None,
            (new_label or "").strip() or None,
            (new_desc or "").strip() or None,
            (new_type or "").strip() or None,
            (new_origin or "").strip() or None,
            arm_id,
        ),
    )
    conn.commit()
    cur.execute(
        "SELECT id,name,label,description,type,data_origin_type,order_index,arm_uid FROM arm WHERE id=?",
        (arm_id,),
    )
    r = cur.fetchone()
    conn.close()
    after = {
        "id": r[0],
        "name": r[1],
        "label": r[2],
        "description": r[3],
        "type": r[4],
        "data_origin_type": r[5],
        "order_index": r[6],
        "arm_uid": r[7],
    }
    mutable = ["name", "label", "description", "type", "data_origin_type"]
    updated_fields = [f for f in mutable if before.get(f) != after.get(f)]
    _record_arm_audit(
        soa_id,
        "update",
        arm_id,
        before=before,
        after={**after, "updated_fields": updated_fields},
    )
    return {**after, "updated_fields": updated_fields}


@router.delete("/arms/{arm_id}", response_class=JSONResponse)
def delete_arm(soa_id: int, arm_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,label,description,type,data_origin_type,order_index,arm_uid FROM arm WHERE id=? AND soa_id=?",
        (arm_id, soa_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Arm not found")
    before = {
        "id": row[0],
        "name": row[1],
        "label": row[2],
        "description": row[3],
        "type": row[4],
        "data_origin_type": row[5],
        "order_index": row[6],
        "arm_uid": row[7],
    }
    cur.execute("DELETE FROM arm WHERE id=?", (arm_id,))
    conn.commit()
    conn.close()
    _record_arm_audit(soa_id, "delete", arm_id, before=before, after=None)
    return {"deleted": True, "id": arm_id}


@router.post("/arms/reorder", response_class=JSONResponse)
def reorder_arms_api(soa_id: int, order: List[int]):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    if not order:
        raise HTTPException(400, "Order list required")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM arm WHERE soa_id=? ORDER BY order_index", (soa_id,))
    old_order = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM arm WHERE soa_id=?", (soa_id,))
    existing = {r[0] for r in cur.fetchall()}
    if set(order) - existing:
        conn.close()
        raise HTTPException(400, "Order contains invalid arm id")
    for idx, aid in enumerate(order, start=1):
        cur.execute("UPDATE arm SET order_index=? WHERE id=?", (idx, aid))
    conn.commit()
    conn.close()
    _record_reorder_audit(soa_id, "arm", old_order, order)
    _record_arm_audit(
        soa_id,
        "reorder",
        arm_id=None,
        before={"old_order": old_order},
        after={"new_order": order},
    )
    return {"ok": True, "old_order": old_order, "new_order": order}
