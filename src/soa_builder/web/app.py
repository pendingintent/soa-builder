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

import os
import sqlite3
import csv
import tempfile
import json
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
import re as _re

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
    # Epochs: high-level study phase grouping (optional). Behaves like visits/activities list ordering.
    cur.execute(
        """CREATE TABLE IF NOT EXISTS epoch (id INTEGER PRIMARY KEY AUTOINCREMENT, soa_id INTEGER, name TEXT, order_index INTEGER)"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS cell (id INTEGER PRIMARY KEY AUTOINCREMENT, soa_id INTEGER, visit_id INTEGER, activity_id INTEGER, status TEXT)"""
    )
    # Mapping table linking activities to biomedical concepts (concept_code + title stored for snapshot purposes)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS activity_concept (id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id INTEGER, concept_code TEXT, concept_title TEXT)"""
    )
    # Frozen versions (snapshot JSON of current matrix & concepts)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS soa_freeze (id INTEGER PRIMARY KEY AUTOINCREMENT, soa_id INTEGER, version_label TEXT, created_at TEXT, snapshot_json TEXT)"""
    )
    # Unique index to enforce one label per SoA
    cur.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_soafreeze_unique ON soa_freeze(soa_id, version_label)"""
    )
    # Rollback audit log
    cur.execute(
        """CREATE TABLE IF NOT EXISTS rollback_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soa_id INTEGER NOT NULL,
            freeze_id INTEGER NOT NULL,
            performed_at TEXT NOT NULL,
            visits_restored INTEGER,
            activities_restored INTEGER,
            cells_restored INTEGER,
            concepts_restored INTEGER
        )"""
    )
    # Reorder audit (tracks manual drag reorder operations for visits & activities)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS reorder_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            soa_id INTEGER NOT NULL,
            entity_type TEXT NOT NULL, -- 'visit' | 'activity' | 'epoch'
            old_order_json TEXT NOT NULL,
            new_order_json TEXT NOT NULL,
            performed_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()


_init_db()


# --------------------- Migration: add epoch_id to visit ---------------------
def _migrate_add_epoch_id_to_visit():
    """Add epoch_id column to visit table if missing."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(visit)")
        cols = {r[1] for r in cur.fetchall()}
        if "epoch_id" not in cols:
            cur.execute("ALTER TABLE visit ADD COLUMN epoch_id INTEGER")
            conn.commit()
            logger.info("Added epoch_id column to visit table")
        conn.close()
    except Exception as e:
        logger.warning("epoch_id migration failed: %s", e)


_migrate_add_epoch_id_to_visit()


# --------------------- Migration: add epoch_seq to epoch ---------------------
def _migrate_add_epoch_seq():
    """Ensure epoch_seq (immutable sequence per SoA) exists; backfill sequential values per study.
    Creates unique index (soa_id, epoch_seq) to guarantee uniqueness inside a study.
    """
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(epoch)")
        cols = {r[1] for r in cur.fetchall()}
        if "epoch_seq" not in cols:
            cur.execute("ALTER TABLE epoch ADD COLUMN epoch_seq INTEGER")
            conn.commit()
            logger.info("Added epoch_seq column to epoch table")
            # Backfill existing epochs with sequential values by id order per soa
            cur.execute("SELECT DISTINCT soa_id FROM epoch")
            soa_ids = [r[0] for r in cur.fetchall()]
            for sid in soa_ids:
                cur.execute("SELECT id FROM epoch WHERE soa_id=? ORDER BY id", (sid,))
                ids = [r[0] for r in cur.fetchall()]
                for seq, eid in enumerate(ids, start=1):
                    cur.execute("UPDATE epoch SET epoch_seq=? WHERE id=?", (seq, eid))
            conn.commit()
        # Unique index (idempotent)
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_epoch_soaid_seq ON epoch(soa_id, epoch_seq)"
            )
            conn.commit()
        except Exception as ie:  # pragma: no cover
            logger.warning("Failed creating idx_epoch_soaid_seq: %s", ie)
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("epoch_seq migration failed: %s", e)


_migrate_add_epoch_seq()


# --------------------- Migration: add epoch label/description ---------------------
def _migrate_add_epoch_label_desc():
    """Add optional epoch_label and epoch_description columns if missing."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(epoch)")
        cols = {r[1] for r in cur.fetchall()}
        alters = []
        if "epoch_label" not in cols:
            alters.append("ALTER TABLE epoch ADD COLUMN epoch_label TEXT")
        if "epoch_description" not in cols:
            alters.append("ALTER TABLE epoch ADD COLUMN epoch_description TEXT")
        for stmt in alters:
            try:
                cur.execute(stmt)
            except Exception as e:  # pragma: no cover
                logger.warning(
                    "Failed epoch label/description migration '%s': %s", stmt, e
                )
        if alters:
            conn.commit()
            logger.info(
                "Applied epoch label/description migration: %s", ", ".join(alters)
            )
        conn.close()
    except Exception as e:  # pragma: no cover
        logger.warning("Epoch label/description migration failed: %s", e)


_migrate_add_epoch_label_desc()
# --------------------- Migrations: add study metadata columns ---------------------


def _migrate_add_study_fields():
    """Ensure study metadata columns (study_id, study_label, study_description) exist on soa table.
    Safe to run repeatedly; SQLite ADD COLUMN is idempotent when guarded by schema inspection.
    """
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(soa)")
        existing = {r[1] for r in cur.fetchall()}  # column names
        alters = []
        if "study_id" not in existing:
            alters.append("ALTER TABLE soa ADD COLUMN study_id TEXT")
        if "study_label" not in existing:
            alters.append("ALTER TABLE soa ADD COLUMN study_label TEXT")
        if "study_description" not in existing:
            alters.append("ALTER TABLE soa ADD COLUMN study_description TEXT")
        for stmt in alters:
            try:
                cur.execute(stmt)
            except (
                Exception
            ) as e:  # pragma: no cover - defensive; should not fail normally
                logger.warning("Failed executing migration statement '%s': %s", stmt, e)
        if alters:
            conn.commit()
        # Create unique index on study_id (NULLs allowed multiple times by SQLite)
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_soa_study_id ON soa(study_id)"
            )
            conn.commit()
        except Exception as e:  # pragma: no cover
            logger.warning("Failed creating unique index idx_soa_study_id: %s", e)
        conn.close()
        if alters:
            logger.info("Applied study field migrations: %s", ", ".join(alters))
    except Exception as e:  # pragma: no cover
        logger.warning("Study field migration failed: %s", e)


_migrate_add_study_fields()

# --------------------- Migrations ---------------------


def _drop_unused_override_table():
    """Drop legacy activity_concept_override table if it still exists.
    This table supported mutable concept titles which are no longer allowed.
    Safe to run repeatedly; will no-op if table absent."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_concept_override'"
        )
        if cur.fetchone():
            try:
                cur.execute("DROP TABLE activity_concept_override")
                conn.commit()
                logger.info("Dropped obsolete table activity_concept_override")
            except Exception as e:
                logger.warning(
                    "Failed to drop obsolete table activity_concept_override: %s", e
                )
        conn.close()
    except Exception as e:
        logger.warning("Migration check for activity_concept_override failed: %s", e)


_drop_unused_override_table()

# --------------------- Models ---------------------


class SOACreate(BaseModel):
    name: str
    study_id: Optional[str] = None
    study_label: Optional[str] = None
    study_description: Optional[str] = None


