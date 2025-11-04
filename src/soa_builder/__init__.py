"""soa_builder package

Modules:
"""

"""soa_builder package initialization.

Public API surface (initial):
 - normalize_soa: convert wide SoA CSV into normalized relational artifacts
 - expand_schedule_rules: project repeating rules into future calendar instances
 - validate_imaging_schedule: basic interval validation for imaging events

Additional utilities are internal; future versions may expose richer models.
"""
from .normalization import normalize_soa
from .schedule import expand_schedule_rules
from .validation import validate_imaging_schedule

__all__ = ["normalize_soa", "expand_schedule_rules", "validate_imaging_schedule"]
