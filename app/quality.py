from __future__ import annotations

from dataclasses import dataclass

from .models import EvaluationSnapshot


# All tunable move-quality thresholds live here. Values are centipawns.
QUALITY_THRESHOLDS = {
    "best_max": 15,
    "good_max": 40,
    "inaccuracy_max": 90,
    "mistake_max": 200,
    "clear_advantage": 150,
    "clear_disadvantage": -100,
    "opening_fullmove_max": 8,
}


@dataclass(frozen=True)
class QualityResult:
    key: str
    symbol: str
    label: str
    centipawn_loss: int | None


QUALITY_META = {
    "brilliant": ("!!", "精彩着"),
    "best": ("!", "最佳着"),
    "good": ("✓", "好棋"),
    "inaccuracy": ("?!", "不精确"),
    "mistake": ("?", "错误"),
    "blunder": ("??", "败着"),
    "routine": ("—", "常规着"),
}


def mover_value(snapshot: EvaluationSnapshot, side: str) -> tuple[int | None, int | None]:
    """Return cp/mate from the player-to-move's perspective."""
    direction = 1 if side == "white" else -1
    cp = snapshot.centipawn * direction if snapshot.centipawn is not None else None
    mate = snapshot.mate_in * direction if snapshot.mate_in is not None else None
    return cp, mate


def centipawn_loss(
    before: EvaluationSnapshot,
    after: EvaluationSnapshot,
    side: str,
) -> int | None:
    before_cp, before_mate = mover_value(before, side)
    after_cp, after_mate = mover_value(after, side)
    if before_mate is not None or after_mate is not None:
        return None
    if before_cp is None or after_cp is None:
        return None
    return max(0, before_cp - after_cp)


def classify_move(
    *,
    before: EvaluationSnapshot,
    after: EvaluationSnapshot,
    side: str,
    played_uci: str,
    best_uci: str | None,
    only_legal_move: bool = False,
    opening_routine: bool = False,
) -> QualityResult:
    """Classify a move, handling forced mates before ordinary cp thresholds."""
    before_cp, before_mate = mover_value(before, side)
    after_cp, after_mate = mover_value(after, side)
    is_engine_choice = bool(best_uci and played_uci == best_uci)
    loss = centipawn_loss(before, after, side)

    # A move that loses a forced mate or allows the opponent to force mate is a blunder.
    if before_mate is not None and before_mate > 0 and (after_mate is None or after_mate <= 0):
        return _result("blunder", loss)
    if after_mate is not None and after_mate < 0 and (before_mate is None or before_mate >= 0):
        return _result("blunder", loss)

    # Forced lines and the only legal move should not receive inflated praise.
    if only_legal_move:
        return _result("routine", 0 if loss is None else loss)

    # Mate scores are not converted into synthetic centipawns.
    if before_mate is not None or after_mate is not None:
        if is_engine_choice:
            return _result("best", 0)
        if after_mate is not None and after_mate < 0:
            return _result("blunder", None)
        return _result("mistake", None)

    if loss is None:
        return _result("best" if is_engine_choice else "routine", loss)

    # A clear advantage turning into a disadvantage is always a blunder.
    if (
        before_cp is not None
        and after_cp is not None
        and before_cp >= QUALITY_THRESHOLDS["clear_advantage"]
        and after_cp <= QUALITY_THRESHOLDS["clear_disadvantage"]
    ):
        return _result("blunder", loss)

    if loss > QUALITY_THRESHOLDS["mistake_max"]:
        return _result("blunder", loss)
    if opening_routine and loss <= QUALITY_THRESHOLDS["best_max"]:
        return _result("routine", loss)
    if is_engine_choice or loss <= QUALITY_THRESHOLDS["best_max"]:
        return _result("best", loss)
    if loss <= QUALITY_THRESHOLDS["good_max"]:
        return _result("good", loss)
    if loss <= QUALITY_THRESHOLDS["inaccuracy_max"]:
        return _result("inaccuracy", loss)
    if loss <= QUALITY_THRESHOLDS["mistake_max"]:
        return _result("mistake", loss)
    return _result("blunder", loss)


def _result(key: str, loss: int | None) -> QualityResult:
    symbol, label = QUALITY_META[key]
    return QualityResult(key=key, symbol=symbol, label=label, centipawn_loss=loss)
