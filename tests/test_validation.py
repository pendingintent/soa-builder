import os, tempfile, csv
from soa_builder import normalize_soa, validate_imaging_schedule
from soa_builder.validation import extract_imaging_events

CSV_PATH = os.path.join(
    os.path.dirname(__file__), "..", "files", "SoA_breast_cancer.csv"
)


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_imaging_validation():
    with tempfile.TemporaryDirectory() as tmp:
        normalize_soa(CSV_PATH, tmp, None)
        visits = _read_csv(os.path.join(tmp, "visits.csv"))
        activities = _read_csv(os.path.join(tmp, "activities.csv"))
        visit_activities = _read_csv(os.path.join(tmp, "visit_activities.csv"))
        events = extract_imaging_events(visits, activities, visit_activities)
        issues = validate_imaging_schedule(events)
        # issues may exist; just ensure logic ran
        assert events is not None
        assert issues is not None
