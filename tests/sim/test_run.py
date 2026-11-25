from __future__ import annotations

import csv
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from sim.run import main
from sim.scenarios import SCENARIOS, clear_lockout

if TYPE_CHECKING:
    from pathlib import Path


def test_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list"]) == 0
    assert "cold_night" in capsys.readouterr().out


def test_one_scenario_with_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "run.csv"
    assert main(["--scenario", "wall_sensor_dead", "--csv", str(out)]) == 0
    assert "wall_sensor_dead" in capsys.readouterr().out
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 16 * 60
    assert "wall_invalid" in {row["bedroom_status"] for row in rows}
    assert any(row["bedroom_wall_c"] == "" for row in rows)


def test_csv_needs_exactly_one_scenario(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--all", "--csv", str(tmp_path / "x.csv")])


def test_failures_set_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failing = replace(clear_lockout(), checks=(lambda _result: ["boom"],))
    monkeypatch.setitem(SCENARIOS, "clear_lockout", lambda: failing)
    assert main(["--scenario", "clear_lockout"]) == 1
    assert "boom" in capsys.readouterr().out
