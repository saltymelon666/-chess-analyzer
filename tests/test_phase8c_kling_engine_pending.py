from __future__ import annotations

from scripts.enrich_phase8c_kling_engine_pending import engine_supports_claim


def test_engine_support_thresholds_do_not_turn_small_scores_into_wins() -> None:
    assert engine_supports_claim("white_win", 250, None)
    assert not engine_supports_claim("white_win", 80, None)
    assert engine_supports_claim("black_win", -250, None)
    assert not engine_supports_claim("black_win", -80, None)
    assert engine_supports_claim("draw", 20, None)
    assert not engine_supports_claim("draw", 90, None)


def test_mate_direction_is_white_relative() -> None:
    assert engine_supports_claim("white_win", None, 4)
    assert engine_supports_claim("black_win", None, -4)
    assert not engine_supports_claim("white_win", None, -4)
