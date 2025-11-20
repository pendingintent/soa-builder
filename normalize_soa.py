#!/usr/bin/env python
"""Normalize a Schedule of Activities (SoA) wide matrix into relational tables.

Input: Wide CSV with first column 'Activity' and subsequent columns representing visits/timepoints.
Outputs (in --out-dir):
    visits.csv
    activities.csv
    visit_activities.csv
    activity_categories.csv
    schedule_rules.csv
Optional: --sqlite to also create a SQLite database with all tables.

Heuristics:
- Any non-first header column is treated as a visit.
- Cell values meaning:
    'X' or starting with 'X' => required_flag = 1
    Contains 'Optional' => conditional_flag = 1
    Contains 'If indicated' => conditional_flag = 1
    Other non-empty values retained in status.
- Repeating patterns (e.g., 'Every 2 cycles', 'q12w', 'q3w', 'Every 12 weeks') extracted into schedule_rules.
- Visit code extracted from parentheses tokens like (C1D1), (EOT), etc.
- Windows parsed from patterns like '(-28 to -1d)' or '(±7d)' or '30±7d'. Center days inferred when present (e.g., 30±7d -> 23..37).
- Activity categories assigned via keyword heuristics (labs, imaging, dosing, admin, safety, pharmacokinetics, pathology, patient_reported, adverse_event, drug_accountability, physical_exam, performance_status).

Assumptions:
- CSV is well-formed and first row is header.
- No embedded commas in unquoted cells beyond standard CSV quoting.

Enhancements (future):
- Refine category mapping with controlled terminology (CDISC).
- Support rule recurrence expansion into concrete scheduled instances.
- Add endpoints linkage and CRF page mapping.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

try:
    import pandas as pd  # type: ignore
except ImportError:
    pd = None  # Fallback to csv module if pandas not installed

VISIT_CODE_RE = re.compile(r"\(([^()]+)\)")
WINDOW_RANGE_RE = re.compile(r"\(([-+]?\d+)\s*to\s*([-+]?\d+)d\)")
WINDOW_PM_RE = re.compile(r"\((?:±|\+/-)(\d+)d\)")
DAY_PM_RE = re.compile(r"(\d+)±(\d+)d")
PM_SYMBOL_RE = re.compile(r"±(\d+)d")


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


CATEGORY_KEYWORDS = {
    "screening": ["screening"],
    "baseline": ["baseline", "day 1"],
    "treatment": ["cycle", "week", "day"],
    "follow_up": ["follow", "fu", "survival", "safety"],
    "eot": ["end of treatment", "eot"],
}

REPEAT_PATTERNS = ["every 2 cycles", "q12w", "q3w", "every 12 weeks"]


def classify_visit(header: str) -> Optional[str]:
    h = header.lower()
    for cat, toks in CATEGORY_KEYWORDS.items():
        if any(t in h for t in toks):
            return cat
    return None


def parse_window(header: str) -> tuple[Optional[int], Optional[int]]:
    # (-28 to -1d)
    m = WINDOW_RANGE_RE.search(header)
    if m:
        return int(m.group(1)), int(m.group(2))
    # (±7d) pattern
    m = WINDOW_PM_RE.search(header)
    if m:
        val = int(m.group(1))
        return -val, val
    # 30±7d pattern (no parentheses sometimes inside follow-up descriptor)
    m = DAY_PM_RE.search(header)
    if m:
        center = int(m.group(1))
        pm = int(m.group(2))
        return center - pm, center + pm
    # ±7d inside parentheses after a number like Safety FU (30±7d)
    m = PM_SYMBOL_RE.search(header)
    if m:
        pm = int(m.group(1))
        return -pm, pm  # Without center value known
    return None, None


def extract_visit_code(header: str) -> Optional[str]:
    codes = VISIT_CODE_RE.findall(header)
    if not codes:
        return None
    # Return first code that looks like a visit code
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


def classify_activity(name: str) -> str:
    n = name.lower()
    for cat, toks in ACTIVITY_CATEGORY_KEYWORDS.items():
        if any(t in n for t in toks):
            return cat
    return "other"


def load_csv(path: str) -> tuple[List[str], List[List[str]]]:
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        raise ValueError("CSV is empty")
    header = rows[0]
    data_rows = rows[1:]
    return header, data_rows


def build_visits(headers: List[str]) -> List[Visit]:
    visits: List[Visit] = []
    for idx, h in enumerate(headers[1:], start=1):  # skip Activity column
        wl, wu = parse_window(h)
        code = extract_visit_code(h)
        cat = classify_visit(h)
        visits.append(
            Visit(
                visit_id=idx,
                raw_header=h,
                visit_name=re.sub(r"\s*\(.*?\)", "", h).strip(),
                visit_code=code,
                sequence_index=idx,
                window_lower=wl,
                window_upper=wu,
                repeat_pattern=None,
                category=cat,
            )
        )
    return visits


def build_activities(rows: List[List[str]]) -> List[Activity]:
    acts: List[Activity] = []
    for i, r in enumerate(rows, start=1):
        name = r[0].strip()
        if not name:
            name = f"Activity_{i}"
        acts.append(Activity(activity_id=i, activity_name=name))
    return acts


def build_visit_activities(
    rows: List[List[str]], visits: List[Visit]
) -> List[VisitActivity]:
    vas: List[VisitActivity] = []
    next_id = 1
    for a_idx, r in enumerate(rows, start=1):
        activity_name = r[0]
        for v_idx, visit in enumerate(visits, start=1):
            if v_idx >= len(r):
                continue
            raw = r[v_idx].strip()
            if not raw:
                continue
            status = raw
            required = 1 if raw.startswith("X") else 0
            conditional = (
                1 if ("if indicated" in raw.lower() or "optional" in raw.lower()) else 0
            )
            rep_pat = detect_repeat_pattern(raw)
            if rep_pat and visit.repeat_pattern is None:
                # annotate visit repeat pattern once
                visit.repeat_pattern = rep_pat
            vas.append(
                VisitActivity(
                    id=next_id,
                    visit_id=visit.visit_id,
                    activity_id=a_idx,
                    status=status,
                    required_flag=required,
                    conditional_flag=conditional,
                )
            )
            next_id += 1
    return vas


def build_activity_categories(activities: List[Activity]) -> List[ActivityCategory]:
    cats: List[ActivityCategory] = []
    for a in activities:
        cats.append(
            ActivityCategory(
                activity_id=a.activity_id, category=classify_activity(a.activity_name)
            )
        )
    return cats


def build_schedule_rules(
    rows: List[List[str]], visits: List[Visit], activities: List[Activity]
) -> List[ScheduleRule]:
    rules: List[ScheduleRule] = []
    rule_id = 1
    # From headers (e.g., Survival FU (q12w))
    for v in visits:
        header_lower = v.raw_header.lower()
        for pat in REPEAT_PATTERNS:
            if pat in header_lower:
                rules.append(
                    ScheduleRule(
                        rule_id=rule_id,
                        pattern=pat,
                        description=f"Visit-level repeating schedule: {pat}",
                        source_type="header",
                        activity_id=None,
                        visit_id=v.visit_id,
                        raw_text=v.raw_header,
                    )
                )
                rule_id += 1
    # From cells
    for a_idx, r in enumerate(rows, start=1):
        for v_idx, visit in enumerate(visits, start=1):
            if v_idx >= len(r):
                continue
            raw = r[v_idx].strip()
            if not raw:
                continue
            pat = detect_repeat_pattern(raw)
            if pat:
                rules.append(
                    ScheduleRule(
                        rule_id=rule_id,
                        pattern=pat,
                        description=f"Activity-level repeating schedule: {pat}",
                        source_type="cell",
                        activity_id=a_idx,
                        visit_id=visit.visit_id,
                        raw_text=raw,
                    )
                )
                rule_id += 1
    # De-duplicate by (pattern, source_type, activity_id, visit_id)
    unique = {}
    for r in rules:
        key = (r.pattern, r.source_type, r.activity_id, r.visit_id)
        if key not in unique:
            unique[key] = r
    return list(unique.values())


def write_csv(path: str, rows: List[Dict[str, Any]]):
    if not rows:
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def to_sqlite(
    db_path: str,
    visits: List[Visit],
    activities: List[Activity],
    vas: List[VisitActivity],
    activity_categories: List[ActivityCategory],
    schedule_rules: List[ScheduleRule],
):
    import sqlite3

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Drop existing tables to allow re-runs without UNIQUE constraint failures
    cur.execute("DROP TABLE IF EXISTS schedule_rules")
    cur.execute("DROP TABLE IF EXISTS activity_categories")
    cur.execute("DROP TABLE IF EXISTS visit_activities")
    cur.execute("DROP TABLE IF EXISTS activities")
    cur.execute("DROP TABLE IF EXISTS visits")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS visits (
            visit_id INTEGER PRIMARY KEY,
            raw_header TEXT,
            visit_name TEXT,
            visit_code TEXT,
            sequence_index INTEGER,
            window_lower INTEGER,
            window_upper INTEGER,
            repeat_pattern TEXT,
            category TEXT
        )"""
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS activities (
            activity_id INTEGER PRIMARY KEY,
            activity_name TEXT
        )"""
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS visit_activities (
            id INTEGER PRIMARY KEY,
            visit_id INTEGER,
            activity_id INTEGER,
            status TEXT,
            required_flag INTEGER,
            conditional_flag INTEGER,
            FOREIGN KEY (visit_id) REFERENCES visits(visit_id),
            FOREIGN KEY (activity_id) REFERENCES activities(activity_id)
        )"""
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_categories (
            activity_id INTEGER PRIMARY KEY,
            category TEXT,
            FOREIGN KEY (activity_id) REFERENCES activities(activity_id)
        )"""
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_rules (
            rule_id INTEGER PRIMARY KEY,
            pattern TEXT,
            description TEXT,
            source_type TEXT,
            activity_id INTEGER,
            visit_id INTEGER,
            raw_text TEXT,
            FOREIGN KEY (activity_id) REFERENCES activities(activity_id),
            FOREIGN KEY (visit_id) REFERENCES visits(visit_id)
        )"""
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
        [tuple(asdict(va).values()) for va in vas],
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


