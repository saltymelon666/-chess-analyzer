from unittest.mock import AsyncMock

import chess
import pytest

from app.ai_explainer import (
    DeepSeekExplainer,
    conservative_move_details,
    conservative_move_explanation,
    parse_move_explanation_details,
    validate_move_explanation,
)
from app.models import ComplexityFactors, EvaluationSnapshot, MoveFacts, MoveReview


def review(complexity: str = "simple") -> MoveReview:
    after = chess.Board()
    after.push_uci("e2e4")
    played = MoveFacts(
        san="e4",
        uci="e2e4",
        from_square="e2",
        to_square="e4",
        piece="white_pawn",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )
    best = MoveFacts(
        san="d4",
        uci="d2d4",
        from_square="d2",
        to_square="d4",
        piece="white_pawn",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )
    return MoveReview(
        index=1,
        move_number=1,
        notation="1.e4",
        side="white",
        san="e4",
        uci="e2e4",
        from_square="e2",
        to_square="e4",
        before_fen=chess.STARTING_FEN,
        after_fen=after.fen(),
        before=EvaluationSnapshot(evaluation="+0.20", centipawn=20),
        after=EvaluationSnapshot(evaluation="-0.20", centipawn=-20),
        played_move=played,
        best_move=best,
        centipawn_loss=40,
        best_move_uci="d2d4",
        best_move_san="d4",
        best_pv=["d4", "d5", "Nf3"],
        quality_key="good",
        quality_symbol="✓",
        quality_label="好棋",
        mate_involved=False,
        only_legal_move=False,
        principal_variation=["d4", "d5", "Nf3"],
        complexity=complexity,
        complexity_factors=ComplexityFactors(
            legal_move_count=20,
            candidate_gap_cp=20,
            only_reasonable_move=False,
            pv_length=3 if complexity != "complex" else 9,
            evaluation_swing_cp=40 if complexity != "complex" else 240,
            forcing_line_plies=0 if complexity != "complex" else 4,
            engaged_piece_count=0 if complexity != "complex" else 10,
        ),
        verified_facts=["白方实战走了 e4：白兵从 e2 到 e4。", "Stockfish 的合法第一选择是 d4：白兵从 d2 到 d4。"],
        allowed_squares=["e2", "e4", "d2", "d4", "d7", "d5", "g1", "f3"],
        allowed_moves=["e4", "e2e4", "d4", "d2d4", "d5", "d7d5", "Nf3", "g1f3"],
        pieces_before={"e2": "white_pawn", "d2": "white_pawn", "g1": "white_knight"},
    )


def test_guard_rejects_hallucinated_square_move_and_capture() -> None:
    errors = validate_move_explanation(
        "白方可以走Qh5并在h9吃掉黑车，这是很好的计划。记住：大胆进攻。",
        review(),
    )
    assert any("棋盘范围外" in error for error in errors)
    assert any("SAN" in error for error in errors)
    assert any("吃子" in error for error in errors)


def test_guard_rejects_wrong_piece_color_and_accepts_negated_event() -> None:
    move = review()
    wrong_color = validate_move_explanation(
        "这一步是黑兵从e2到e4，评价变化不大。记住：先核对棋子颜色。" + "说明" * 8,
        move,
    )
    assert any("颜色" in error for error in wrong_color)

    correct_negative = conservative_move_explanation(move).replace("被评为好棋", "不是吃子，被评为好棋")
    assert not any("吃子" in error for error in validate_move_explanation(correct_negative, move))
    mate_notation = conservative_move_explanation(move).replace("+0.20", "白方M1")
    assert not any("棋盘范围外" in error for error in validate_move_explanation(mate_notation, move))


@pytest.mark.asyncio
async def test_first_validation_failure_retries_once() -> None:
    move = review()
    valid = conservative_move_explanation(move)
    valid_json = conservative_move_details(move).model_dump_json(by_alias=True)
    explainer = DeepSeekExplainer(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    explainer._chat = AsyncMock(side_effect=["白方应该走Qh5。记住：进攻。", valid_json])
    result = await explainer.explain_move(move)
    assert result.explanation == valid
    assert result.details.complexity == "simple"
    assert explainer._chat.await_count == 2
    first_prompt = explainer._chat.await_args_list[0].kwargs["prompt"]
    assert move.before_fen not in first_prompt
    assert '"version": "1.0"' in first_prompt


@pytest.mark.asyncio
async def test_second_validation_failure_uses_conservative_template() -> None:
    move = review()
    explainer = DeepSeekExplainer(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    explainer._chat = AsyncMock(return_value="白方应该走Qh5。记住：进攻。")
    result = await explainer.explain_move(move)
    assert "Qh5" not in result.explanation
    assert validate_move_explanation(result.explanation, move) == []
    assert explainer._chat.await_count == 2


def test_simple_and_complex_fallback_lengths_are_distinct() -> None:
    simple = conservative_move_explanation(review("simple"))
    complex_text = conservative_move_explanation(review("complex"))
    simple_length = len("".join(simple.split()))
    complex_length = len("".join(complex_text.split()))
    assert 50 <= simple_length <= 100
    assert 250 <= complex_length <= 500
    assert complex_length - simple_length >= 150
    assert validate_move_explanation(simple, review("simple")) == []
    assert validate_move_explanation(complex_text, review("complex")) == []


def test_complex_json_requires_all_sections_and_two_to_four_steps() -> None:
    move = review("complex")
    details = conservative_move_details(move)
    parsed, errors = parse_move_explanation_details(details.model_dump_json(by_alias=True), move)
    assert errors == []
    assert parsed is not None
    assert 2 <= len(parsed.variation_explanation) <= 4

    broken = details.model_copy(update={"opponent_threat": "", "variation_explanation": ["只有一步"]})
    _, errors = parse_move_explanation_details(broken.model_dump_json(by_alias=True), move)
    assert any("opponent_threat" in error for error in errors)
    assert any("2—4" in error for error in errors)


@pytest.mark.asyncio
async def test_complex_request_uses_large_json_token_budget() -> None:
    move = review("complex")
    valid_json = conservative_move_details(move).model_dump_json(by_alias=True)
    explainer = DeepSeekExplainer(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    explainer._chat = AsyncMock(return_value=valid_json)
    result = await explainer.explain_move(move)
    assert result.details.complexity == "complex"
    assert explainer._chat.await_args.kwargs["max_tokens"] == 1100
    assert explainer._chat.await_args.kwargs["json_mode"] is True
