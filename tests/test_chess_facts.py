import json
from unittest.mock import AsyncMock

import chess
import httpx
import pytest

from app.ai_explainer import DeepSeekExplainer
from app.chess_facts import (
    EventExplanation,
    FactExplanationDraft,
    RouteExplanation,
    build_engine_fact_package,
    validate_fact_explanation,
    verify_route,
)
from app.models import EngineResult, MoveResult, VariationMove


def engine_result() -> EngineResult:
    return EngineResult(
        evaluation="+0.25",
        centipawn=25,
        mate_in=None,
        depth=16,
        nodes=1234,
        time_ms=20,
        top_moves=[
            MoveResult(
                move="e2e4",
                san="e4",
                centipawn=25,
                mate_in=None,
                pv=["e5", "Nf3"],
                depth=16,
                rank=1,
            )
        ],
    )


def test_invalid_route_id_and_model_generated_move_are_rejected() -> None:
    package = build_engine_fact_package(chess.STARTING_FEN, engine_result())
    draft = FactExplanationDraft(
        summary="建议走Qh5，随后继续进攻。",
        route_explanations=[
            RouteExplanation(route_id="pv_missing", explanation="这条路线更主动。"),
        ],
        event_explanations=[
            EventExplanation(event_id="event_missing", explanation="这里出现了战术事件。"),
        ],
    )

    errors = validate_fact_explanation(draft, package)

    assert any("route_id" in error for error in errors)
    assert any("event_id" in error for error in errors)
    assert any("不得返回具体棋步" in error for error in errors)


def test_illegal_pv_marks_complete_route_unverified() -> None:
    first = VariationMove(
        ply=1,
        move_number=1,
        side="white",
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
    illegal = VariationMove(
        ply=2,
        move_number=1,
        side="black",
        san="Ke3",
        uci="e1e3",
        from_square="e1",
        to_square="e3",
        piece="white_king",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )

    route = verify_route(
        route_id="pv_1",
        start_fen=chess.STARTING_FEN,
        moves=[first, illegal],
        evaluation=20,
        mate=None,
    )

    assert route.verified is False
    assert route.error == "illegal_move_at_ply_2"
    assert route.moves_san == []
    assert route.moves_uci == []


def test_invalid_engine_pv_is_not_exposed_as_verified_prefix() -> None:
    damaged = engine_result()
    damaged.top_moves[0].pv = ["Ke3"]

    package = build_engine_fact_package(chess.STARTING_FEN, damaged)

    assert package.candidate_routes[0].verified is False
    assert package.candidate_routes[0].moves_san == []
    assert package.prompt_payload()["candidate_routes"] == []


@pytest.mark.asyncio
async def test_review_deepseek_failure_uses_template_fallback() -> None:
    explainer = DeepSeekExplainer(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    explainer._chat = AsyncMock(side_effect=httpx.ConnectError("offline"))

    explanation = await explainer.explain(chess.STARTING_FEN, engine_result())

    assert "Stockfish评价" in explanation
    assert "已经由棋规逐步验证" in explanation


@pytest.mark.asyncio
async def test_review_deepseek_prompt_omits_fen_and_uses_fact_package() -> None:
    explainer = DeepSeekExplainer(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    response = {
        "information_insufficient": False,
        "summary": "局面评价来自统一事实包。",
        "actual_move_explanation": "",
        "best_move_explanation": "第一候选保持了局面的协调。",
        "route_explanations": [
            {"route_id": "pv_1", "explanation": "该路线保持了稳定的局面结构。"}
        ],
        "event_explanations": [],
    }
    explainer._chat = AsyncMock(return_value=json.dumps(response, ensure_ascii=False))

    explanation = await explainer.explain(chess.STARTING_FEN, engine_result())

    prompt = explainer._chat.await_args.kwargs["prompt"]
    assert chess.STARTING_FEN not in prompt
    assert '"version":"1.0"' in prompt
    assert '"perspective":"white"' in prompt
    assert '"route_id":"pv_1"' in prompt
    assert "第一候选保持了局面的协调" in explanation


def test_evaluation_is_explicitly_white_perspective() -> None:
    package = build_engine_fact_package(chess.STARTING_FEN, engine_result())

    assert package.evaluation.perspective == "white"
    assert package.evaluation.evaluation_cp == 25
    assert package.evaluation.evaluation_pawns == 0.25