def main():
    ap = argparse.ArgumentParser(
        description="Normalize SoA wide CSV into relational tables"
    )
    ap.add_argument("--input", required=True, help="Path to wide SoA CSV")
    ap.add_argument(
        "--out-dir", required=True, help="Directory to write normalized outputs"
    )
    ap.add_argument(
        "--sqlite",
        help="Optional path to SQLite DB to create (e.g., normalized/soa.db)",
    )
    args = ap.parse_args()

    header, rows = load_csv(args.input)
    visits = build_visits(header)
    activities = build_activities(rows)
    visit_activities = build_visit_activities(rows, visits)
    activity_categories = build_activity_categories(activities)
    schedule_rules = build_schedule_rules(rows, visits, activities)

    os.makedirs(args.out_dir, exist_ok=True)
    write_csv(os.path.join(args.out_dir, "visits.csv"), [asdict(v) for v in visits])
    write_csv(
        os.path.join(args.out_dir, "activities.csv"), [asdict(a) for a in activities]
    )
    write_csv(
        os.path.join(args.out_dir, "visit_activities.csv"),
        [asdict(va) for va in visit_activities],
    )
    write_csv(
        os.path.join(args.out_dir, "activity_categories.csv"),
        [asdict(c) for c in activity_categories],
    )
    write_csv(
        os.path.join(args.out_dir, "schedule_rules.csv"),
        [asdict(r) for r in schedule_rules],
    )

    if args.sqlite:
        to_sqlite(
            args.sqlite,
            visits,
            activities,
            visit_activities,
            activity_categories,
            schedule_rules,
        )

    # Basic summary
    print(
        f"Visits: {len(visits)} | Activities: {len(activities)} | Mappings: {len(visit_activities)} | Categories: {len(activity_categories)} | Rules: {len(schedule_rules)}"
    )
    # Show sample of first few mappings
    for va in visit_activities[:5]:
        print(
            f"VA {va.id}: visit {va.visit_id} activity {va.activity_id} status='{va.status}' required={va.required_flag} conditional={va.conditional_flag}"
        )


if __name__ == "__main__":
    main()