class SOAMetadataUpdate(BaseModel):
    study_id: Optional[str] = None
    study_label: Optional[str] = None
    study_description: Optional[str] = None


class VisitCreate(BaseModel):
    name: str
    raw_header: Optional[str] = None
    epoch_id: Optional[int] = None


class ActivityCreate(BaseModel):
    name: str


class EpochCreate(BaseModel):
    name: str
    epoch_label: Optional[str] = None
    epoch_description: Optional[str] = None


class EpochUpdate(BaseModel):
    name: Optional[str] = None
    epoch_label: Optional[str] = None
    epoch_description: Optional[str] = None


class ConceptsUpdate(BaseModel):
    concept_codes: List[str]


class FreezeCreate(BaseModel):
    version_label: Optional[str] = None


def _list_freezes(soa_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, version_label, created_at FROM soa_freeze WHERE soa_id=? ORDER BY id DESC",
        (soa_id,),
    )
    rows = [dict(id=r[0], version_label=r[1], created_at=r[2]) for r in cur.fetchall()]
    conn.close()
    return rows


def _get_freeze(soa_id: int, freeze_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, version_label, created_at, snapshot_json FROM soa_freeze WHERE id=? AND soa_id=?",
        (freeze_id, soa_id),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    try:
        snap = json.loads(row[3])
    except Exception:
        snap = {"error": "Corrupt snapshot"}
    return {
        "id": row[0],
        "version_label": row[1],
        "created_at": row[2],
        "snapshot": snap,
    }


def _create_freeze(soa_id: int, version_label: Optional[str]):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    # Auto version label if not provided
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT version_label FROM soa_freeze WHERE soa_id=?", (soa_id,))
    existing_labels = {r[0] for r in cur.fetchall()}
    if not version_label or not version_label.strip():
        # Find next available vN
        n = 1
        while f"v{n}" in existing_labels:
            n += 1
        version_label = f"v{n}"
    else:
        version_label = version_label.strip()
    if version_label in existing_labels:
        raise HTTPException(400, "Version label already exists for this SOA")
    # Gather snapshot data
    cur.execute(
        "SELECT name, created_at, study_id, study_label, study_description FROM soa WHERE id=?",
        (soa_id,),
    )
    row = cur.fetchone()
    soa_name = row[0] if row else f"SOA {soa_id}"
    study_id_val = row[2] if row else None
    study_label_val = row[3] if row else None
    study_description_val = row[4] if row else None
    visits, activities, cells = _fetch_matrix(soa_id)
    # Epochs snapshot (ordered)
    conn2 = _connect()
    cur2 = conn2.cursor()
    cur2.execute(
        "SELECT id,name,order_index,epoch_seq,epoch_label,epoch_description FROM epoch WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    epochs = [
        dict(
            id=r[0],
            name=r[1],
            order_index=r[2],
            epoch_seq=r[3],
            epoch_label=r[4],
            epoch_description=r[5],
        )
        for r in cur2.fetchall()
    ]
    conn2.close()
    # Concept mapping
    activity_ids = [a["id"] for a in activities]
    concepts_map = {}
    if activity_ids:
        placeholders = ",".join("?" for _ in activity_ids)
        cur.execute(
            f"SELECT activity_id, concept_code, concept_title FROM activity_concept WHERE activity_id IN ({placeholders})",
            activity_ids,
        )
        for aid, code, title in cur.fetchall():
            concepts_map.setdefault(aid, []).append({"code": code, "title": title})
    snapshot = {
        "soa_id": soa_id,
        "soa_name": soa_name,
        "study_id": study_id_val,
        "study_label": study_label_val,
        "study_description": study_description_val,
        "version_label": version_label,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "epochs": epochs,
        "visits": visits,
        "activities": activities,
        "cells": cells,
        "activity_concepts": concepts_map,
    }
    snap_json = json.dumps(snapshot)
    cur.execute(
        "INSERT INTO soa_freeze (soa_id, version_label, created_at, snapshot_json) VALUES (?,?,?,?)",
        (soa_id, version_label, datetime.now(timezone.utc).isoformat(), snap_json),
    )
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return fid, version_label


def _diff_freezes(soa_id: int, left_id: int, right_id: int):
    return _diff_freezes_limited(soa_id, left_id, right_id, limit=None)


def _diff_freezes_limited(
    soa_id: int, left_id: int, right_id: int, limit: Optional[int]
):
    left = _get_freeze(soa_id, left_id)
    right = _get_freeze(soa_id, right_id)
    if not left or not right:
        raise HTTPException(404, "Freeze not found")
    l_snap = left["snapshot"]
    r_snap = right["snapshot"]
    # Visits
    l_vis = {
        str(v["id"]): v
        for v in l_snap.get("visits", [])
        if isinstance(v, dict) and "id" in v
    }
    r_vis = {
        str(v["id"]): v
        for v in r_snap.get("visits", [])
        if isinstance(v, dict) and "id" in v
    }
    visits_added_all = [r_vis[k] for k in r_vis.keys() - l_vis.keys()]
    visits_removed_all = [l_vis[k] for k in l_vis.keys() - r_vis.keys()]
    # Activities
    l_act = {
        str(a["id"]): a
        for a in l_snap.get("activities", [])
        if isinstance(a, dict) and "id" in a
    }
    r_act = {
        str(a["id"]): a
        for a in r_snap.get("activities", [])
        if isinstance(a, dict) and "id" in a
    }
    acts_added_all = [r_act[k] for k in r_act.keys() - l_act.keys()]
    acts_removed_all = [l_act[k] for k in l_act.keys() - r_act.keys()]
    # Cells (status changes)
    l_cells = {
        (c["visit_id"], c["activity_id"]): c
        for c in l_snap.get("cells", [])
        if isinstance(c, dict)
    }
    r_cells = {
        (c["visit_id"], c["activity_id"]): c
        for c in r_snap.get("cells", [])
        if isinstance(c, dict)
    }
    cells_added_all = [r_cells[k] for k in r_cells.keys() - l_cells.keys()]
    cells_removed_all = [l_cells[k] for k in l_cells.keys() - r_cells.keys()]
    cells_changed_all = []
    for k in r_cells.keys() & l_cells.keys():
        if r_cells[k].get("status") != l_cells[k].get("status"):
            cells_changed_all.append(
                {
                    "visit_id": k[0],
                    "activity_id": k[1],
                    "old_status": l_cells[k].get("status"),
                    "new_status": r_cells[k].get("status"),
                }
            )
    # Concepts per activity with title change detection
    l_concepts_map = l_snap.get("activity_concepts", {}) or {}
    r_concepts_map = r_snap.get("activity_concepts", {}) or {}
    concept_changes_all = []
    all_aids = set(map(str, l_concepts_map.keys())) | set(
        map(str, r_concepts_map.keys())
    )

    def _get_concept_list(m, key):
        # Support snapshots where JSON serialization converted int keys to strings
        if key in m:
            return m[key] or []
        if key.isdigit() and int(key) in m:
            return m[int(key)] or []
        return []

    for aid in all_aids:
        la = _get_concept_list(l_concepts_map, aid)
        ra = _get_concept_list(r_concepts_map, aid)
        l_set = {c["code"] for c in la if isinstance(c, dict)}
        r_set = {c["code"] for c in ra if isinstance(c, dict)}
        added = sorted(list(r_set - l_set))
        removed = sorted(list(l_set - r_set))
        title_changes = []
        for code in sorted(list(l_set & r_set)):
            l_title = next((c["title"] for c in la if c.get("code") == code), None)
            r_title = next((c["title"] for c in ra if c.get("code") == code), None)
            if l_title is not None and r_title is not None and l_title != r_title:
                title_changes.append(
                    {"code": code, "old_title": l_title, "new_title": r_title}
                )
        if added or removed or title_changes:
            concept_changes_all.append(
                {
                    "activity_id": aid,
                    "added": added,
                    "removed": removed,
                    "title_changes": title_changes,
                }
            )

    # Apply limit truncation if provided and >0
    def _truncate(lst):
        if limit and limit > 0 and len(lst) > limit:
            return lst[:limit], True
        return lst, False

    visits_added, visits_added_trunc = _truncate(visits_added_all)
    visits_removed, visits_removed_trunc = _truncate(visits_removed_all)
    acts_added, acts_added_trunc = _truncate(acts_added_all)
    acts_removed, acts_removed_trunc = _truncate(acts_removed_all)
    cells_added, cells_added_trunc = _truncate(cells_added_all)
    cells_removed, cells_removed_trunc = _truncate(cells_removed_all)
    cells_changed, cells_changed_trunc = _truncate(cells_changed_all)
    concept_changes, concept_changes_trunc = _truncate(concept_changes_all)
    meta = {
        "limit": limit,
        "visits": {
            "added_total": len(visits_added_all),
            "removed_total": len(visits_removed_all),
            "added_truncated": visits_added_trunc,
            "removed_truncated": visits_removed_trunc,
        },
        "activities": {
            "added_total": len(acts_added_all),
            "removed_total": len(acts_removed_all),
            "added_truncated": acts_added_trunc,
            "removed_truncated": acts_removed_trunc,
        },
        "cells": {
            "added_total": len(cells_added_all),
            "removed_total": len(cells_removed_all),
            "changed_total": len(cells_changed_all),
            "added_truncated": cells_added_trunc,
            "removed_truncated": cells_removed_trunc,
            "changed_truncated": cells_changed_trunc,
        },
        "concepts": {
            "changes_total": len(concept_changes_all),
            "changes_truncated": concept_changes_trunc,
        },
    }
    return {
        "left": {
            "id": left["id"],
            "label": left["version_label"],
            "created_at": left["created_at"],
        },
        "right": {
            "id": right["id"],
            "label": right["version_label"],
            "created_at": right["created_at"],
        },
        "visits": {"added": visits_added, "removed": visits_removed},
        "activities": {"added": acts_added, "removed": acts_removed},
        "cells": {
            "added": cells_added,
            "removed": cells_removed,
            "changed": cells_changed,
        },
        "concepts": concept_changes,
        "meta": meta,
    }


def _rollback_freeze(soa_id: int, freeze_id: int) -> dict:
    freeze = _get_freeze(soa_id, freeze_id)
    if not freeze:
        raise HTTPException(404, "Freeze not found")
    snap = freeze["snapshot"]
    if snap.get("soa_id") != soa_id:
        raise HTTPException(400, "Snapshot SoA mismatch")
    visits = snap.get("visits", [])
    activities = snap.get("activities", [])
    cells = snap.get("cells", [])
    concepts_map = snap.get("activity_concepts", {}) or {}
    conn = _connect()
    cur = conn.cursor()
    # Clear existing
    # Order matters: delete cells, then concepts (while activity rows still exist), then activities, then visits.
    cur.execute("DELETE FROM cell WHERE soa_id=?", (soa_id,))
    cur.execute(
        "DELETE FROM activity_concept WHERE activity_id IN (SELECT id FROM activity WHERE soa_id=? )",
        (soa_id,),
    )
    cur.execute("DELETE FROM activity WHERE soa_id=?", (soa_id,))
    cur.execute("DELETE FROM visit WHERE soa_id=?", (soa_id,))
    # Reinsert visits mapping old id->new id
    visit_id_map = {}
    for v in sorted(visits, key=lambda x: x.get("order_index", 0)):
        cur.execute(
            "INSERT INTO visit (soa_id,name,raw_header,order_index) VALUES (?,?,?,?)",
            (
                soa_id,
                v.get("name"),
                v.get("raw_header") or v.get("name"),
                v.get("order_index"),
            ),
        )
        new_id = cur.lastrowid
        visit_id_map[v.get("id")] = new_id
    # Reinsert activities mapping old id->new id
    activity_id_map = {}
    for a in sorted(activities, key=lambda x: x.get("order_index", 0)):
        cur.execute(
            "INSERT INTO activity (soa_id,name,order_index) VALUES (?,?,?)",
            (soa_id, a.get("name"), a.get("order_index")),
        )
        new_id = cur.lastrowid
        activity_id_map[a.get("id")] = new_id
    # Reinsert cells
    inserted_cells = 0
    for c in cells:
        old_vid = c.get("visit_id")
        old_aid = c.get("activity_id")
        status = c.get("status", "").strip()
        if status == "":
            continue
        vid = visit_id_map.get(old_vid)
        aid = activity_id_map.get(old_aid)
        if vid and aid:
            cur.execute(
                "INSERT INTO cell (soa_id, visit_id, activity_id, status) VALUES (?,?,?,?)",
                (soa_id, vid, aid, status),
            )
            inserted_cells += 1
    # Reinsert concepts
    inserted_concepts = 0
    for old_aid, concept_list in concepts_map.items():
        new_aid = activity_id_map.get(int(old_aid))
        if not new_aid:
            continue
        for c in concept_list:
            code = c.get("code")
            title = c.get("title") or code
            if not code:
                continue
            cur.execute(
                "INSERT INTO activity_concept (activity_id, concept_code, concept_title) VALUES (?,?,?)",
                (new_aid, code, title),
            )
            inserted_concepts += 1
    conn.commit()
    conn.close()
    return {
        "rollback_freeze_id": freeze_id,
        "visits_restored": len(visits),
        "activities_restored": len(activities),
        "cells_restored": inserted_cells,
        "concept_mappings_restored": inserted_concepts,
    }


def _record_rollback_audit(soa_id: int, freeze_id: int, stats: dict):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rollback_audit (soa_id, freeze_id, performed_at, visits_restored, activities_restored, cells_restored, concepts_restored) VALUES (?,?,?,?,?,?,?)",
        (
            soa_id,
            freeze_id,
            datetime.now(timezone.utc).isoformat(),
            stats.get("visits_restored"),
            stats.get("activities_restored"),
            stats.get("cells_restored"),
            stats.get("concept_mappings_restored"),
        ),
    )
    conn.commit()
    conn.close()


