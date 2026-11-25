"""
Run simulator scenarios from the command line.

    uv run python -m sim.run --scenario cold_night [--csv out.csv]
    uv run python -m sim.run --all
    uv run python -m sim.run --list
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .loop import simulate
from .metrics import Metrics, Report, assess
from .scenarios import SCENARIOS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .loop import SimResult

HEADER = (
    f"{'scenario':<20} {'result':<6} {'starts':>6} {'max/h':>5} {'run min':>7} "
    f"{'short':>5} {'med err K':>9} {'over min':>8} {'levels':>6} {'mean %':>6}  guard stops"
)


def summary_line(report: Report) -> str:
    """One table row per scenario."""
    m: Metrics = report.metrics
    error = "-" if m.worst_median_error_k is None else f"{m.worst_median_error_k:.2f}"
    mean = "-" if m.mean_pct_on is None else f"{m.mean_pct_on:.0f}"
    stops = ", ".join(f"{name} {count}" for name, count in sorted(m.guard_stops.items())) or "-"
    return (
        f"{report.name:<20} {'pass' if report.passed else 'FAIL':<6} {m.starts:>6} "
        f"{m.max_starts_per_hour:>5} {m.run_minutes:>7} {m.short_runs:>5} {error:>9} "
        f"{m.worst_overheat_min:>8.0f} {m.distinct_pct:>6} {mean:>6}  {stops}"
    )


def write_csv(result: SimResult, path: Path) -> None:
    """One row per minute: boiler, engine terms and every room."""
    names = [spec.params.name for spec in result.scenario.house.rooms]
    room_columns = ("temp_c", "wall_c", "target_c", "opening", "status")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "time",
                "outdoor_c",
                "heating",
                "power_pct",
                "reason",
                "load_m2k",
                "error_k",
                "base_pct",
                "p_pct",
                "integral_pct",
                *(f"{name}_{column}" for name in names for column in room_columns),
            ]
        )
        for sample in result.samples:
            rooms = []
            for room in sample.rooms:
                wall = "" if room.wall_c is None else f"{room.wall_c:.1f}"
                status = "" if room.status is None else str(room.status)
                rooms += [f"{room.temp_c:.3f}", wall, room.target_c, room.opening, status]
            writer.writerow(
                [
                    sample.time.isoformat(),
                    f"{sample.outdoor_c:.2f}",
                    int(sample.heating),
                    sample.power_pct,
                    sample.reasons[-1] if sample.reasons else "",
                    f"{sample.load_m2k:.2f}",
                    f"{sample.error_k:.3f}",
                    f"{sample.base_pct:.1f}",
                    f"{sample.p_pct:.1f}",
                    f"{sample.integral_pct:.2f}",
                    *rooms,
                ]
            )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the chosen scenarios; exit 1 when any check failed."""
    parser = argparse.ArgumentParser(prog="python -m sim.run", description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--scenario", action="append", choices=sorted(SCENARIOS))
    choice.add_argument("--all", action="store_true", help="every scenario")
    choice.add_argument("--list", action="store_true", help="list the scenarios")
    parser.add_argument("--csv", type=Path, help="per-minute record (one scenario only)")
    args = parser.parse_args(argv)

    if args.list:
        for name, factory in SCENARIOS.items():
            print(f"{name:<20} {factory().description}")
        return 0
    names: list[str] = list(SCENARIOS) if args.all else args.scenario
    if args.csv is not None and len(names) != 1:
        parser.error("--csv needs exactly one --scenario")

    print(HEADER)
    failed = False
    for name in names:
        result = simulate(SCENARIOS[name]())
        report = assess(result)
        print(summary_line(report))
        for failure in report.failures:
            print(f"    {failure}")
        failed = failed or not report.passed
        if args.csv is not None:
            write_csv(result, args.csv)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
