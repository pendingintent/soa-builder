"""Schedule expansion utilities with logging and safeguards.

Features:
 - Logging (DEBUG when enabled via CLI)
 - Skips malformed or zero-interval patterns safely
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import logging
from typing import List, Optional, Dict

WEEK = 7
logger = logging.getLogger(__name__)
PATTERN_CYCLE_RE = re.compile(r"every\s+(\d+)\s+cycles?", re.IGNORECASE)
PATTERN_QW_RE = re.compile(r"q(\d+)w", re.IGNORECASE)
PATTERN_EVERY_WEEKS_RE = re.compile(r"every\s+(\d+)\s+weeks?", re.IGNORECASE)
PATTERN_QD_RE = re.compile(r"q(\d+)d", re.IGNORECASE)
CYCLE_DAY1_RE = re.compile(r"cycle\s*(\d+)\s*day\s*1", re.IGNORECASE)


@dataclass
class VisitStub:
    visit_id: int
    visit_name: str
    raw_header: str
    sequence_index: int


@dataclass
class RuleStub:
    rule_id: int
    pattern: str
    description: str
    source_type: str
    activity_id: Optional[int]
    visit_id: Optional[int]
    raw_text: str


@dataclass
class Instance:
    instance_id: int
    rule_id: int
    pattern: str
    occurrence_index: int
    anchor_visit_id: Optional[int]
    anchor_activity_id: Optional[int]
    nominal_day: int
    projected_date: str
    source_type: str
    description: str


def parse_pattern_interval_days(pattern: str, cycle_length_days: int) -> Optional[int]:
    p = pattern.lower()
    m = PATTERN_CYCLE_RE.search(p)
    if m:
        return int(m.group(1)) * cycle_length_days
    m = PATTERN_QW_RE.search(p)
    if m:
        return int(m.group(1)) * WEEK
    m = PATTERN_EVERY_WEEKS_RE.search(p)
    if m:
        return int(m.group(1)) * WEEK
    m = PATTERN_QD_RE.search(p)
    if m:
        return int(m.group(1))
    if p == "q3w":
        return 3 * WEEK
    if p == "q12w":
        return 12 * WEEK
    if p == "every 2 cycles":
        return 2 * cycle_length_days
    if p == "every 12 weeks":
        return 12 * WEEK
    return None


def get_cycle_start_day(cycle_num: int, cycle_lengths: List[int]) -> int:
    return 1 + sum(cycle_lengths[: max(0, cycle_num - 1)])


def derive_nominal_day_for_visit(
    v: VisitStub, cycle_length_days: int, cycle_lengths: Optional[List[int]]
) -> int:
    name = v.visit_name.lower()
    header = v.raw_header.lower()
    for txt in (name, header):
        mc = CYCLE_DAY1_RE.search(txt)
        if mc:
            cnum = int(mc.group(1))
            if cycle_lengths:
                return get_cycle_start_day(cnum, cycle_lengths)
            return 1 + (cnum - 1) * cycle_length_days
        mw = re.search(r"week\s*(\d+)", txt)
        if mw:
            return int(mw.group(1)) * WEEK
        md = re.search(r"day\s*(\d+)", txt)
        if md:
            return int(md.group(1))
        if "screening" in txt:
            return 0
    return v.sequence_index * WEEK


def get_horizon_days(
    num_cycles: int,
    cycle_length_days: int,
    followup_weeks: int,
    cycle_lengths: Optional[List[int]],
) -> int:
    if cycle_lengths:
        ext = cycle_lengths[:num_cycles] + (
            [cycle_lengths[-1]] * max(0, num_cycles - len(cycle_lengths))
        )
        treatment = sum(ext)
    else:
        treatment = num_cycles * cycle_length_days
    followup = followup_weeks * WEEK
    return max(treatment, followup)


def expand_schedule_rules(
    rules: List[RuleStub],
    visits: Dict[int, VisitStub],
    start_date: datetime,
    cycle_length_days: int = 21,
    num_cycles: int = 8,
    followup_weeks: int = 104,
    horizon_days: Optional[int] = None,
    cycle_lengths: Optional[List[int]] = None,
    max_occurrences: Optional[int] = None,
    filter_patterns: Optional[List[str]] = None,
) -> List[Instance]:
    results: List[Instance] = []
    filter_set = {p.lower() for p in filter_patterns} if filter_patterns else None
    for rule in rules:
        if filter_set and rule.pattern.lower() not in filter_set:
            continue
        interval_days = parse_pattern_interval_days(rule.pattern, cycle_length_days)
        if interval_days is None:
            logger.debug(
                f"Skipping rule {rule.rule_id}: unrecognized pattern '{rule.pattern}'"
            )
            continue
        if interval_days <= 0:
            logger.debug(
                f"Skipping rule {rule.rule_id}: non-positive interval derived from '{rule.pattern}' -> {interval_days}"
            )
            continue
        if horizon_days is None:
            horizon = get_horizon_days(
                num_cycles, cycle_length_days, followup_weeks, cycle_lengths
            )
        else:
            horizon = horizon_days
        anchor_day = 1
        if rule.visit_id and rule.visit_id in visits:
            anchor_day = derive_nominal_day_for_visit(
                visits[rule.visit_id], cycle_length_days, cycle_lengths
            )
        cycle_based = (
            "cycle" in rule.pattern.lower() and "every" in rule.pattern.lower()
        )
        occ_idx = 0
        if cycle_based and cycle_lengths:
            m = PATTERN_CYCLE_RE.search(rule.pattern.lower())
            if not m:
                logger.debug(
                    f"Skipping rule {rule.rule_id}: cycle pattern not matched '{rule.pattern}'"
                )
                continue
            cycle_interval = int(m.group(1))
            if cycle_interval <= 0:
                logger.debug(
                    f"Skipping rule {rule.rule_id}: non-positive cycle interval {cycle_interval}"
                )
                continue
            anchor_cycle = 1
            if rule.visit_id and rule.visit_id in visits:
                txt = (
                    visits[rule.visit_id].visit_name
                    + " "
                    + visits[rule.visit_id].raw_header
                ).lower()
                mc = CYCLE_DAY1_RE.search(txt)
                if mc:
                    anchor_cycle = int(mc.group(1))
            k = 1
            while True:
                target_cycle = anchor_cycle + cycle_interval * k
                nominal_day = get_cycle_start_day(target_cycle, cycle_lengths)
                if nominal_day - anchor_day > horizon:
                    break
                occ_idx += 1
                projected = start_date + timedelta(days=nominal_day - 1)
                results.append(
                    Instance(
                        0,
                        rule.rule_id,
                        rule.pattern,
                        occ_idx,
                        rule.visit_id,
                        rule.activity_id,
                        nominal_day,
                        projected.date().isoformat(),
                        rule.source_type,
                        rule.description,
                    )
                )
                if max_occurrences and occ_idx >= max_occurrences:
                    break
                k += 1
        else:
            next_day = anchor_day + (interval_days if cycle_based else 0)
            while next_day - anchor_day <= horizon:
                occ_idx += 1
                projected = start_date + timedelta(days=next_day - 1)
                results.append(
                    Instance(
                        0,
                        rule.rule_id,
                        rule.pattern,
                        occ_idx,
                        rule.visit_id,
                        rule.activity_id,
                        next_day,
                        projected.date().isoformat(),
                        rule.source_type,
                        rule.description,
                    )
                )
                if max_occurrences and occ_idx >= max_occurrences:
                    break
                next_day += interval_days
    for inst in results:
        inst.instance_id = inst.rule_id * 10000 + inst.occurrence_index
    logger.debug(
        f"Expanded total instances: {len(results)} from {len(rules)} rules (after filtering/skips)"
    )
    return results
    # assign ids for this rule
    for inst in results:
        inst.instance_id = inst.rule_id * 10000 + inst.occurrence_index
    return results
