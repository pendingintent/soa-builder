"""Validation utilities for Schedule of Activities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

WEEK = 7


@dataclass
class ImagingEvent:
    visit_name: str
    nominal_day: int


IMAGING_KEYWORDS = ["ct", "mri", "pet", "scan", "imaging"]

WEEK_RE = re.compile(r"week\s*(\d+)", re.IGNORECASE)
DAY_RE = re.compile(r"day\s*(\d+)", re.IGNORECASE)
CYCLE_DAY1_RE = re.compile(r"cycle\s*(\d+)\s*day\s*1", re.IGNORECASE)


def derive_nominal_day(label: str) -> int:
    txt = label.lower()
    m = WEEK_RE.search(txt)
    if m:
        return int(m.group(1)) * WEEK
    m = DAY_RE.search(txt)
    if m:
        return int(m.group(1))
    m = CYCLE_DAY1_RE.search(txt)
    if m:
        cycle = int(m.group(1))
        return 1 + (cycle - 1) * 21
    if "screen" in txt:
        return 0
    return -1


def extract_imaging_events(
    visit_rows: List[Dict[str, str]],
    activity_rows: List[Dict[str, str]],
    visit_activity_rows: List[Dict[str, str]],
) -> List[ImagingEvent]:
    # Build lookup of activity categories or names
    imaging_activity_ids = set()
    for act in activity_rows:
        name = act.get("activity_name", "").lower()
        if any(k in name for k in IMAGING_KEYWORDS):
            imaging_activity_ids.add(act["activity_id"])
    events: List[ImagingEvent] = []
    visit_lookup = {v["visit_id"]: v for v in visit_rows}
    for va in visit_activity_rows:
        aid = va["activity_id"]
        if aid in imaging_activity_ids:
            vid = va["visit_id"]
            visit = visit_lookup.get(vid)
            if not visit:
                continue
            visit_name = visit.get("visit_name", "")
            nd = derive_nominal_day(visit_name)
            if nd >= 0:
                events.append(ImagingEvent(visit_name, nd))
    events.sort(key=lambda e: e.nominal_day)
    return events


def validate_imaging_schedule(
    events: List[ImagingEvent],
    expected_interval_weeks: int = 6,
    tolerance_days: int = 4,
) -> List[str]:
    issues: List[str] = []
    if not events:
        return ["No imaging events detected."]
    expected_interval_days = expected_interval_weeks * WEEK
    prev = events[0]
    for cur in events[1:]:
        delta = cur.nominal_day - prev.nominal_day
        if abs(delta - expected_interval_days) > tolerance_days:
            issues.append(
                f"Interval deviation: {prev.visit_name} -> {cur.visit_name} is {delta} days (expected {expected_interval_days}±{tolerance_days})."
            )
        prev = cur
    return issues
