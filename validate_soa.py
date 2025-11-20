#!/usr/bin/env python
"""Validate normalized SoA artifacts.

Checks implemented:
1. Imaging schedule: baseline + expected Week 6, 12, 18 imaging occurrences.
2. Interval consistency: approximate midpoints of windows between imaging visits should align with expected ~6 week spacing (tolerance configurable).

Exit code: 0 if all checks pass, 1 if any fail.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys

import pandas as pd

IMAGING_KEYWORDS = ["imaging (ct/mri", "imaging"]
EXPECTED_IMAGING_COUNT = 4  # baseline + week6 + week12 + week18
EXPECTED_INTERVAL_WEEKS = 6
WEEK_TO_DAYS = 7
DEFAULT_TOLERANCE_DAYS = 10  # allow variability due to windows


def load_normalized(dir_path: str):
    visits = pd.read_csv(os.path.join(dir_path, "visits.csv"))
    activities = pd.read_csv(os.path.join(dir_path, "activities.csv"))
    va = pd.read_csv(os.path.join(dir_path, "visit_activities.csv"))
    return visits, activities, va


def find_imaging_activity_ids(activities: pd.DataFrame):
    ids = []
    for _, row in activities.iterrows():
        name = row["activity_name"].lower()
        if any(k in name for k in IMAGING_KEYWORDS):
            ids.append(row["activity_id"])
    return ids


WEEK_RE = re.compile(r"week\s*(\d+)", re.IGNORECASE)
DAY_RE = re.compile(r"day\s*(\d+)", re.IGNORECASE)


def derive_nominal_day(row) -> float:
    """Attempt to infer a nominal study day for a visit.
    Priority:
      1. Week N -> N*7
      2. Day N -> N
      3. Screening -> 0 (treat baseline reference)
      4. Use window midpoint if both bounds present
      5. Fallback to sequence_index * 7 (coarse)
    """
    name = str(row.get("visit_name", "")).lower()
    header = str(row.get("raw_header", "")).lower()
    for text in (name, header):
        m = WEEK_RE.search(text)
        if m:
            return int(m.group(1)) * 7
        m = DAY_RE.search(text)
        if m:
            return int(m.group(1))
    if "screening" in name:
        return 0.0
    wl = row.get("window_lower")
    wu = row.get("window_upper")
    if math.isfinite(wl) and math.isfinite(wu):
        return (wl + wu) / 2.0
    # Coarse fallback: assume each sequence index spaced a week
    return float(row["sequence_index"]) * 7.0


def validate_imaging(
    visits: pd.DataFrame, va: pd.DataFrame, imaging_ids: list[int], tolerance_days: int
):
    if not imaging_ids:
        return False, ["No imaging activity detected."]
    imaging_va = va[va["activity_id"].isin(imaging_ids) & (va["required_flag"] == 1)]
    if imaging_va.empty:
        return False, ["No required imaging entries found."]
    # Merge to get visit info
    imaging_visits = imaging_va.merge(
        visits, left_on="visit_id", right_on="visit_id", how="left"
    )
    imaging_visits = imaging_visits.sort_values("sequence_index")
    errors = []
    if len(imaging_visits) < EXPECTED_IMAGING_COUNT:
        errors.append(
            f"Expected >= {EXPECTED_IMAGING_COUNT} required imaging visits; found {len(imaging_visits)}."
        )
    # Compute intervals
    nominal_days = [derive_nominal_day(r) for _, r in imaging_visits.iterrows()]
    for i in range(1, len(nominal_days)):
        delta = nominal_days[i] - nominal_days[i - 1]
        expected_days = EXPECTED_INTERVAL_WEEKS * WEEK_TO_DAYS
        if abs(delta - expected_days) > tolerance_days:
            vnames = imaging_visits.iloc[[i - 1, i]]["visit_name"].tolist()
            errors.append(
                f"Imaging interval between {vnames[0]} and {vnames[1]} is ~{delta:.1f}d (expected ~{expected_days}d ±{tolerance_days}d)."
            )
    return len(errors) == 0, errors


def main():
    ap = argparse.ArgumentParser(description="Validate normalized SoA schedule")
    ap.add_argument(
        "--dir", required=True, help="Directory containing normalized CSV outputs"
    )
    ap.add_argument(
        "--tolerance-days",
        type=int,
        default=DEFAULT_TOLERANCE_DAYS,
        help="Tolerance for imaging interval deviations",
    )
    args = ap.parse_args()

    visits, activities, va = load_normalized(args.dir)
    imaging_ids = find_imaging_activity_ids(activities)
    imaging_ok, imaging_errors = validate_imaging(
        visits, va, imaging_ids, args.tolerance_days
    )

    overall_ok = imaging_ok
    if overall_ok:
        print("VALIDATION PASSED")
    else:
        print("VALIDATION FAILED")
    if imaging_errors:
        print("Imaging checks:")
        for e in imaging_errors:
            print(f" - {e}")
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
