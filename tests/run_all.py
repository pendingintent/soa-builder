import os, pathlib, tempfile, csv
from datetime import datetime

from soa_builder import normalize_soa, expand_schedule_rules, validate_imaging_schedule
from soa_builder.schedule import RuleStub, VisitStub
from soa_builder.validation import extract_imaging_events, ImagingEvent

BASE = pathlib.Path(__file__).resolve().parents[1]
CSV_PATH = BASE / "files" / "SoA_breast_cancer.csv"

assert CSV_PATH.exists(), "Missing input CSV"

passed = []
failed = []

try:
    with tempfile.TemporaryDirectory() as tmp:
        summary = normalize_soa(str(CSV_PATH), tmp, None)
        assert summary["visits"] > 0 and summary["activities"] > 0
        passed.append("normalization")

        # load rules + visits
        def read_dict(path):
            with open(path, "r", encoding="utf-8") as f:
                return list(csv.DictReader(f))

        rules_rows = read_dict(os.path.join(tmp, "schedule_rules.csv"))
        visit_rows = read_dict(os.path.join(tmp, "visits.csv"))
        rules = [
            RuleStub(
                rule_id=int(r["rule_id"]),
                pattern=r["pattern"],
                description=r.get("description", ""),
                source_type=r.get("source_type", ""),
                activity_id=int(r["activity_id"]) if r.get("activity_id") else None,
                visit_id=int(r["visit_id"]) if r.get("visit_id") else None,
                raw_text=r.get("raw_text", ""),
            )
            for r in rules_rows
        ]
        visits = {
            int(v["visit_id"]): VisitStub(
                visit_id=int(v["visit_id"]),
                visit_name=v.get("visit_name", ""),
                raw_header=v.get("raw_header", ""),
                sequence_index=int(v.get("sequence_index", "0")),
            )
            for v in visit_rows
        }
        instances = expand_schedule_rules(
            rules, visits, start_date=datetime(2025, 1, 1)
        )
        if rules:
            assert instances, "Expected instances from rules"
        passed.append("schedule_expansion")
        # Edge: no rules
        empty_instances = expand_schedule_rules(
            [], visits, start_date=datetime(2025, 1, 1)
        )
        assert empty_instances == []
        passed.append("no_rules_edge")
        # Edge: malformed pattern
        malformed = RuleStub(
            rule_id=999,
            pattern="qXYZw",
            description="bad",
            source_type="cell",
            activity_id=None,
            visit_id=None,
            raw_text="qXYZw",
        )
        malformed_instances = expand_schedule_rules(
            [malformed], visits, start_date=datetime(2025, 1, 1)
        )
        assert malformed_instances == []
        passed.append("malformed_pattern_edge")
        # Edge: zero interval pattern
        zero = RuleStub(
            rule_id=1000,
            pattern="q0w",
            description="zero",
            source_type="cell",
            activity_id=None,
            visit_id=None,
            raw_text="q0w",
        )
        zero_instances = expand_schedule_rules(
            [zero], visits, start_date=datetime(2025, 1, 1)
        )
        assert zero_instances == []
        passed.append("zero_interval_edge")
        # validation
        activities_rows = read_dict(os.path.join(tmp, "activities.csv"))
        visit_activity_rows = read_dict(os.path.join(tmp, "visit_activities.csv"))
        events = extract_imaging_events(
            visit_rows, activities_rows, visit_activity_rows
        )
        issues = validate_imaging_schedule(events)
        assert events is not None and issues is not None
        passed.append("validation")
        # Imaging gap deviation check
        custom_events = [ImagingEvent("Baseline", 0), ImagingEvent("Late Imaging", 60)]
        deviation_issues = validate_imaging_schedule(
            custom_events, expected_interval_weeks=6, tolerance_days=4
        )
        assert (
            deviation_issues
        ), "Expected deviation issues for 60-day gap with 6-week expectation"
        passed.append("imaging_gap_deviation")
except AssertionError as e:
    failed.append(str(e))
except Exception as e:
    failed.append(f"Unhandled: {e}")

if failed:
    print("TESTS FAILED")
    for f in failed:
        print(" -", f)
    sys.exit(1)
else:
    print("ALL TESTS PASSED:", ", ".join(passed))
