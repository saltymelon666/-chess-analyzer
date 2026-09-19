from __future__ import annotations

import pytest

from app.game_review import analyze_pgn
from app.accounts import CoachMemory
from app.prompt_compression import compress_coach_memories, compress_game_context
from tests.test_game_review import ScriptedStockfish
from tests.test_api import sample_move_review


@pytest.mark.asyncio
async def test_long_game_compression_keeps_opening_errors_and_context() -> None:
    result = await analyze_pgn(
        pgn="1. e4 e5",
        stockfish=ScriptedStockfish(),
        analysis_id="compression-test",
        depth=10,
        timeout_seconds=30,
        max_plies=20,
    )
    context = compress_game_context(result.moves, threshold=1)

    assert context["mode"] == "compressed"
    assert context["openingPliesKept"] == 2
    move_segments = [item for item in context["segments"] if item["type"] == "move"]
    assert [item["ply"] for item in move_segments] == [1, 2]
    assert move_segments[1]["quality"] == "inaccuracy"
    assert move_segments[1]["pv"]


def test_long_normal_stretches_are_replaced_by_compact_summaries() -> None:
    moves = []
    for index in range(1, 81):
        move = sample_move_review().model_copy(deep=True)
        move.index = index
        move.move_number = (index + 1) // 2
        move.notation = f"{move.move_number}.{'e4' if index % 2 else '..e5'}"
        move.quality_key = "mistake" if index == 50 else "routine"
        move.verified_tactics = []
        move.mate_involved = False
        move.complexity_factors.direct_piece_loss = False
        move.complexity_factors.multiple_threats = False
        move.complexity_factors.only_reasonable_move = False
        moves.append(move)

    context = compress_game_context(moves)
    kept = [item for item in context["segments"] if item["type"] == "move"]
    summaries = [item for item in context["segments"] if item["type"] == "summary"]
    assert len(kept) == 23  # first 20 plies plus mistake context 49-51
    assert {49, 50, 51}.issubset({item["ply"] for item in kept})
    assert summaries
    assert len(context["segments"]) < len(moves) / 2


def test_coach_memory_marks_only_program_confirmed_repeat_and_improvement() -> None:
    historical = [
        CoachMemory(
            key_lesson="历史",
            practical_focus="历史",
            error_types=["战术检查", "局面选择"],
            key_positions=[],
            training_advice="历史",
        )
    ]
    current = CoachMemory(
        key_lesson="当前",
        practical_focus="当前",
        error_types=["战术检查"],
        key_positions=[],
        training_advice="当前",
    )
    context = compress_coach_memories(historical, current_game=current)
    assert context["repeatedIssues"] == ["战术检查"]
    assert context["improvedIssues"] == ["局面选择"]
