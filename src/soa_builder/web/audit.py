import json
from datetime import datetime, timezone
from .db import _connect
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("soa_builder.concepts")


def _record_arm_audit(
    soa_id: int,
    action: str,
    arm_id: int | None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO arm_audit (soa_id, arm_id, action, before_json, after_json, performed_at) VALUES (?,?,?,?,?,?)",
            (
                soa_id,
                arm_id,
                action,
                json.dumps(before) if before else None,
                json.dumps(after) if after else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed recording arm audit: %s", e)


def _record_element_audit(
    soa_id: int,
    action: str,
    element_id: int | None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO element_audit (soa_id, element_id, action, before_json, after_json, performed_at) VALUES (?,?,?,?,?,?)",
            (
                soa_id,
                element_id,
                action,
                json.dumps(before) if before else None,
                json.dumps(after) if after else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed recording element audit: %s", e)


def _record_reorder_audit(
    soa_id: int,
    entity_type: str,
    old_order: List[int],
    new_order: List[int],
):
    try:
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
    except Exception as e:
        logger.warning("Failed recording reorder audit: %s", e)


def _record_visit_audit(
    soa_id: int,
    action: str,
    visit_id: Optional[int],
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO visit_audit (soa_id, visit_id, action, before_json, after_json, performed_at) VALUES (?,?,?,?,?,?)",
            (
                soa_id,
                visit_id,
                action,
                json.dumps(before) if before else None,
                json.dumps(after) if after else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed recording visit audit: %s", e)


def _record_activity_audit(
    soa_id: int,
    action: str,
    activity_id: Optional[int],
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO activity_audit (soa_id, activity_id, action, before_json, after_json, performed_at) VALUES (?,?,?,?,?,?)",
            (
                soa_id,
                activity_id,
                action,
                json.dumps(before) if before else None,
                json.dumps(after) if after else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed recording activity audit: %s", e)
