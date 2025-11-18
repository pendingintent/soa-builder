from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone

from ..db import _connect
from ..audit import _record_activity_audit, _record_reorder_audit

# Lightweight concept fetcher to avoid circular import with app.py
import os, json, time

_ACT_CONCEPT_CACHE = {"data": None, "fetched_at": 0}
_ACT_CONCEPT_TTL = 60 * 60


def fetch_biomedical_concepts(force: bool = False):
    override_json = os.environ.get("CDISC_CONCEPTS_JSON")
    if override_json:
        try:
            data = json.loads(override_json)
            # Normalize data into an iterable list of dicts
            if isinstance(data, dict):
                # Common patterns: {'items': [...]}, {'concepts': [...]}, or direct properties
                candidate_lists = []
                for key in ["items", "concepts", "data"]:
                    val = data.get(key)
                    if isinstance(val, list):
                        candidate_lists.append(val)
                if candidate_lists:
                    iterable = candidate_lists[0]
                else:
                    # Fallback: treat dict values; filter only list of dicts or single dicts
                    vals = []
                    for v in data.values():
                        if isinstance(v, list):
                            vals.extend([x for x in v if isinstance(x, dict)])
                        elif isinstance(v, dict):
                            vals.append(v)
                    iterable = vals
            elif isinstance(data, list):
                iterable = data
            else:
                iterable = []
            concepts = []
            for c in iterable:
                if not isinstance(c, dict):
                    continue
                code = c.get("code") or c.get("concept_code")
                title = c.get("title") or c.get("concept_title") or code
                if code:
                    concepts.append({"code": code, "title": title})
            return concepts
        except Exception:
            return []
    now = time.time()
    if (
        not force
        and _ACT_CONCEPT_CACHE["data"]
        and now - _ACT_CONCEPT_CACHE["fetched_at"] < _ACT_CONCEPT_TTL
    ):
        return _ACT_CONCEPT_CACHE["data"]
    # Remote fetch intentionally omitted here to prevent dependency & circular import; return empty list (titles fallback to codes)
    _ACT_CONCEPT_CACHE["data"] = []
    _ACT_CONCEPT_CACHE["fetched_at"] = now
    return []


router = APIRouter(prefix="/soa/{soa_id}")


class ActivityCreate(BaseModel):
    name: str


class ActivityUpdate(BaseModel):
    name: Optional[str] = None


class BulkActivities(BaseModel):
    names: List[str]


