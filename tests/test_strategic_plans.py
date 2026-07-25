import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import chess
import pytest

from app.ai_explainer import DeepSeekExplainer, parse_fact_explanation
from app.chess_facts import (
    ChessFactPackage,
    FactEvaluation,
    FactExplanationDraft,
    FactPosition,
    PlanExplanation,
    validate_fact_explanation,
    verify_route,
)
from app.strategic_plans import StrategicPlanAnalyzer


def package_with_routes(
    fen: str,
    routes: list[tuple[list[str], int]],
    *,
    evaluation_cp: int = 0,
) -> ChessFactPackage:
    board = chess.Board(fen)
    verified = [
        verify_route(
            route_id=f"pv_{index}",
            start_fen=fen,
            moves=[SimpleNamespace(uci=uci) for uci in moves],
            evaluation=evaluation,
            mate=None,
        )
        for index, (moves, evaluation) in enumerate(routes, start=1)
    ]
    assert all(route.verified for route in verified)
    return ChessFactPackage(
        position=FactPosition(
            fen=board.fen(),
            move_number=board.fullmove_number,
            side_to_move="white" if board.turn == chess.WHITE else "black",
        ),
        evaluation=FactEvaluation(
            evaluation_cp=evaluation_cp,
            evaluation_pawns=round(evaluation_cp / 100, 2),
        ),
        candidate_routes=verified,
    )


def center_break_package(route_count: int = 2) -> ChessFactPackage:
    fen = "r3k2r/pppp1ppp/8/8/4P3/8/PPPP1PPP/R3K2R w KQkq - 0 1"
    routes = [
        (["a1b1", "d7d5"], 20),
        (["h1g1", "d7d5"], 40),
        (["e1f1", "d7d5"], 30),
    ][:route_count]
    return package_with_routes(fen, routes)


def test_prepare_center_break_aggregates_common_goal_across_multipv() -> None:
    package = center_break_package(3)

    result = StrategicPlanAnalyzer().analyze(package)

    plan = next(item for item in result.plans if item.type == "prepare_center_break")
    assert plan.side == "black"
    assert plan.evidence_route_ids == ["pv_1", "pv_2", "pv_3"]
    assert "d5" in plan.supporting_moves
    assert plan.confidence == "high"
    assert plan.structural_evidence


def test_occupy_open_file_requires_rook_and_open_file_in_multiple_routes() -> None:
    package = package_with_routes(
        "6k1/8/8/8/8/8/6K1/R6R w - - 0 1",
        [
            (["a1d1", "g8f7"], 50),
            (["h1d1", "g8f7"], 70),
        ],
    )

    result = StrategicPlanAnalyzer().analyze(package)

    plan = next(item for item in result.plans if item.type == "occupy_open_file")
    assert plan.side == "white"
    assert "d开放线" in plan.goal
    assert plan.evidence_route_ids == ["pv_1", "pv_2"]
    assert plan.confidence == "high"


def test_random_unrelated_moves_do_not_create_plan() -> None:
    package = package_with_routes(
        chess.STARTING_FEN,
        [
            (["e2e4", "e7e5", "g1f3"], 20),
            (["d2d4", "d7d5", "c2c4"], 30),
        ],
    )

    assert StrategicPlanAnalyzer().analyze(package).plans == []


def test_single_pv_never_creates_strategic_plan() -> None:
    package = center_break_package(1)

    assert StrategicPlanAnalyzer().analyze(package).plans == []


def test_conflicting_route_evaluations_cancel_plan() -> None:
    package = package_with_routes(
        "6k1/8/8/8/8/8/6K1/R6R w - - 0 1",
        [
            (["a1d1", "g8f7"], 200),
            (["h1d1", "g8f7"], -300),
        ],
    )

    assert StrategicPlanAnalyzer().analyze(package).plans == []


def test_deepseek_unknown_plan_id_is_rejected() -> None:
    package = center_break_package(2)
    package.plans = StrategicPlanAnalyzer().analyze(package).plans
    draft = FactExplanationDraft(
        plan_explanations=[
            PlanExplanation(
                plan_id="plan_missing",
                explanation="这个方向能够逐步改善局面。",
            )
        ]
    )

    errors = validate_fact_explanation(draft, package)

    assert any("plan_id" in error for error in errors)


