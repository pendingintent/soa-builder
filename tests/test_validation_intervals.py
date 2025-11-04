from soa_builder.validation import ImagingEvent, validate_imaging_schedule


def test_imaging_gap_deviation():
    # Expect deviation: baseline day0 to day60 vs expected 6 weeks (42 days) tolerance 4
    events = [ImagingEvent("Baseline", 0), ImagingEvent("Late Imaging", 60)]
    issues = validate_imaging_schedule(
        events, expected_interval_weeks=6, tolerance_days=4
    )
    assert issues, "Should report deviation for 60-day gap when expected ~42±4"