def _record_reorder_audit(
    soa_id: int, entity_type: str, old_order: list[int], new_order: list[int]
):
    """Persist a reorder audit record if ordering truly changed.

    Parameters:
      soa_id: owning SoA id
            entity_type: 'visit' | 'activity' | 'epoch'
      old_order: list of IDs before reorder (ascending order_index)
      new_order: list of IDs after reorder (ascending order_index)
    """
    try:
        if old_order == new_order:
            return  # no change
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO reorder_audit (soa_id, entity_type, old_order_json, new_order_json, performed_at) VALUES (?,?,?,?,?)",
            (
                soa_id,
                entity_type,
                json.dumps(old_order),
                json.dumps(new_order),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except (
        Exception
    ) as e:  # pragma: no cover - audit failure should not break core flow
        logger.warning("Failed to record reorder audit: %s", e)


def _list_reorder_audit(soa_id: int) -> list[dict]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, entity_type, old_order_json, new_order_json, performed_at FROM reorder_audit WHERE soa_id=? ORDER BY id DESC",
        (soa_id,),
    )
    rows = [
        {
            "id": r[0],
            "entity_type": r[1],
            "old_order": json.loads(r[2]) if r[2] else [],
            "new_order": json.loads(r[3]) if r[3] else [],
            "performed_at": r[4],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return rows


def _list_rollback_audit(soa_id: int) -> list[dict]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, freeze_id, performed_at, visits_restored, activities_restored, cells_restored, concepts_restored FROM rollback_audit WHERE soa_id=? ORDER BY id DESC",
        (soa_id,),
    )
    rows = [
        {
            "id": r[0],
            "freeze_id": r[1],
            "performed_at": r[2],
            "visits_restored": r[3],
            "activities_restored": r[4],
            "cells_restored": r[5],
            "concepts_restored": r[6],
        }
        for r in cur.fetchall()
    ]
    conn.close()
    return rows


def _rollback_preview(soa_id: int, freeze_id: int) -> dict:
    freeze = _get_freeze(soa_id, freeze_id)
    if not freeze:
        raise HTTPException(404, "Freeze not found")
    snap = freeze["snapshot"]
    visits = snap.get("visits", [])
    activities = snap.get("activities", [])
    cells = [c for c in snap.get("cells", []) if c.get("status", "").strip() != ""]
    concepts_map = snap.get("activity_concepts", {}) or {}
    return {
        "freeze_id": freeze_id,
        "version_label": freeze.get("version_label"),
        "visits_to_restore": len(visits),
        "activities_to_restore": len(activities),
        "cells_to_restore": len(cells),
        "concept_mappings_to_restore": sum(len(v) for v in concepts_map.values()),
    }


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
    # Epochs not part of matrix axes currently; retrieved separately where needed.
    cur.execute(
        "SELECT id,name,raw_header,order_index,epoch_id FROM visit WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    visits = [
        dict(id=r[0], name=r[1], raw_header=r[2], order_index=r[3], epoch_id=r[4])
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


@app.post("/ui/soa/{soa_id}/concepts_refresh")
def ui_refresh_concepts(request: Request, soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    fetch_biomedical_concepts(force=True)
    # If HTMX request, use HX-Redirect header for clean redirect without injecting script
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": f"/ui/soa/{soa_id}/edit"})
    # Fallback: plain form POST non-htmx redirect via script
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/freeze", response_class=HTMLResponse)
def ui_freeze_soa(request: Request, soa_id: int, version_label: str = Form("")):
    try:
        _fid, _vlabel = _create_freeze(soa_id, version_label or None)
    except HTTPException as he:
        # Return inline error block for HTMX; simple alert fallback for non-HTMX
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


@app.get("/soa/{soa_id}/freeze/{freeze_id}")
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


@app.get("/ui/soa/{soa_id}/freeze/{freeze_id}/view", response_class=HTMLResponse)
def ui_freeze_view(request: Request, soa_id: int, freeze_id: int):
    freeze = _get_freeze(soa_id, freeze_id)
    if not freeze:
        raise HTTPException(404, "Freeze not found")
    return templates.TemplateResponse(
        "freeze_modal.html",
        {"request": request, "mode": "view", "freeze": freeze, "soa_id": soa_id},
    )


@app.get("/ui/soa/{soa_id}/freeze/diff", response_class=HTMLResponse)
def ui_freeze_diff(request: Request, soa_id: int, left: int, right: int, full: int = 0):
    limit = None if full == 1 else 50
    diff = _diff_freezes_limited(soa_id, left, right, limit=limit)
    return templates.TemplateResponse(
        "freeze_modal.html",
        {"request": request, "mode": "diff", "diff": diff, "soa_id": soa_id},
    )


@app.post("/ui/soa/{soa_id}/freeze/{freeze_id}/rollback", response_class=HTMLResponse)
def ui_freeze_rollback(request: Request, soa_id: int, freeze_id: int):
    result = _rollback_freeze(soa_id, freeze_id)
    _record_rollback_audit(
        soa_id,
        freeze_id,
        {
            "visits_restored": result["visits_restored"],
            "activities_restored": result["activities_restored"],
            "cells_restored": result["cells_restored"],
            "concept_mappings_restored": result["concept_mappings_restored"],
        },
    )
    # HTMX redirect back to edit with status message injected if desired later
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": f"/ui/soa/{soa_id}/edit"})
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.get(
    "/ui/soa/{soa_id}/freeze/{freeze_id}/rollback_preview", response_class=HTMLResponse
)
def ui_freeze_rollback_preview(request: Request, soa_id: int, freeze_id: int):
    preview = _rollback_preview(soa_id, freeze_id)
    freeze = _get_freeze(soa_id, freeze_id)
    return templates.TemplateResponse(
        "freeze_modal.html",
        {
            "request": request,
            "mode": "rollback_preview",
            "preview": preview,
            "freeze": freeze,
            "soa_id": soa_id,
        },
    )


@app.get("/soa/{soa_id}/freeze/diff.json")
def get_freeze_diff_json(soa_id: int, left: int, right: int, full: int = 0):
    limit = None if full == 1 else 1000  # large default for JSON
    diff = _diff_freezes_limited(soa_id, left, right, limit=limit)
    return JSONResponse(diff)


@app.get("/soa/{soa_id}/rollback_audit")
def get_rollback_audit_json(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    return {"audit": _list_rollback_audit(soa_id)}


@app.get("/soa/{soa_id}/reorder_audit")
def get_reorder_audit_json(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    return {"audit": _list_reorder_audit(soa_id)}


@app.get("/ui/soa/{soa_id}/rollback_audit", response_class=HTMLResponse)
def ui_rollback_audit(request: Request, soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    return templates.TemplateResponse(
        "rollback_audit_modal.html",
        {"request": request, "soa_id": soa_id, "audit": _list_rollback_audit(soa_id)},
    )


@app.get("/ui/soa/{soa_id}/reorder_audit", response_class=HTMLResponse)
def ui_reorder_audit(request: Request, soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    return templates.TemplateResponse(
        "reorder_audit_modal.html",
        {"request": request, "soa_id": soa_id, "audit": _list_reorder_audit(soa_id)},
    )


@app.get("/soa/{soa_id}/rollback_audit/export/xlsx")
def export_rollback_audit_xlsx(soa_id: int):
    """Export rollback audit history for the SoA to an Excel workbook."""
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    rows = _list_rollback_audit(soa_id)
    # Prepare DataFrame
    df = pd.DataFrame(rows)
    if df.empty:
        # Create empty frame with columns for consistency
        df = pd.DataFrame(
            columns=[
                "id",
                "freeze_id",
                "performed_at",
                "visits_restored",
                "activities_restored",
                "cells_restored",
                "concepts_restored",
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


@app.get("/soa/{soa_id}/reorder_audit/export/xlsx")
def export_reorder_audit_xlsx(soa_id: int):
    """Export reorder audit history (visit/activity reorders) to Excel."""
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    rows = _list_reorder_audit(soa_id)
    # Flatten old/new order arrays to strings for readability
    flat_rows = []
    for r in rows:
        moves = []
        old_pos = {vid: idx + 1 for idx, vid in enumerate(r.get("old_order", []))}
        new_order = r.get("new_order", [])
        for idx, vid in enumerate(new_order, start=1):
            op = old_pos.get(vid)
            if op and op != idx:
                moves.append(f"{vid}:{op}->{idx}")
        flat_rows.append(
            {
                "id": r.get("id"),
                "entity_type": r.get("entity_type"),
                "performed_at": r.get("performed_at"),
                "old_order": ",".join(map(str, r.get("old_order", []))),
                "new_order": ",".join(map(str, new_order)),
                "moves": "; ".join(moves) if moves else "",
            }
        )
    df = pd.DataFrame(flat_rows)
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


@app.get("/soa/{soa_id}/reorder_audit/export/csv")
def export_reorder_audit_csv(soa_id: int):
    """Export reorder audit history to CSV."""
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    rows = _list_reorder_audit(soa_id)
    # Prepare CSV lines
    header = ["id", "entity_type", "performed_at", "old_order", "new_order", "moves"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    for r in rows:
        old_order = r.get("old_order", [])
        new_order = r.get("new_order", [])
        moves = []
        old_pos = {vid: idx + 1 for idx, vid in enumerate(old_order)}
        for idx, vid in enumerate(new_order, start=1):
            op = old_pos.get(vid)
            if op and op != idx:
                moves.append(f"{vid}:{op}->{idx}")
        writer.writerow(
            [
                r.get("id"),
                r.get("entity_type"),
                r.get("performed_at"),
                ",".join(map(str, old_order)),
                ",".join(map(str, new_order)),
                "; ".join(moves) if moves else "",
            ]
        )
    output.seek(0)
    filename = f"soa_{soa_id}_reorder_audit.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    # Enforce unique study_id if provided
    if payload.study_id and payload.study_id.strip():
        cur.execute("SELECT 1 FROM soa WHERE study_id=?", (payload.study_id.strip(),))
        if cur.fetchone():
            conn.close()
            raise HTTPException(400, "study_id already exists")
    cur.execute(
        "INSERT INTO soa (name, created_at, study_id, study_label, study_description) VALUES (?,?,?,?,?)",
        (
            payload.name,
            datetime.now(timezone.utc).isoformat(),
            (payload.study_id or "").strip() or None,
            (payload.study_label or "").strip() or None,
            (payload.study_description or "").strip() or None,
        ),
    )
    soa_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {
        "id": soa_id,
        "name": payload.name,
        "study_id": payload.study_id,
        "study_label": payload.study_label,
        "study_description": payload.study_description,
    }


@app.get("/soa/{soa_id}")
def get_soa(soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    visits, activities, cells = _fetch_matrix(soa_id)
    # Fetch epochs
    conn_ep = _connect()
    cur_ep = conn_ep.cursor()
    cur_ep.execute(
        "SELECT id,name,order_index,epoch_seq,epoch_label,epoch_description FROM epoch WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    epochs = [
        dict(
            id=r[0],
            name=r[1],
            order_index=r[2],
            epoch_seq=r[3],
            epoch_label=r[4],
            epoch_description=r[5],
        )
        for r in cur_ep.fetchall()
    ]
    conn_ep.close()
    # Also include study metadata if present
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT study_id, study_label, study_description FROM soa WHERE id=?", (soa_id,)
    )
    meta_row = cur.fetchone()
    conn.close()
    study_meta = (
        {
            "study_id": meta_row[0],
            "study_label": meta_row[1],
            "study_description": meta_row[2],
        }
        if meta_row
        else {}
    )
    return {
        "id": soa_id,
        **study_meta,
        "epochs": epochs,
        "visits": visits,
        "activities": activities,
        "cells": cells,
    }


@app.post("/soa/{soa_id}/metadata")
def update_soa_metadata(soa_id: int, payload: SOAMetadataUpdate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    # Fetch current study_id to enforce non-blank persistence
    cur.execute("SELECT study_id FROM soa WHERE id=?", (soa_id,))
    row = cur.fetchone()
    current_study_id = row[0] if row else None
    proposed = (payload.study_id or "").strip()
    if proposed == "" and current_study_id:
        # Ignore clearing attempt – keep existing value
        new_study_id = current_study_id
    else:
        new_study_id = proposed or None
    if new_study_id:
        cur.execute(
            "SELECT id FROM soa WHERE study_id=? AND id<>?", (new_study_id, soa_id)
        )
        if cur.fetchone():
            conn.close()
            raise HTTPException(400, "study_id already exists")
    # If there was no previous study_id and none provided now, reject
    if not current_study_id and not new_study_id:
        conn.close()
        raise HTTPException(400, "study_id is required and cannot be blank")
    cur.execute(
        "UPDATE soa SET study_id=?, study_label=?, study_description=? WHERE id=?",
        (
            new_study_id,
            (payload.study_label or "").strip() or None,
            (payload.study_description or "").strip() or None,
            soa_id,
        ),
    )
    conn.commit()
    conn.close()
    return {"id": soa_id, "updated": True}


@app.post("/soa/{soa_id}/visits")
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


@app.post("/soa/{soa_id}/epochs")
def add_epoch(soa_id: int, payload: EpochCreate):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM epoch WHERE soa_id=?", (soa_id,))
    order_index = cur.fetchone()[0] + 1
    # Immutable sequence per study
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
    return {"epoch_id": eid, "order_index": order_index, "epoch_seq": next_seq}


@app.get("/soa/{soa_id}/epochs")
def list_epochs(soa_id: int):
    """Return ordered list of epoch metadata for a study."""
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


@app.get("/soa/{soa_id}/epochs/{epoch_id}")
def get_epoch(soa_id: int, epoch_id: int):
    """Return metadata for a single epoch."""
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


@app.post("/soa/{soa_id}/epochs/{epoch_id}/metadata")
def update_epoch_metadata(soa_id: int, epoch_id: int, payload: EpochUpdate):
    """Update mutable epoch metadata (name, label, description)."""
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM epoch WHERE id=? AND soa_id=?", (epoch_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Epoch not found")
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
    return {
        "id": row[0],
        "name": row[1],
        "order_index": row[2],
        "epoch_seq": row[3],
        "epoch_label": row[4],
        "epoch_description": row[5],
    }


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
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT concept_code, concept_title FROM activity_concept WHERE activity_id=?",
        (activity_id,),
    )
    rows = [{"code": c, "title": t} for c, t in cur.fetchall()]
    conn.close()
    return rows


@app.post(
    "/ui/soa/{soa_id}/activity/{activity_id}/concepts/add", response_class=HTMLResponse
)
def ui_add_activity_concept(
    request: Request, soa_id: int, activity_id: int, concept_code: str = Form(...)
):
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
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM activity WHERE id=? AND soa_id=?", (activity_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Activity not found")
    cur.execute(
        "SELECT 1 FROM activity_concept WHERE activity_id=? AND concept_code=?",
        (activity_id, code),
    )
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO activity_concept (activity_id, concept_code, concept_title) VALUES (?,?,?)",
            (activity_id, code, title),
        )
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


@app.post(
    "/ui/soa/{soa_id}/activity/{activity_id}/concepts/remove",
    response_class=HTMLResponse,
)
def ui_remove_activity_concept(
    request: Request, soa_id: int, activity_id: int, concept_code: str = Form(...)
):
    if not activity_id:
        raise HTTPException(400, "Missing activity_id")
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    code = concept_code.strip()
    if not code:
        raise HTTPException(400, "Empty concept_code")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM activity_concept WHERE activity_id=? AND concept_code=?",
        (activity_id, code),
    )
    conn.commit()
    conn.close()
    concepts = fetch_biomedical_concepts()
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
def export_xlsx(soa_id: int, left: Optional[int] = None, right: Optional[int] = None):
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
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT activity_id, concept_code, concept_title FROM activity_concept")
    concepts_map = {}
    for aid, code, title in cur.fetchall():
        concepts_map.setdefault(aid, {})[code] = title
    conn.close()
    visits, activities, _cells = _fetch_matrix(soa_id)
    activity_ids_in_order = [a["id"] for a in activities]
    # Build display strings using EffectiveTitle (override if present) and show code in parentheses
    concepts_strings = []
    for aid in activity_ids_in_order:
        cmap = concepts_map.get(aid, {})
        if not cmap:
            concepts_strings.append("")
            continue
        items = sorted(cmap.items(), key=lambda kv: kv[1].lower())
        concepts_strings.append(
            "; ".join([f"{title} ({code})" for code, title in items])
        )
    if len(concepts_strings) == len(df):
        df.insert(1, "Concepts", concepts_strings)
    # Build concept mappings sheet data
    mapping_rows = []
    for a in activities:
        aid = a["id"]
        cmap = concepts_map.get(aid, {})
        for code, title in cmap.items():
            mapping_rows.append([aid, a["name"], code, title])
    mapping_df = pd.DataFrame(
        mapping_rows,
        columns=["ActivityID", "ActivityName", "ConceptCode", "ConceptTitle"],
    )
    # Build rollback audit sheet data (optional)
    audit_rows = (
        _list_rollback_audit(soa_id) if "_list_rollback_audit" in globals() else []
    )
    audit_df = pd.DataFrame(audit_rows)
    if audit_df.empty:
        audit_df = pd.DataFrame(
            columns=[
                "id",
                "freeze_id",
                "performed_at",
                "visits_restored",
                "activities_restored",
                "cells_restored",
                "concepts_restored",
            ]
        )
    bio = io.BytesIO()
    # Prepare cover sheet metadata
    # Fetch study core metadata (name, study fields, created_at)
    conn_info = _connect()
    cur_info = conn_info.cursor()
    cur_info.execute(
        "SELECT name, created_at, study_id, study_label, study_description FROM soa WHERE id=?",
        (soa_id,),
    )
    info_row = cur_info.fetchone()
    conn_info.close()
    if info_row:
        soa_name_val, created_at_val, study_id_val, study_label_val, study_desc_val = (
            info_row
        )
    else:
        soa_name_val, created_at_val, study_id_val, study_label_val, study_desc_val = (
            f"SOA {soa_id}",
            None,
            None,
            None,
            None,
        )
    freezes = _list_freezes(soa_id)
    last_freeze_label = freezes[0]["version_label"] if freezes else None
    last_freeze_time = freezes[0]["created_at"] if freezes else None
    left_freeze = _get_freeze(soa_id, left) if left else None
    right_freeze = _get_freeze(soa_id, right) if right else None
    concept_mapping_count = len(mapping_rows)
    cell_count = len(cells)
    meta_rows = [
        ["Study ID", study_id_val or ""],
        ["Study Name", soa_name_val],
        ["Study Label", study_label_val or ""],
        ["Study Description", (study_desc_val or "")[:4000]],
        ["Created At", created_at_val or ""],
        ["Visit Count", str(len(visits))],
        ["Activity Count", str(len(activities))],
        ["Cell Count", str(cell_count)],
        ["Concept Mapping Count", str(concept_mapping_count)],
        ["Frozen Versions Count", str(len(freezes))],
        ["Latest Freeze Label", last_freeze_label or ""],
        ["Latest Freeze Time", last_freeze_time or ""],
    ]
    if left_freeze and right_freeze:
        meta_rows.extend(
            [
                ["Diff Left Label", left_freeze.get("version_label")],
                ["Diff Left Frozen At", left_freeze.get("created_at")],
                ["Diff Right Label", right_freeze.get("version_label")],
                ["Diff Right Frozen At", right_freeze.get("created_at")],
            ]
        )
    study_df = pd.DataFrame(meta_rows, columns=["Key", "Value"])
    # Optional concept diff sheet if left/right provided
    concept_diff_df = None
    if left and right:
        try:
            diff = _diff_freezes_limited(soa_id, left, right, limit=None)
            left_freeze = _get_freeze(soa_id, left)
            right_freeze = _get_freeze(soa_id, right)
            activity_name_lookup = {}
            if left_freeze:
                for a in left_freeze.get("snapshot", {}).get("activities", []):
                    if isinstance(a, dict):
                        activity_name_lookup[str(a.get("id"))] = a.get("name")
            if right_freeze:
                for a in right_freeze.get("snapshot", {}).get("activities", []):
                    if isinstance(a, dict):
                        activity_name_lookup[str(a.get("id"))] = a.get("name")
            diff_rows = []
            for ch in diff.get("concepts", []):
                aid = str(ch.get("activity_id"))
                aname = activity_name_lookup.get(aid, "")
                added = ", ".join(ch.get("added", []))
                removed = ", ".join(ch.get("removed", []))
                title_changes = "; ".join(
                    [
                        f"{tc['code']}: '{tc['old_title']}' -> '{tc['new_title']}'"
                        for tc in ch.get("title_changes", [])
                    ]
                )
                diff_rows.append([aid, aname, added, removed, title_changes])
            concept_diff_df = pd.DataFrame(
                diff_rows,
                columns=[
                    "ActivityID",
                    "ActivityName",
                    "AddedConceptCodes",
                    "RemovedConceptCodes",
                    "TitleChanges",
                ],
            )
            if concept_diff_df.empty:
                concept_diff_df = pd.DataFrame(
                    columns=[
                        "ActivityID",
                        "ActivityName",
                        "AddedConceptCodes",
                        "RemovedConceptCodes",
                        "TitleChanges",
                    ]
                )
        except Exception as e:
            # Provide an error sheet to highlight issue rather than failing entire export
            concept_diff_df = pd.DataFrame([[str(e)]], columns=["ConceptDiffError"])
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        study_df.to_excel(writer, index=False, sheet_name="Study")
        df.to_excel(writer, index=False, sheet_name="SoA")
        mapping_df.to_excel(writer, index=False, sheet_name="ConceptMappings")
        audit_df.to_excel(writer, index=False, sheet_name="RollbackAudit")
        if concept_diff_df is not None:
            concept_diff_df.to_excel(writer, index=False, sheet_name="ConceptDiff")
    bio.seek(0)
    # Dynamic filename pattern: studyid_version.xlsx
    # Determine study_id and version context
    conn_meta = _connect()
    cur_meta = conn_meta.cursor()
    cur_meta.execute("SELECT study_id FROM soa WHERE id=?", (soa_id,))
    row_meta = cur_meta.fetchone()
    conn_meta.close()
    study_id_val = (row_meta[0] if row_meta else None) or f"soa{soa_id}"
    # Sanitize study_id for filename (keep alnum, '-', '_')

    safe_study = (
        _re.sub(r"[^A-Za-z0-9_-]+", "-", study_id_val.strip())[:80] or f"soa{soa_id}"
    )
    version_segment = ""
    if left and right:
        # Diff export: include both labels
        left_f = _get_freeze(soa_id, left)
        right_f = _get_freeze(soa_id, right)
        left_label = left_f.get("version_label") if left_f else f"v{left}"
        right_label = right_f.get("version_label") if right_f else f"v{right}"
        version_segment = f"{left_label}_vs_{right_label}"
    else:
        freezes = _list_freezes(soa_id)
        if freezes:
            version_segment = freezes[0]["version_label"] or f"v{freezes[0]['id']}"
        else:
            # No freezes yet: assume initial version number 1
            version_segment = "v1"
    safe_version = _re.sub(r"[^A-Za-z0-9._-]+", "-", version_segment)[:60]
    filename = f"{safe_study}_{safe_version}.xlsx"
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


@app.delete("/soa/{soa_id}/epochs/{epoch_id}")
def delete_epoch(soa_id: int, epoch_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM epoch WHERE id=? AND soa_id=?", (epoch_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Epoch not found")
    cur.execute("DELETE FROM epoch WHERE id=?", (epoch_id,))
    conn.commit()
    conn.close()
    _reindex("epoch", soa_id)
    return {"deleted_epoch_id": epoch_id}


# --------------------- HTML UI Endpoints ---------------------


@app.get("/", response_class=HTMLResponse)
def ui_index(request: Request):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id,name,created_at,study_id,study_label,study_description FROM soa ORDER BY id DESC"
    )
    rows = cur.fetchall()
    conn.close()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "soas": [
                {
                    "id": r[0],
                    "name": r[1],
                    "created_at": r[2],
                    "study_id": r[3],
                    "study_label": r[4],
                    "study_description": r[5],
                }
                for r in rows
            ],
        },
    )


@app.post("/ui/soa/create", response_class=HTMLResponse)
def ui_create_soa(
    request: Request,
    name: str = Form(...),
    study_id: Optional[str] = Form(None),
    study_label: Optional[str] = Form(None),
    study_description: Optional[str] = Form(None),
):
    conn = _connect()
    cur = conn.cursor()
    # Uniqueness check
    if study_id and study_id.strip():
        cur.execute("SELECT 1 FROM soa WHERE study_id=?", (study_id.strip(),))
        if cur.fetchone():
            conn.close()
            return HTMLResponse(
                "<script>alert('study_id already exists');window.location='/'</script>"
            )
    cur.execute(
        "INSERT INTO soa (name, created_at, study_id, study_label, study_description) VALUES (?,?,?,?,?)",
        (
            name,
            datetime.now(timezone.utc).isoformat(),
            (study_id or "").strip() or None,
            (study_label or "").strip() or None,
            (study_description or "").strip() or None,
        ),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return HTMLResponse(f"<script>window.location='/ui/soa/{sid}/edit';</script>")


@app.post("/ui/soa/{soa_id}/update_meta", response_class=HTMLResponse)
def ui_update_meta(
    request: Request,
    soa_id: int,
    study_id: Optional[str] = Form(None),
    study_label: Optional[str] = Form(None),
    study_description: Optional[str] = Form(None),
):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT study_id FROM soa WHERE id=?", (soa_id,))
    row = cur.fetchone()
    current_study_id = row[0] if row else None
    proposed = (study_id or "").strip()
    if proposed == "" and current_study_id:
        new_study_id = current_study_id  # preserve existing
    else:
        new_study_id = proposed or None
    if new_study_id:
        cur.execute(
            "SELECT id FROM soa WHERE study_id=? AND id<>?", (new_study_id, soa_id)
        )
        if cur.fetchone():
            conn.close()
            return HTMLResponse(
                "<script>alert('study_id already exists');window.location='/ui/soa/%d/edit';</script>"
                % soa_id
            )
    if not current_study_id and not new_study_id:
        conn.close()
        return HTMLResponse(
            "<script>alert('study_id is required');window.location='/ui/soa/%d/edit';</script>"
            % soa_id
        )
    cur.execute(
        "UPDATE soa SET study_id=?, study_label=?, study_description=? WHERE id=?",
        (
            new_study_id,
            (study_label or "").strip() or None,
            (study_description or "").strip() or None,
            soa_id,
        ),
    )
    conn.commit()
    conn.close()
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.get("/ui/soa/{soa_id}/edit", response_class=HTMLResponse)
def ui_edit(request: Request, soa_id: int):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    visits, activities, cells = _fetch_matrix(soa_id)
    # Epochs list
    conn_ep = _connect()
    cur_ep = conn_ep.cursor()
    cur_ep.execute(
        "SELECT id,name,order_index,epoch_seq,epoch_label,epoch_description FROM epoch WHERE soa_id=? ORDER BY order_index",
        (soa_id,),
    )
    epochs = [
        dict(
            id=r[0],
            name=r[1],
            order_index=r[2],
            epoch_seq=r[3],
            epoch_label=r[4],
            epoch_description=r[5],
        )
        for r in cur_ep.fetchall()
    ]
    conn_ep.close()
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
    fetched_at = _concept_cache.get("fetched_at")
    last_fetch_iso = None
    last_fetch_relative = None
    if fetched_at:
        dt = datetime.fromtimestamp(fetched_at, tz=timezone.utc)
        last_fetch_iso = dt.isoformat()
        # Simple relative string (seconds/minutes/hours)
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            last_fetch_relative = f"{secs}s ago"
        elif secs < 3600:
            last_fetch_relative = f"{secs//60}m ago"
        else:
            last_fetch_relative = f"{secs//3600}h ago"
    freeze_list = _list_freezes(soa_id)
    last_frozen_at = freeze_list[0]["created_at"] if freeze_list else None
    # Study metadata for edit form
    conn_meta = _connect()
    cur_meta = conn_meta.cursor()
    cur_meta.execute(
        "SELECT study_id, study_label, study_description FROM soa WHERE id=?", (soa_id,)
    )
    meta_row = cur_meta.fetchone()
    conn_meta.close()
    study_meta = {
        "study_id": meta_row[0] if meta_row else None,
        "study_label": meta_row[1] if meta_row else None,
        "study_description": meta_row[2] if meta_row else None,
    }
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "soa_id": soa_id,
            "epochs": epochs,
            "visits": visits,
            "activities": activities_page,
            "cell_map": cell_map,
            "concepts": concepts,
            "activity_concepts": activity_concepts,
            "concepts_empty": len(concepts) == 0,
            "concepts_diag": concepts_diag,
            "concepts_last_fetch_iso": last_fetch_iso,
            "concepts_last_fetch_relative": last_fetch_relative,
            "freezes": freeze_list,
            "freeze_count": len(freeze_list),
            "last_frozen_at": last_frozen_at,
            **study_meta,
        },
    )


@app.post("/ui/soa/{soa_id}/add_visit", response_class=HTMLResponse)
def ui_add_visit(
    request: Request,
    soa_id: int,
    name: str = Form(...),
    raw_header: str = Form(""),
    epoch_id: Optional[int] = Form(None),
):
    add_visit(
        soa_id, VisitCreate(name=name, raw_header=raw_header or name, epoch_id=epoch_id)
    )
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/add_activity", response_class=HTMLResponse)
def ui_add_activity(request: Request, soa_id: int, name: str = Form(...)):
    add_activity(soa_id, ActivityCreate(name=name))
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/add_epoch", response_class=HTMLResponse)
def ui_add_epoch(
    request: Request,
    soa_id: int,
    name: str = Form(...),
    epoch_label: Optional[str] = Form(None),
    epoch_description: Optional[str] = Form(None),
):
    add_epoch(
        soa_id,
        EpochCreate(
            name=name,
            epoch_label=epoch_label or None,
            epoch_description=epoch_description or None,
        ),
    )
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/update_epoch", response_class=HTMLResponse)
def ui_update_epoch(
    request: Request,
    soa_id: int,
    epoch_id: int = Form(...),
    name: Optional[str] = Form(None),
    epoch_label: Optional[str] = Form(None),
    epoch_description: Optional[str] = Form(None),
):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    payload = EpochUpdate(
        name=name,
        epoch_label=epoch_label,
        epoch_description=epoch_description,
    )
    # Reuse API logic
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM epoch WHERE id=? AND soa_id=?", (epoch_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Epoch not found")
    conn.close()
    update_epoch_metadata(soa_id, epoch_id, payload)
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post(
    "/ui/soa/{soa_id}/activity/{activity_id}/concepts", response_class=HTMLResponse
)
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
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT concept_code, concept_title FROM activity_concept WHERE activity_id=?",
            (activity_id,),
        )
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


