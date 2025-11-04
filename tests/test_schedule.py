import os, tempfile
from datetime import datetime
import csv

from soa_builder import normalize_soa, expand_schedule_rules
from soa_builder.schedule import RuleStub, VisitStub

CSV_PATH = os.path.join(
    os.path.dirname(__file__), "..", "files", "SoA_breast_cancer.csv"
)


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_schedule_expansion():
    with tempfile.TemporaryDirectory() as tmp:
        summary = normalize_soa(CSV_PATH, tmp, None)
        assert summary["rules"] >= 0
        # load rules
        rules_rows = _read_csv(os.path.join(tmp, "schedule_rules.csv"))
        visit_rows = _read_csv(os.path.join(tmp, "visits.csv"))
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
        # Expect at least some instances when rules exist
        if rules:
            assert instances, "Expected instances from rules."
