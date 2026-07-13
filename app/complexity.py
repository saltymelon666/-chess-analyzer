from __future__ import annotations

from dataclasses import dataclass

from .models import ComplexityFactors, EngineResult, MoveFacts


COMPLEXITY_THRESHOLDS = {
    "unique_candidate_gap_cp": 100,
    "large_evaluation_swing_cp": 90,
    "very_large_evaluation_swing_cp": 200,
    "long_pv_plies": 8,
    "forcing_line_plies": 3,
    "many_legal_moves": 30,
    "many_engaged_pieces": 8,
    "complex_score": 5,
}

EXPLANATION_PROFILES = {
    "simple": {"min_chars": 40, "max_chars": 80, "max_tokens": 180},
    "normal": {"min_chars": 80, "max_chars": 150, "max_tokens": 300},
    "complex": {"min_chars": 150, "max_chars": 280, "max_tokens": 520},
}


@dataclass(frozen=True)
class ComplexityResult:
    level: str
    factors: ComplexityFactors


def classify_complexity(
    *,
    before_result: EngineResult,
    side: str,
    played: MoveFacts,
    pv_facts: list[MoveFacts],
    legal_move_count: int,
    evaluation_swing_cp: int | None,
    mate_involved: bool,
    only_legal_move: bool,
    engaged_piece_count: int,
) -> ComplexityResult:
    candidate_gap = candidate_gap_cp(before_result, side)
    only_reasonable = only_legal_move or (
        candidate_gap is not None
        and candidate_gap >= COMPLEXITY_THRESHOLDS["unique_candidate_gap_cp"]
    )
    forcing_plies = _forcing_prefix_length(pv_facts)
    pv_length = len(pv_facts)
    tactical_event = (
        played.capture
        or played.check
        or played.checkmate
        or played.promotion is not None
        or forcing_plies > 0
    )

    score = 0
    if mate_involved:
        score += 4
    if played.checkmate or played.promotion is not None:
        score += 3
    elif played.capture or played.check:
        score += 1
    if only_reasonable and not only_legal_move:
        score += 2
    if pv_length >= COMPLEXITY_THRESHOLDS["long_pv_plies"]:
        score += 1
    if evaluation_swing_cp is not None:
        if evaluation_swing_cp > COMPLEXITY_THRESHOLDS["very_large_evaluation_swing_cp"]:
            score += 2
        elif evaluation_swing_cp > COMPLEXITY_THRESHOLDS["large_evaluation_swing_cp"]:
            score += 1
    if forcing_plies >= COMPLEXITY_THRESHOLDS["forcing_line_plies"]:
        score += 2
    elif forcing_plies:
        score += 1
    if engaged_piece_count >= COMPLEXITY_THRESHOLDS["many_engaged_pieces"]:
        score += 2
    elif engaged_piece_count >= 4:
        score += 1
    if legal_move_count >= COMPLEXITY_THRESHOLDS["many_legal_moves"]:
        score += 1

    simple = (
        not mate_involved
        and not tactical_event
        and (evaluation_swing_cp or 0) <= 40
        and engaged_piece_count < 4
        and score <= 1
    )
    if score >= COMPLEXITY_THRESHOLDS["complex_score"]:
        level = "complex"
    elif simple:
        level = "simple"
    else:
        level = "normal"

    return ComplexityResult(
        level=level,
        factors=ComplexityFactors(
            legal_move_count=legal_move_count,
            candidate_gap_cp=candidate_gap,
            only_reasonable_move=only_reasonable,
            pv_length=pv_length,
            evaluation_swing_cp=evaluation_swing_cp,
            forcing_line_plies=forcing_plies,
            engaged_piece_count=engaged_piece_count,
        ),
    )


def candidate_gap_cp(result: EngineResult, side: str) -> int | None:
    if len(result.top_moves) < 2:
        return None
    best = _candidate_value(result.top_moves[0].centipawn, result.top_moves[0].mate_in, side)
    second = _candidate_value(result.top_moves[1].centipawn, result.top_moves[1].mate_in, side)
    if best is None or second is None:
        return None
    return max(0, best - second)


def _candidate_value(centipawn: int | None, mate_in: int | None, side: str) -> int | None:
    direction = 1 if side == "white" else -1
    if mate_in is not None:
        mover_mate = mate_in * direction
        return (100_000 - abs(mover_mate) * 100) if mover_mate > 0 else (-100_000 + abs(mover_mate) * 100)
    return centipawn * direction if centipawn is not None else None


def _forcing_prefix_length(pv_facts: list[MoveFacts]) -> int:
    count = 0
    for fact in pv_facts:
        if not (fact.capture or fact.check or fact.checkmate or fact.promotion is not None):
            break
        count += 1
    return count