@app.get(
    "/ui/soa/{soa_id}/activity/{activity_id}/concepts_cell", response_class=HTMLResponse
)
def ui_activity_concepts_cell(
    request: Request, soa_id: int, activity_id: int, edit: int = 0
):
    # Defensive guard: if activity_id is somehow falsy (should not happen for valid int path param)
    # surface a clear 400 error rather than proceeding and causing confusing downstream behavior.
    if not activity_id:
        raise HTTPException(status_code=400, detail="Missing activity_id")
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    concepts = fetch_biomedical_concepts()
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT concept_code, concept_title FROM activity_concept WHERE activity_id=?",
        (activity_id,),
    )
    selected = [{"code": c, "title": t} for c, t in cur.fetchall()]
    conn.close()
    return HTMLResponse(
        templates.get_template("concepts_cell.html").render(
            request=request,
            soa_id=soa_id,
            activity_id=activity_id,
            concepts=concepts,
            selected_codes=[s["code"] for s in selected],
            selected_list=selected,
            edit=bool(edit),
        )
    )


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


@app.post("/ui/soa/{soa_id}/set_visit_epoch", response_class=HTMLResponse)
def ui_set_visit_epoch(
    request: Request,
    soa_id: int,
    visit_id: int = Form(...),
    epoch_id: Optional[int] = Form(None),
):
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM visit WHERE id=? AND soa_id=?", (visit_id, soa_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Visit not found")
    # Validate epoch (allow clearing with None)
    if epoch_id is not None:
        cur.execute("SELECT 1 FROM epoch WHERE id=? AND soa_id=?", (epoch_id, soa_id))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(400, "Invalid epoch_id for this SOA")
    cur.execute("UPDATE visit SET epoch_id=? WHERE id=?", (epoch_id, visit_id))
    conn.commit()
    conn.close()
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/delete_activity", response_class=HTMLResponse)
def ui_delete_activity(request: Request, soa_id: int, activity_id: int = Form(...)):
    delete_activity(soa_id, activity_id)
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/delete_epoch", response_class=HTMLResponse)
def ui_delete_epoch(request: Request, soa_id: int, epoch_id: int = Form(...)):
    delete_epoch(soa_id, epoch_id)
    return HTMLResponse(f"<script>window.location='/ui/soa/{soa_id}/edit';</script>")