def test_deepseek_cannot_return_or_modify_plan_type() -> None:
    content = json.dumps({
        "information_insufficient": False,
        "summary": "",
        "actual_move_explanation": "",
        "best_move_explanation": "",
        "route_explanations": [],
        "event_explanations": [],
        "threat_explanations": [],
        "plan_explanations": [{
            "plan_id": "plan_1",
            "type": "invented_plan",
            "explanation": "这是新的计划。",
        }],
    }, ensure_ascii=False)

    draft, errors = parse_fact_explanation(content)

    assert draft is None
    assert errors


@pytest.mark.asyncio
async def test_deepseek_receives_confirmed_plans_without_fen() -> None:
    package = center_break_package(2)
    package.plans = StrategicPlanAnalyzer().analyze(package).plans
    plan_id = package.plans[0].plan_id
    explainer = DeepSeekExplainer(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    explainer._chat = AsyncMock(return_value=json.dumps({
        "information_insufficient": False,
        "summary": "双方仍需围绕结构安排棋子。",
        "actual_move_explanation": "",
        "best_move_explanation": "",
        "route_explanations": [],
        "event_explanations": [],
        "threat_explanations": [],
        "plan_explanations": [{
            "plan_id": plan_id,
            "explanation": "这个计划有多条稳定路线和明确的兵结构支持。",
        }],
    }, ensure_ascii=False))

    explanation = await explainer.explain_fact_package(package)

    prompt = explainer._chat.await_args.kwargs["prompt"]
    assert package.position.fen not in prompt
    assert f'"plan_id":"{plan_id}"' in prompt
    assert "多条稳定路线" in explanation


def test_plan_with_unknown_route_cannot_cross_model_boundary() -> None:
    package = center_break_package(2)
    plan = StrategicPlanAnalyzer().analyze(package).plans[0]
    package.plans = [
        plan.model_copy(update={"evidence_route_ids": ["pv_1", "pv_missing"]})
    ]

    assert package.plan_ids == set()
    assert package.prompt_payload()["plans"] == []


def test_improve_worst_piece_and_activate_rook_are_supported() -> None:
    worst_piece = package_with_routes(
        chess.STARTING_FEN,
        [
            (["d2d3", "a7a6", "c1g5"], 20),
            (["d2d4", "h7h6", "c1e3"], 40),
        ],
    )
    activate_rook = package_with_routes(
        "7k/p2p3p/8/8/8/8/P2P4/RN5K w - - 0 1",
        [
            (["b1c3", "a7a6", "a1d1"], 30),
            (["b1a3", "h7h6", "a1d1"], 50),
        ],
    )
    analyzer = StrategicPlanAnalyzer()

    assert "improve_worst_piece" in {
        item.type for item in analyzer.analyze(worst_piece).plans
    }
    assert "activate_rook" in {
        item.type for item in analyzer.analyze(activate_rook).plans
    }


def test_king_safety_weak_pawn_and_passed_pawn_plans_are_supported() -> None:
    king_safety = package_with_routes(
        "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1",
        [
            (["e1g1", "a7a6"], 20),
            (["h2h3", "a7a6", "e1g1"], 40),
        ],
    )
    weak_pawn = package_with_routes(
        "7k/8/8/3p4/8/8/6K1/R6R w - - 0 1",
        [
            (["a1d1", "h8g8"], 60),
            (["h1d1", "h8g8"], 80),
        ],
    )
    passed_pawn = package_with_routes(
        "7k/7p/8/3p4/2P5/8/8/7K w - - 0 1",
        [
            (["c4d5", "h7h6"], 100),
            (["h1g1", "h7h6", "c4d5"], 120),
        ],
    )
    analyzer = StrategicPlanAnalyzer()

    assert "improve_king_safety" in {
        item.type for item in analyzer.analyze(king_safety).plans
    }
    assert "attack_weak_pawn" in {
        item.type for item in analyzer.analyze(weak_pawn).plans
    }
    assert "create_passed_pawn" in {
        item.type for item in analyzer.analyze(passed_pawn).plans
    }


def test_simplify_endgame_requires_advantage_and_stable_exchange_routes() -> None:
    package = package_with_routes(
        "r2q2k1/8/8/8/8/8/8/R2Q2K1 w - - 0 1",
        [
            (["d1d8", "a8d8"], 250),
            (["a1a8", "d8a8"], 270),
        ],
        evaluation_cp=300,
    )

    result = StrategicPlanAnalyzer().analyze(package)

    plan = next(item for item in result.plans if item.type == "simplify_endgame")
    assert plan.side == "white"
    assert plan.confidence == "high"
