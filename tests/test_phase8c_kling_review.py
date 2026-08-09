from __future__ import annotations

from scripts.build_phase8c_kling_review_page import build_page, strict_cases
from scripts.build_phase8c_kling_round2_review import (
    rotate_180,
    round2_cases,
    source_orientation_placement,
)


def test_strict_cases_require_two_kings_and_confidence() -> None:
    payload = {"results": [
        {"file": "good.jpeg", "bookRead": {
            "placement": "3k4/8/8/8/8/8/8/3K4",
            "minConfidence": 0.8, "meanConfidence": 0.95,
        }},
        {"file": "low.jpeg", "bookRead": {
            "placement": "3k4/8/8/8/8/8/8/3K4",
            "minConfidence": 0.4, "meanConfidence": 0.95,
        }},
        {"file": "kingless.jpeg", "bookRead": {
            "placement": "8/8/8/8/8/8/8/8",
            "minConfidence": 0.9, "meanConfidence": 0.99,
        }},
    ]}

    cases = strict_cases(payload)

    assert [case["file"] for case in cases] == ["good.jpeg"]


def test_review_page_uses_visible_piece_glyphs_and_export() -> None:
    page = build_page([{
        "file": "good.jpeg",
        "bookRead": {
            "placement": "3k4/8/8/8/8/8/8/3K4",
            "minConfidence": 0.8,
            "meanConfidence": 0.95,
        },
    }])

    assert "♔" in page and "♚" in page
    assert "导出审核结果" in page
    assert "原书棋盘" in page and "程序恢复" in page


def test_round2_restores_source_orientation_and_excludes_reviewed() -> None:
    rotated = "6k1/7p/4K3/8/8/8/3P4/4B3"
    expected = "3B4/4P3/8/8/8/3K4/p7/1k6"
    assert rotate_180(rotated) == expected
    assert source_orientation_placement({
        "placement": rotated,
        "orientation": "black",
    }) == expected

    payload = {"positions": [
        {
            "file": "reviewed.jpeg", "placement": "3k4/8/8/8/8/8/8/3K4",
            "minConfidence": 0.5, "meanConfidence": 0.95,
            "selectionMargin": 0.03, "validTurns": ["w", "b"],
        },
        {
            "file": "new.jpeg", "placement": expected,
            "minConfidence": 0.5, "meanConfidence": 0.95,
            "selectionMargin": 0.03, "validTurns": ["w"],
        },
    ]}

    cases = round2_cases(payload, {"reviewed.jpeg"})

    assert [item["file"] for item in cases] == ["new.jpeg"]
    assert cases[0]["bookRead"]["placement"] == expected


def test_review_page_supports_round2_identity() -> None:
    page = build_page([{
        "file": "new.jpeg",
        "bookRead": {
            "placement": "3k4/8/8/8/8/8/8/3K4",
            "minConfidence": 0.8,
            "meanConfidence": 0.95,
        },
    }], id_prefix="KH2", schema_version="phase8c-kling-review-2.0",
       download_name="round2.json")

    assert "KH2-001" in page
    assert "phase8c-kling-review-2.0" in page
    assert "round2.json" in page
