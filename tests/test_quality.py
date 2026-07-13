import pytest

from app.models import EvaluationSnapshot
from app.quality import centipawn_loss, classify_move


def cp(value: int) -> EvaluationSnapshot:
    return EvaluationSnapshot(evaluation=f"{value / 100:+.2f}", centipawn=value)


def mate(value: int) -> EvaluationSnapshot:
    return EvaluationSnapshot(evaluation=f"M{value}", mate_in=value)


@pytest.mark.parametrize(
    ("loss", "expected"),
    [
        (0, "best"),
        (15, "best"),
        (16, "good"),
        (40, "good"),
        (41, "inaccuracy"),
        (90, "inaccuracy"),
        (91, "mistake"),
        (200, "mistake"),
        (201, "blunder"),
    ],
)
def test_threshold_boundaries(loss: int, expected: str) -> None:
    result = classify_move(
        before=cp(100),
        after=cp(100 - loss),
        side="white",
        played_uci="a2a3",
        best_uci="b2b3",
    )
    assert result.key == expected


def test_black_centipawn_loss_uses_black_perspective() -> None:
    assert centipawn_loss(cp(-100), cp(-84), "black") == 16
    result = classify_move(
        before=cp(-100),
        after=cp(-84),
        side="black",
        played_uci="a7a6",
        best_uci="b7b6",
    )
    assert result.key == "good"


def test_black_advantage_turning_into_disadvantage_is_blunder() -> None:
    result = classify_move(
        before=cp(-180),
        after=cp(120),
        side="black",
        played_uci="a7a5",
        best_uci="g8f6",
    )
    assert result.key == "blunder"
    assert result.centipawn_loss == 300


def test_negative_search_noise_is_clamped_to_zero() -> None:
    assert centipawn_loss(cp(20), cp(25), "white") == 0
    assert centipawn_loss(cp(-20), cp(-25), "black") == 0


def test_missing_a_forced_mate_is_blunder() -> None:
    result = classify_move(
        before=mate(3),
        after=cp(450),
        side="white",
        played_uci="a2a3",
        best_uci="h5f7",
    )
    assert result.key == "blunder"
    assert result.centipawn_loss is None


def test_allowing_opponent_forced_mate_is_blunder_for_black() -> None:
    result = classify_move(
        before=cp(-20),
        after=mate(3),
        side="black",
        played_uci="a7a6",
        best_uci="g8f6",
    )
    assert result.key == "blunder"


def test_only_legal_move_and_quiet_opening_are_routine() -> None:
    forced = classify_move(
        before=cp(0),
        after=cp(-10),
        side="white",
        played_uci="a1a2",
        best_uci="a1a2",
        only_legal_move=True,
    )
    opening = classify_move(
        before=cp(20),
        after=cp(18),
        side="white",
        played_uci="g1f3",
        best_uci="g1f3",
        opening_routine=True,
    )
    assert forced.key == "routine"
    assert opening.key == "routine"


def test_brilliant_is_not_assigned_without_reliable_evidence() -> None:
    result = classify_move(
        before=cp(0),
        after=cp(0),
        side="white",
        played_uci="e2e4",
        best_uci="e2e4",
    )
    assert result.key == "best"
