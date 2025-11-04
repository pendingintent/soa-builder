import os, tempfile
from soa_builder import normalize_soa

CSV_PATH = os.path.join(
    os.path.dirname(__file__), "..", "files", "SoA_breast_cancer.csv"
)


def test_normalization_basic():
    assert os.path.exists(CSV_PATH), "Input CSV missing."
    with tempfile.TemporaryDirectory() as tmp:
        summary = normalize_soa(CSV_PATH, tmp, None)
        assert summary["visits"] > 0
        assert summary["activities"] > 0
        assert summary["mappings"] > 0
        # ensure files written
        for fname in [
            "visits.csv",
            "activities.csv",
            "visit_activities.csv",
            "activity_categories.csv",
            "schedule_rules.csv",
        ]:
            assert os.path.exists(os.path.join(tmp, fname))