@app.post("/ui/soa/{soa_id}/reorder_visits", response_class=HTMLResponse)
def ui_reorder_visits(request: Request, soa_id: int, order: str = Form("")):
    """Persist new visit ordering. 'order' is a comma-separated list of visit IDs in desired order."""
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    ids = [int(x) for x in order.split(",") if x.strip().isdigit()]
    if not ids:
        return HTMLResponse("Invalid order", status_code=400)
    conn = _connect()
    cur = conn.cursor()
    # Capture existing order BEFORE modifications
    cur.execute("SELECT id FROM visit WHERE soa_id=? ORDER BY order_index", (soa_id,))
    old_order = [r[0] for r in cur.fetchall()]
    # Validate membership
    cur.execute("SELECT id FROM visit WHERE soa_id=?", (soa_id,))
    existing = {r[0] for r in cur.fetchall()}
    if set(ids) - existing:
        conn.close()
        return HTMLResponse("Order contains invalid visit id", status_code=400)
    # Apply new order indices
    for idx, vid in enumerate(ids, start=1):
        cur.execute("UPDATE visit SET order_index=? WHERE id=?", (idx, vid))
    conn.commit()
    conn.close()
    _record_reorder_audit(soa_id, "visit", old_order, ids)
    return HTMLResponse("OK")


