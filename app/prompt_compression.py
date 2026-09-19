from __future__ import annotations

from collections import Counter
from typing import Any

from .accounts import CoachMemory
from .models import MoveReview


LONG_GAME_THRESHOLD_PLIES = 60
ERROR_QUALITIES = {"inaccuracy", "mistake", "blunder"}


def _important(review: MoveReview) -> bool:
    return bool(
        review.quality_key in ERROR_QUALITIES
        or review.verified_tactics
        or review.mate_involved
        or review.complexity_factors.direct_piece_loss
        or review.complexity_factors.multiple_threats
        or review.complexity_factors.only_reasonable_move
    )


def compress_game_context(moves: list[MoveReview], *, threshold: int = LONG_GAME_THRESHOLD_PLIES) -> dict[str, Any]:
    if not moves:
        return {"mode": "empty", "totalPlies": 0, "segments": []}
    if len(moves) <= threshold:
        selected = set(range(len(moves)))
        mode = "full"
    else:
        selected = set(range(min(20, len(moves))))
        for index, review in enumerate(moves):
            if _important(review):
                selected.update(range(max(0, index - 1), min(len(moves), index + 2)))
        mode = "compressed"
    segments: list[dict[str, Any]] = []
    cursor = 0
    ordered = sorted(selected)
    for index in ordered:
        if index > cursor:
            start = moves[cursor]
            end = moves[index - 1]
            segments.append({
                "type": "summary",
                "fromPly": start.index,
                "toPly": end.index,
                "text": f"第{start.move_number}—{end.move_number}回合未出现程序标记的重要转折，省略普通着。",
            })
        review = moves[index]
        important = _important(review)
        move_segment: dict[str, Any] = {
            "type": "move",
            "ply": review.index,
            "notation": review.notation,
            "quality": review.quality_key,
            "evaluationCp": review.after.centipawn,
            "important": important,
        }
        if important:
            move_segment.update({
                "bestMove": review.best_move_san,
                "verifiedFacts": review.verified_facts[:3],
                "pv": review.principal_variation[:6],
            })
        segments.append(move_segment)
        cursor = index + 1
    if cursor < len(moves):
        start = moves[cursor]
        end = moves[-1]
        segments.append({
            "type": "summary",
            "fromPly": start.index,
            "toPly": end.index,
            "text": f"第{start.move_number}—{end.move_number}回合未出现程序标记的重要转折，省略普通着。",
        })
    return {
        "mode": mode,
        "thresholdPlies": threshold,
        "totalPlies": len(moves),
        "openingPliesKept": min(20, len(moves)),
        "segments": segments,
        "policy": "程序标记、Stockfish PV和已验证事实优先；摘要不得替代当前局面事实。",
    }


def build_coach_memory(moves: list[MoveReview]) -> CoachMemory:
    errors = [move for move in moves if move.quality_key in ERROR_QUALITIES]
    error_types: list[str] = []
    for move in errors:
        if move.complexity_factors.direct_piece_loss or move.verified_tactics:
            error_types.append("战术检查")
        elif move.complexity_factors.multiple_threats:
            error_types.append("应对直接威胁")
        else:
            error_types.append("局面选择")
    counts = Counter(error_types)
    main_type = counts.most_common(1)[0][0] if counts else "稳定完成正常着"
    key_positions = [move.notation for move in errors[:5]]
    if errors:
        key_lesson = f"本盘最需要复盘的是{main_type}，共有{len(errors)}步被标记为不准确或失误。"
        practical_focus = f"下盘在落子前先检查：将军、吃子、对手的直接威胁，以及首选路线的第一项作用。"
        advice = f"用本盘的{'、'.join(key_positions[:3])}做短题，先口述威胁再计算候选着。"
    else:
        key_lesson = "本盘没有出现程序标记的明显失误，重点是保持候选着检查顺序。"
        practical_focus = "继续在每个关键局面先核对对手的将军、吃子和直接威胁。"
        advice = "选取评价变化最大的局面复盘，确认好棋解决了哪一个具体问题。"
    return CoachMemory(
        key_lesson=key_lesson,
        practical_focus=practical_focus,
        error_types=sorted(counts, key=lambda item: (-counts[item], item))[:3],
        key_positions=key_positions,
        training_advice=advice,
    )


def compress_coach_memories(
    memories: list[CoachMemory],
    *,
    current_game: CoachMemory | None = None,
) -> dict[str, Any]:
    recent = memories[:5]
    counts = Counter(error for memory in recent for error in memory.error_types)
    current_errors = set(current_game.error_types if current_game else [])
    repeated = sorted(name for name in current_errors if counts[name] >= 1)
    improved = sorted(name for name in counts if name not in current_errors)
    return {
        "games": [
            {
                "keyLesson": memory.key_lesson,
                "practicalFocus": memory.practical_focus,
                "errorTypes": memory.error_types,
                "keyPositions": memory.key_positions[:3],
                "trainingAdvice": memory.training_advice,
            }
            for memory in recent
        ],
        "repeatedIssues": repeated,
        "improvedIssues": improved,
        "currentGameErrorTypes": sorted(current_errors),
        "policy": "仅作教练连续性辅助；不得覆盖或修改当前Stockfish、Facts、Threat、Plan事实。",
    }
