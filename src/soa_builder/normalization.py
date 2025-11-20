"""Normalization logic for Schedule of Activities.

Expose function normalize_soa(input_csv, out_dir, sqlite_path=None) returning summary dict.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

VISIT_CODE_RE = re.compile(r"\(([^()]+)\)")
WINDOW_RANGE_RE = re.compile(r"\(([-+]?\d+)\s*to\s*([-+]?\d+)d\)")
WINDOW_PM_RE = re.compile(r"\((?:±|\+/-)(\d+)d\)")
DAY_PM_RE = re.compile(r"(\d+)±(\d+)d")
PM_SYMBOL_RE = re.compile(r"±(\d+)d")
REPEAT_PATTERNS = ["every 2 cycles", "q12w", "q3w", "every 12 weeks"]

CATEGORY_KEYWORDS = {
    "screening": ["screening"],
    "baseline": ["baseline", "day 1"],
    "treatment": ["cycle", "week", "day"],
    "follow_up": ["follow", "fu", "survival", "safety"],
    "eot": ["end of treatment", "eot"],
}

ACTIVITY_CATEGORY_KEYWORDS = {
    "labs": ["hematology", "cbc", "chemistry", "cmp", "urinalysis", "tumor markers"],
    "imaging": ["imaging", "ct/mri", "mri", "brain mri"],
    "dosing": [
        "study drug administration",
        "dose modifications",
        "premedication",
        "drug accountability",
    ],
    "admin": [
        "informed consent",
        "randomization",
        "concomitant medications",
        "demographics",
        "height",
        "weight",
        "pregnancy test",
    ],
    "safety": [
        "vital signs",
        "ecg",
        "echocardiogram",
        "muga",
        "adverse event",
        "physical exam",
    ],
    "pharmacokinetics": ["pharmacokinetics", "pk"],
    "pathology": [
        "tumor tissue",
        "biopsy",
        "archival tumor tissue",
        "baseline biopsy",
        "on-treatment biopsy",
    ],
    "patient_reported": ["patient-reported", "eortc"],
    "performance_status": ["ecog"],
    "drug_accountability": ["drug accountability"],
    "adverse_event": ["adverse event assessment"],
}


@dataclass
class Visit:
    visit_id: int
    raw_header: str
    visit_name: str
    visit_code: Optional[str]
    sequence_index: int
    window_lower: Optional[int]
    window_upper: Optional[int]
    repeat_pattern: Optional[str]
    category: Optional[str]


@dataclass
class Activity:
    activity_id: int
    activity_name: str


@dataclass
class VisitActivity:
    id: int
    visit_id: int
    activity_id: int
    status: str
    required_flag: int
    conditional_flag: int


@dataclass
class ActivityCategory:
    activity_id: int
    category: str


@dataclass
class ScheduleRule:
    rule_id: int
    pattern: str
    description: str
    source_type: str  # 'cell' or 'header'
    activity_id: Optional[int]
    visit_id: Optional[int]
    raw_text: str


# ----------------- helpers -----------------


def classify_visit(header: str) -> Optional[str]:
    h = header.lower()
    for cat, toks in CATEGORY_KEYWORDS.items():
        if any(t in h for t in toks):
            return cat
    return None


def parse_window(header: str) -> tuple[Optional[int], Optional[int]]:
    m = WINDOW_RANGE_RE.search(header)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = WINDOW_PM_RE.search(header)
    if m:
        val = int(m.group(1))
        return -val, val
    m = DAY_PM_RE.search(header)
    if m:
        center = int(m.group(1))
        pm = int(m.group(2))
        return center - pm, center + pm
    m = PM_SYMBOL_RE.search(header)
    if m:
        pm = int(m.group(1))
        return -pm, pm
    return None, None


def extract_visit_code(header: str) -> Optional[str]:
    codes = VISIT_CODE_RE.findall(header)
    if not codes:
        return None
    for c in codes:
        if re.search(r"C\d+D\d+|EOT|FU|C\d+", c):
            return c
    return codes[0]


def detect_repeat_pattern(cell_value: str) -> Optional[str]:
    v = cell_value.lower()
    for pat in REPEAT_PATTERNS:
        if pat in v:
            return pat
    return None


def classify_activity(name: str) -> str:
    n = name.lower()
    for cat, toks in ACTIVITY_CATEGORY_KEYWORDS.items():
        if any(t in n for t in toks):
            return cat
    return "other"


# ----------------- build functions -----------------


def build_visits(headers: List[str]) -> List[Visit]:
    visits: List[Visit] = []
    for idx, h in enumerate(headers[1:], start=1):
        wl, wu = parse_window(h)
        code = extract_visit_code(h)
        cat = classify_visit(h)
        visits.append(
            Visit(
                idx,
                h,
                re.sub(r"\s*\(.*?\)", "", h).strip(),
                code,
                idx,
                wl,
                wu,
                None,
                cat,
            )
        )
    return visits


def build_activities(rows: List[List[str]]) -> List[Activity]:
    acts: List[Activity] = []
    for i, r in enumerate(rows, start=1):
        name = r[0].strip() or f"Activity_{i}"
        acts.append(Activity(i, name))
    return acts


def build_visit_activities(
    rows: List[List[str]], visits: List[Visit]
) -> List[VisitActivity]:
    vas: List[VisitActivity] = []
    next_id = 1
    for a_idx, r in enumerate(rows, start=1):
        for v_idx, visit in enumerate(visits, start=1):
            if v_idx >= len(r):
                continue
            raw = r[v_idx].strip()
            if not raw:
                continue
            required = 1 if raw.startswith("X") else 0
            conditional = (
                1 if ("if indicated" in raw.lower() or "optional" in raw.lower()) else 0
            )
            rep_pat = detect_repeat_pattern(raw)
            if rep_pat and visit.repeat_pattern is None:
                visit.repeat_pattern = rep_pat
            vas.append(
                VisitActivity(
                    next_id, visit.visit_id, a_idx, raw, required, conditional
                )
            )
            next_id += 1
    return vas


def build_activity_categories(activities: List[Activity]) -> List[ActivityCategory]:
    return [
        ActivityCategory(a.activity_id, classify_activity(a.activity_name))
        for a in activities
    ]


def build_schedule_rules(
    rows: List[List[str]], visits: List[Visit]
) -> List[ScheduleRule]:
    rules: List[ScheduleRule] = []
    rid = 1
    # headers
    for v in visits:
        low = v.raw_header.lower()
        for pat in REPEAT_PATTERNS:
            if pat in low:
                rules.append(
                    ScheduleRule(
                        rid,
                        pat,
                        f"Visit-level repeating schedule: {pat}",
                        "header",
                        None,
                        v.visit_id,
                        v.raw_header,
                    )
                )
                rid += 1
    # cells
    for a_idx, r in enumerate(rows, start=1):
        for v_idx, v in enumerate(visits, start=1):
            if v_idx >= len(r):
                continue
            raw = r[v_idx].strip()
            if not raw:
                continue
            pat = detect_repeat_pattern(raw)
            if pat:
                rules.append(
                    ScheduleRule(
                        rid,
                        pat,
                        f"Activity-level repeating schedule: {pat}",
                        "cell",
                        a_idx,
                        v.visit_id,
                        raw,
                    )
                )
                rid += 1
    # de-duplicate
    unique = {}
    for r in rules:
        key = (r.pattern, r.source_type, r.activity_id, r.visit_id)
        if key not in unique:
            unique[key] = r
    return list(unique.values())


# ----------------- main normalization API -----------------


def normalize_soa(
    input_csv: str, out_dir: str, sqlite_path: Optional[str] = None
) -> Dict[str, Any]:
    with open(input_csv, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        raise ValueError("Empty CSV")
    header = rows[0]
    data_rows = rows[1:]
    visits = build_visits(header)
    activities = build_activities(data_rows)
    visit_activities = build_visit_activities(data_rows, visits)
    activity_categories = build_activity_categories(activities)
    schedule_rules = build_schedule_rules(data_rows, visits)

    os.makedirs(out_dir, exist_ok=True)

    def write(name: str, items: List[Any]):
        if not items:
            open(os.path.join(out_dir, name), "w").close()
            return
        fieldnames = list(asdict(items[0]).keys())
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for it in items:
                w.writerow(asdict(it))

    write("visits.csv", visits)
    write("activities.csv", activities)
    write("visit_activities.csv", visit_activities)
    write("activity_categories.csv", activity_categories)
    write("schedule_rules.csv", schedule_rules)

    if sqlite_path:
        conn = sqlite3.connect(sqlite_path)
        cur = conn.cursor()
        for tbl in [
            "schedule_rules",
            "activity_categories",
            "visit_activities",
            "activities",
            "visits",
        ]:
            cur.execute(f"DROP TABLE IF EXISTS {tbl}")
        cur.execute(
            """CREATE TABLE visits (visit_id INTEGER PRIMARY KEY, raw_header TEXT, visit_name TEXT, visit_code TEXT, sequence_index INTEGER, window_lower INTEGER, window_upper INTEGER, repeat_pattern TEXT, category TEXT)"""
        )
        cur.execute(
            """CREATE TABLE activities (activity_id INTEGER PRIMARY KEY, activity_name TEXT)"""
        )
        cur.execute(
            """CREATE TABLE visit_activities (id INTEGER PRIMARY KEY, visit_id INTEGER, activity_id INTEGER, status TEXT, required_flag INTEGER, conditional_flag INTEGER)"""
        )
        cur.execute(
            """CREATE TABLE activity_categories (activity_id INTEGER PRIMARY KEY, category TEXT)"""
        )
        cur.execute(
            """CREATE TABLE schedule_rules (rule_id INTEGER PRIMARY KEY, pattern TEXT, description TEXT, source_type TEXT, activity_id INTEGER, visit_id INTEGER, raw_text TEXT)"""
        )
        cur.executemany(
            "INSERT INTO visits VALUES (?,?,?,?,?,?,?,?,?)",
            [tuple(asdict(v).values()) for v in visits],
        )
        cur.executemany(
            "INSERT INTO activities VALUES (?,?)",
            [tuple(asdict(a).values()) for a in activities],
        )
        cur.executemany(
            "INSERT INTO visit_activities VALUES (?,?,?,?,?,?)",
            [tuple(asdict(va).values()) for va in visit_activities],
        )
        cur.executemany(
            "INSERT INTO activity_categories VALUES (?,?)",
            [tuple(asdict(c).values()) for c in activity_categories],
        )
        cur.executemany(
            "INSERT INTO schedule_rules VALUES (?,?,?,?,?,?,?)",
            [tuple(asdict(r).values()) for r in schedule_rules],
        )
        conn.commit()
        conn.close()

    return {
        "visits": len(visits),
        "activities": len(activities),
        "mappings": len(visit_activities),
        "categories": len(activity_categories),
        "rules": len(schedule_rules),
    }
