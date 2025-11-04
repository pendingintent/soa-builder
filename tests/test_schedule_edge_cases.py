from datetime import datetime
from soa_builder.schedule import RuleStub, VisitStub, expand_schedule_rules


def test_expand_no_rules():
    visits = {
        1: VisitStub(
            visit_id=1, visit_name="Baseline", raw_header="Baseline", sequence_index=1
        )
    }
    instances = expand_schedule_rules([], visits, start_date=datetime(2025, 1, 1))
    assert instances == []


def test_expand_malformed_pattern():
    visits = {
        1: VisitStub(
            visit_id=1, visit_name="Baseline", raw_header="Baseline", sequence_index=1
        )
    }
    bad_rule = RuleStub(
        rule_id=999,
        pattern="qXYZw",
        description="Malformed",
        source_type="cell",
        activity_id=None,
        visit_id=None,
        raw_text="qXYZw",
    )
    instances = expand_schedule_rules(
        [bad_rule], visits, start_date=datetime(2025, 1, 1)
    )
    assert instances == []


def test_expand_zero_interval():
    visits = {
        1: VisitStub(
            visit_id=1, visit_name="Baseline", raw_header="Baseline", sequence_index=1
        )
    }
    zero_rule = RuleStub(
        rule_id=1000,
        pattern="q0w",
        description="Zero interval",
        source_type="cell",
        activity_id=None,
        visit_id=None,
        raw_text="q0w",
    )
    instances = expand_schedule_rules(
        [zero_rule], visits, start_date=datetime(2025, 1, 1)
    )
    assert instances == []