def _soa_exists(soa_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM soa WHERE id=?", (soa_id,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


@router.get("/activities", response_class=JSONResponse)
def list_activities(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,order_index,activity_uid FROM activity WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    rows = [
        {"id": r[0], "name": r[1], "order_index": r[2], "activity_uid": r[3]}
        for r in cur.fetchall()
    ]
    conn.close()
    return JSONResponse(rows)


@router.get("/activities/{activity_id}", response_class=JSONResponse)
def get_activity(soa_id: int, activity_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,order_index,activity_uid FROM activity WHERE id=? AND soa_id=?",
        (activity_id, soa_id),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Activity not found")
    return {
        "id": row[0],
        "soa_id": soa_id,
        "name": row[1],
        "order_index": row[2],
        "activity_uid": row[3],
    }


@router.post("/activities", response_class=JSONResponse)
def add_activity(soa_id: int, payload: ActivityCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM activity WHERE soa_id=?", (soa_id,))
    order_index = cur.fetchone()[0] + 1
    cur.execute(
        "INSERT INTO activity (soa_id,name,order_index,activity_uid) VALUES (?,?,?,?)",
        (soa_id, payload.name, order_index, f"Activity_{order_index}"),
    )
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    after = {
        "id": aid,
        "name": payload.name,
        "order_index": order_index,
        "activity_uid": f"Activity_{order_index}",
    }
    _record_activity_audit(soa_id, "create", aid, before=None, after=after)
    return {
        "activity_id": aid,
        "order_index": order_index,
        "activity_uid": f"Activity_{order_index}",
    }


@router.patch("/activities/{activity_id}", response_class=JSONResponse)
def update_activity(soa_id: int, activity_id: int, payload: ActivityUpdate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,order_index,activity_uid FROM activity WHERE id=? AND soa_id=?",
        (activity_id, soa_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Activity not found")
    before = {
        "id": row[0],
        "name": row[1],
        "order_index": row[2],
        "activity_uid": row[3],
    }
    new_name = (payload.name if payload.name is not None else before["name"]) or ""
    new_name = new_name.strip()
    cur.execute(
        "UPDATE activity SET name=? WHERE id=?", (new_name or None, activity_id)
    )
    conn.commit()
    cur.execute(
        "SELECT id,name,order_index,activity_uid FROM activity WHERE id=?",
        (activity_id,),
    )
    r = cur.fetchone()
    conn.close()
    after = {"id": r[0], "name": r[1], "order_index": r[2], "activity_uid": r[3]}
    updated_fields = ["name"] if before["name"] != after["name"] else []
    _record_activity_audit(
        soa_id,
        "update",
        activity_id,
        before=before,
        after={**after, "updated_fields": updated_fields},
    )
    return JSONResponse({**after, "updated_fields": updated_fields})


@router.post("/activities/reorder", response_class=JSONResponse)
def reorder_activities_api(soa_id: int, order: List[int]):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    if not order:
        raise HTTPException(400, "Order list required")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM activity WHERE soa_id=? ORDER BY order_index", (soa_id,)
    )
    old_order = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM activity WHERE soa_id=?", (soa_id,))
    existing = {r[0] for r in cur.fetchall()}
    if set(order) - existing:
        conn.close()
        raise HTTPException(400, "Order contains invalid activity id")
    before_rows = {
        r[0]: r[1]
        for r in cur.execute(
            "SELECT id,order_index FROM activity WHERE soa_id=?", (soa_id,)
        ).fetchall()
    }
    for idx, aid in enumerate(order, start=1):
        cur.execute("UPDATE activity SET order_index=? WHERE id=?", (idx, aid))
    after_rows = {
        r[0]: r[1]
        for r in cur.execute(
            "SELECT id,order_index FROM activity WHERE soa_id=?", (soa_id,)
        ).fetchall()
    }
    # Two-phase UID reassignment
    cur.execute(
        "UPDATE activity SET activity_uid='TMP_' || id WHERE soa_id=?", (soa_id,)
    )
    cur.execute(
        "UPDATE activity SET activity_uid='Activity_' || order_index WHERE soa_id=?",
        (soa_id,),
    )
    conn.commit()
    conn.close()
    _record_reorder_audit(soa_id, "activity", old_order, order)
    reorder_details = [
        {
            "id": aid,
            "before_order_index": before_rows.get(aid),
            "after_order_index": after_rows.get(aid),
        }
        for aid in order
    ]
    _record_activity_audit(
        soa_id,
        "reorder",
        activity_id=None,
        before={"old_order": old_order},
        after={"new_order": order, "details": reorder_details},
    )
    return JSONResponse({"ok": True, "old_order": old_order, "new_order": order})


@router.post("/activities/bulk", response_class=JSONResponse)
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
    cur.execute("SELECT COUNT(*) FROM activity WHERE soa_id=?", (soa_id,))
    count = cur.fetchone()[0]
    order_index = count
    added = []
    skipped = []
    for name in names:
        lname = name.lower()
        if lname in existing:
            skipped.append(name)
            continue
        order_index += 1
        cur.execute(
            "INSERT INTO activity (soa_id,name,order_index,activity_uid) VALUES (?,?,?,?)",
            (soa_id, name, order_index, f"Activity_{order_index}"),
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


@router.post("/activities/{activity_id}/concepts", response_class=JSONResponse)
def set_activity_concepts(soa_id: int, activity_id: int, concept_codes: List[str]):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM activity WHERE id=? AND soa_id=?", (activity_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Activity not found")
    cur.execute("DELETE FROM activity_concept WHERE activity_id=?", (activity_id,))
    concepts = fetch_biomedical_concepts()
    lookup = {c["code"]: c["title"] for c in concepts}
    inserted = 0
    for code in concept_codes:
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