@app.post("/ui/soa/{soa_id}/reorder_activities", response_class=HTMLResponse)
def ui_reorder_activities(request: Request, soa_id: int, order: str = Form("")):
    """Persist new activity ordering. 'order' is a comma-separated list of activity IDs in desired order."""
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    ids = [int(x) for x in order.split(",") if x.strip().isdigit()]
    if not ids:
        return HTMLResponse("Invalid order", status_code=400)
    conn = _connect()
    cur = conn.cursor()
    # Capture previous order
    cur.execute(
        "SELECT id FROM activity WHERE soa_id=? ORDER BY order_index", (soa_id,)
    )
    old_order = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM activity WHERE soa_id=?", (soa_id,))
    existing = {r[0] for r in cur.fetchall()}
    if set(ids) - existing:
        conn.close()
        return HTMLResponse("Order contains invalid activity id", status_code=400)
    for idx, aid in enumerate(ids, start=1):
        cur.execute("UPDATE activity SET order_index=? WHERE id=?", (idx, aid))
    conn.commit()
    conn.close()
    _record_reorder_audit(soa_id, "activity", old_order, ids)
    return HTMLResponse("OK")


@app.post("/ui/soa/{soa_id}/reorder_epochs", response_class=HTMLResponse)
def ui_reorder_epochs(request: Request, soa_id: int, order: str = Form("")):
    """Persist new epoch ordering."""
    if not _soa_exists(soa_id):
        raise HTTPException(404, "SOA not found")
    ids = [int(x) for x in order.split(",") if x.strip().isdigit()]
    if not ids:
        return HTMLResponse("Invalid order", status_code=400)
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM epoch WHERE soa_id=? ORDER BY order_index", (soa_id,))
    old_order = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM epoch WHERE soa_id=?", (soa_id,))
    existing = {r[0] for r in cur.fetchall()}
    if set(ids) - existing:
        conn.close()
        return HTMLResponse("Order contains invalid epoch id", status_code=400)
    for idx, eid in enumerate(ids, start=1):
        cur.execute("UPDATE epoch SET order_index=? WHERE id=?", (idx, eid))
    conn.commit()
    conn.close()
    _record_reorder_audit(soa_id, "epoch", old_order, ids)
    return HTMLResponse("OK")


# --------------------- Entry ---------------------


def main():  # pragma: no cover
    import uvicorn

    uvicorn.run("soa_builder.web.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":  # pragma: no cover
    main()
