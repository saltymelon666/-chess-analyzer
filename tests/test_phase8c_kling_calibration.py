from __future__ import annotations

from scripts.calibrate_phase8c_kling_board_recovery import (
    calibration_profile,
    candidate_score,
    select_candidate,
)


def _read(placement: str, x0: int, confidence: float) -> dict:
    return {
        "variant": "raw",
        "corners": {"x0": x0, "y0": 10, "x1": 90, "y1": 90},
        "placement": placement,
        "minConfidence": confidence,
        "meanConfidence": confidence,
    }


def test_calibration_uses_exact_reviewed_candidate_geometry() -> None:
    rows = [{
        "file": "gold.jpeg", "width": 100, "height": 100,
        "bookReads": [_read("correct", 5, 0.8), _read("wrong", 15, 0.99)],
    }]

    profile = calibration_profile(rows, {"gold.jpeg": "correct"})

    assert profile == [0.05, 0.1, 0.9, 0.9]


def test_selector_balances_layout_and_model_confidence() -> None:
    row = {
        "file": "new.jpeg", "width": 100, "height": 100,
        "bookReads": [_read("correct", 5, 0.8), _read("shifted", 20, 0.99)],
    }
    profile = [0.05, 0.1, 0.9, 0.9]

    selected = select_candidate(row, profile)

    assert selected["placement"] == "correct"
    assert selected["selectionMargin"] > 0
    assert candidate_score(row["bookReads"][0], 100, 100, profile) > candidate_score(
        row["bookReads"][1], 100, 100, profile
    )
