"""Command-line interface for soa_builder using Click.

Commands:
  normalize  -> Run normalization on a wide SoA CSV
  expand     -> Expand repeating schedule rules into projected instances
  validate   -> Run imaging interval validation

Usage examples:
  python -m soa_builder.cli normalize --input files/SoA_breast_cancer.csv --out-dir normalized
  python -m soa_builder.cli expand --normalized-dir normalized --start-date 2025-01-01 --json-out normalized/schedule_instances.json
  python -m soa_builder.cli validate --normalized-dir normalized
"""

from __future__ import annotations
import csv, os, sys, json, logging
from datetime import datetime
from typing import List, Optional
import click

from .normalization import normalize_soa
from .schedule import RuleStub, VisitStub, expand_schedule_rules
from .validation import extract_imaging_events, validate_imaging_schedule

# --------------------- helpers ---------------------


def _read_csv(path: str) -> List[dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _load_rules(normalized_dir: str) -> List[RuleStub]:
    rows = _read_csv(os.path.join(normalized_dir, "schedule_rules.csv"))
    rules: List[RuleStub] = []
    for r in rows:
        rules.append(
            RuleStub(
                rule_id=int(r["rule_id"]),
                pattern=r["pattern"],
                description=r.get("description", ""),
                source_type=r.get("source_type", ""),
                activity_id=int(r["activity_id"]) if r.get("activity_id") else None,
                visit_id=int(r["visit_id"]) if r.get("visit_id") else None,
                raw_text=r.get("raw_text", ""),
            )
        )
    return rules


def _load_visits(normalized_dir: str) -> dict:
    rows = _read_csv(os.path.join(normalized_dir, "visits.csv"))
    visits = {}
    for r in rows:
        vid = int(r["visit_id"])
        visits[vid] = VisitStub(
            visit_id=vid,
            visit_name=r.get("visit_name", ""),
            raw_header=r.get("raw_header", ""),
            sequence_index=int(r.get("sequence_index", "0")),
        )
    return visits


# --------------------- CLI group ---------------------


@click.group()
@click.option("--verbose", is_flag=True, help="Enable verbose logging (DEBUG).")
@click.version_option("0.1.0")
def cli(verbose: bool):
    """soa_builder CLI."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")
    logging.debug("Verbose logging enabled." if verbose else "Logging level INFO.")


# --------------------- normalize ---------------------


@cli.command("normalize")
@click.option(
    "--input",
    "input_csv",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Wide SoA CSV path.",
)
@click.option(
    "--out-dir",
    required=True,
    type=click.Path(file_okay=False),
    help="Output directory for normalized tables.",
)
@click.option(
    "--sqlite",
    "sqlite_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="Optional SQLite database path.",
)
def cmd_normalize(input_csv: str, out_dir: str, sqlite_path: Optional[str]):
    """Normalize wide SoA CSV into relational tables."""
    try:
        summary = normalize_soa(input_csv, out_dir, sqlite_path)
        click.echo(f"Normalization complete: {summary}")
    except Exception as e:
        logging.exception("Normalization failed")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# --------------------- expand ---------------------


@cli.command("expand")
@click.option(
    "--normalized-dir",
    required=True,
    type=click.Path(file_okay=False),
    help="Directory containing normalized CSV outputs.",
)
@click.option("--start-date", required=True, help="Anchor study start date YYYY-MM-DD.")
@click.option(
    "--cycle-length-days",
    default=21,
    show_default=True,
    type=int,
    help="Default cycle length days.",
)
@click.option(
    "--num-cycles",
    default=8,
    show_default=True,
    type=int,
    help="Number of cycles for horizon calc.",
)
@click.option(
    "--followup-weeks",
    default=104,
    show_default=True,
    type=int,
    help="Follow-up weeks horizon.",
)
@click.option(
    "--horizon-days",
    default=None,
    type=int,
    help="Explicit horizon in days (overrides cycles/followup).",
)
@click.option(
    "--cycle-lengths",
    default=None,
    help="Comma list of per-cycle lengths (e.g. 21,21,28).",
)
@click.option(
    "--max-occurrences", default=None, type=int, help="Cap occurrences per rule."
)
@click.option(
    "--filter-pattern",
    multiple=True,
    help="Limit to specific pattern tokens (repeatable).",
)
@click.option(
    "--json-out",
    default=None,
    type=click.Path(dir_okay=False),
    help="Optional JSON output for instances.",
)
@click.option(
    "--csv-out",
    default=None,
    type=click.Path(dir_okay=False),
    help="Optional CSV output path.",
)
def cmd_expand(
    normalized_dir: str,
    start_date: str,
    cycle_length_days: int,
    num_cycles: int,
    followup_weeks: int,
    horizon_days: Optional[int],
    cycle_lengths: Optional[str],
    max_occurrences: Optional[int],
    filter_pattern: List[str],
    json_out: Optional[str],
    csv_out: Optional[str],
):
    """Expand repeating schedule rules into projected instances."""
    try:
        anchor = datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        raise click.BadParameter("start-date must be YYYY-MM-DD")
    cycle_lengths_list = None
    if cycle_lengths:
        cycle_lengths_list = [
            int(x.strip()) for x in cycle_lengths.split(",") if x.strip()
        ]
    try:
        rules = _load_rules(normalized_dir)
        visits = _load_visits(normalized_dir)
    except FileNotFoundError as e:
        click.echo(f"Missing normalized file: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logging.exception("Failed to load normalized artifacts")
        click.echo(f"Error loading artifacts: {e}", err=True)
        sys.exit(1)
    if not rules:
        click.echo("No schedule rules found; nothing to expand.")
        instances = []
    else:
        try:
            instances = expand_schedule_rules(
                rules=rules,
                visits=visits,
                start_date=anchor,
                cycle_length_days=cycle_length_days,
                num_cycles=num_cycles,
                followup_weeks=followup_weeks,
                horizon_days=horizon_days,
                cycle_lengths=cycle_lengths_list,
                max_occurrences=max_occurrences,
                filter_patterns=list(filter_pattern) if filter_pattern else None,
            )
        except Exception as e:
            logging.exception("Expansion failed")
            click.echo(f"Error during expansion: {e}", err=True)
            sys.exit(1)
    click.echo(f"Expanded {len(instances)} instances from {len(rules)} rules.")
    # default output path
    if not csv_out:
        csv_out = os.path.join(normalized_dir, "schedule_instances.csv")
    # write CSV
    import csv as _csv

    if instances:
        fieldnames = [
            "instance_id",
            "rule_id",
            "pattern",
            "occurrence_index",
            "anchor_visit_id",
            "anchor_activity_id",
            "nominal_day",
            "projected_date",
            "source_type",
            "description",
        ]
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for inst in instances:
                w.writerow({k: getattr(inst, k) for k in fieldnames})
    else:
        open(csv_out, "w").close()
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump([inst.__dict__ for inst in instances], f, indent=2)
    click.echo(
        f"Instances written: CSV={csv_out}{' JSON='+json_out if json_out else ''}"
    )


# --------------------- validate ---------------------


@cli.command("validate")
@click.option(
    "--normalized-dir",
    required=True,
    type=click.Path(file_okay=False),
    help="Directory with normalized tables.",
)
@click.option(
    "--expected-interval-weeks",
    default=6,
    show_default=True,
    type=int,
    help="Expected imaging interval in weeks.",
)
@click.option(
    "--tolerance-days",
    default=4,
    show_default=True,
    type=int,
    help="Tolerance days for interval deviation.",
)
def cmd_validate(
    normalized_dir: str, expected_interval_weeks: int, tolerance_days: int
):
    """Validate imaging schedule intervals."""
    try:
        visits = _read_csv(os.path.join(normalized_dir, "visits.csv"))
        activities = _read_csv(os.path.join(normalized_dir, "activities.csv"))
        visit_activities = _read_csv(
            os.path.join(normalized_dir, "visit_activities.csv")
        )
    except FileNotFoundError as e:
        click.echo(f"Missing file: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logging.exception("Failed to load tables")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    try:
        events = extract_imaging_events(visits, activities, visit_activities)
        issues = validate_imaging_schedule(
            events, expected_interval_weeks, tolerance_days
        )
    except Exception as e:
        logging.exception("Validation processing failed")
        click.echo(f"Validation error: {e}", err=True)
        sys.exit(1)
    if issues:
        click.echo("Validation issues detected:")
        for i in issues:
            click.echo(f" - {i}")
        sys.exit(1)
    click.echo("Imaging schedule validation passed.")


# --------------------- entry ---------------------


def main():  # pragma: no cover
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
