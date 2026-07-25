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
    ThreatExplanation,
    validate_fact_explanation,
    verify_route,
)
from app.models import EngineResult
from app.threat_analysis import ThreatAnalyzer


def package_with_routes(
    fen: str,
    routes: list[tuple[list[str], int | None, int | None]],
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
            mate=mate,
        )
        for index, (moves, evaluation, mate) in enumerate(routes, start=1)
    ]
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


class FakeIgnoreEngine:
    def __init__(self, centipawns: list[int]) -> None:
        self.centipawns = centipawns
        self.calls: list[tuple[list[str], int, float]] = []

    async def analyze_many(
        self,
        fens: list[str],
        *,
        depth: int,
        timeout_seconds: float,
    ) -> list[EngineResult]:
        self.calls.append((fens, depth, timeout_seconds))
        return [
            EngineResult(
                evaluation=f"{centipawn / 100:+.2f}",
                centipawn=centipawn,
                depth=depth,
                nodes=10,
                time_ms=1,
                top_moves=[],
            )
            for centipawn in self.centipawns[:len(fens)]
        ]


def test_real_tactical_capture_and_material_win_are_programmatically_detected() -> None:
    package = package_with_routes(
        "7k/8/8/8/3q4/8/3R4/7K b - - 0 1",
        [(["d4d2"], -500, None)],
    )

    threats = ThreatAnalyzer().detect(package)

    assert {threat.type for threat in threats} >= {"tactical_capture", "material_win"}
    tactical = next(threat for threat in threats if threat.type == "tactical_capture")
    assert tactical.side == "black"
    assert tactical.target == "d2"
    assert tactical.evidence_route_ids == ["pv_1"]
    assert tactical.evidence


def test_undefended_but_unexploitable_piece_is_not_a_threat() -> None:
    package = package_with_routes(
        "7k/8/8/8/8/8/3N4/7K b - - 0 1",
        [],
    )

    assert ThreatAnalyzer().detect(package) == []


@pytest.mark.asyncio
async def test_ignore_test_records_evaluation_loss_with_bounded_search() -> None:
    package = package_with_routes(
        "7k/8/8/8/3q4/8/3R4/7K b - - 0 1",
        [(["d4d2"], -500, None)],
        evaluation_cp=0,
    )
    engine = FakeIgnoreEngine([-400, -300, -200])
    analyzer = ThreatAnalyzer(
        ignore_depth=7,
        ignore_timeout_seconds=4,
        max_ignore_moves=3,
        max_ignore_tests=1,
    )

    result = await analyzer.analyze(package, stockfish=engine)

    tested = next(threat for threat in result.threats if threat.ignore_test.performed)
    assert tested.ignore_test.evaluation_before == 0
    assert tested.ignore_test.evaluation_after == -4
    assert tested.ignore_test.evaluation_loss == 4
    assert tested.ignore_test.ignored_move
    assert len(engine.calls) == 1
    assert len(engine.calls[0][0]) <= 3
    assert engine.calls[0][1:] == (7, 4)


def test_no_verified_route_evidence_returns_empty_package() -> None:
    package = package_with_routes(chess.STARTING_FEN, [])

    threats = ThreatAnalyzer().detect(package)

    assert threats == []


def test_threat_with_unknown_route_evidence_cannot_cross_model_boundary() -> None:
    package = package_with_routes(
        "7k/8/8/8/3q4/8/3R4/7K b - - 0 1",
        [(["d4d2"], -500, None)],
    )
    threat = ThreatAnalyzer().detect(package)[0]
    package.threats = [
        threat.model_copy(update={"evidence_route_ids": ["pv_missing"]})
    ]

    assert package.threat_ids == set()
    assert package.prompt_payload()["threats"] == []


def test_deepseek_unknown_threat_id_and_generated_move_are_rejected() -> None:
    package = package_with_routes(
        "7k/8/8/8/3q4/8/3R4/7K b - - 0 1",
        [(["d4d2"], -500, None)],
    )
    package.threats = ThreatAnalyzer().detect(package)
    draft = FactExplanationDraft(
        threat_explanations=[
            ThreatExplanation(
                threat_id="threat_missing",
                explanation="建议走Qh5来制造新的进攻。",
            )
        ],
    )

    errors = validate_fact_explanation(draft, package)

    assert any("threat_id" in error for error in errors)
    assert any("不得返回具体棋步" in error for error in errors)


def test_deepseek_cannot_return_or_modify_threat_type() -> None:
    content = json.dumps({
        "information_insufficient": False,
        "summary": "",
        "actual_move_explanation": "",
        "best_move_explanation": "",
        "route_explanations": [],
        "event_explanations": [],
        "threat_explanations": [{
            "threat_id": "threat_1",
            "type": "invented_threat",
            "explanation": "这是新的威胁类型。",
        }],
    }, ensure_ascii=False)

    draft, errors = parse_fact_explanation(content)

    assert draft is None
    assert errors


@pytest.mark.asyncio
async def test_deepseek_receives_program_threats_without_fen() -> None:
    package = package_with_routes(
        "7k/8/8/8/3q4/8/3R4/7K b - - 0 1",
        [(["d4d2"], -500, None)],
    )
    package.threats = ThreatAnalyzer().detect(package)
    threat_id = package.threats[0].threat_id
    explainer = DeepSeekExplainer(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    explainer._chat = AsyncMock(return_value=json.dumps({
        "information_insufficient": False,
        "summary": "程序确认当前存在直接危险。",
        "actual_move_explanation": "",
        "best_move_explanation": "",
        "route_explanations": [],
        "event_explanations": [],
        "threat_explanations": [{
            "threat_id": threat_id,
            "explanation": "如果不及时处理，重要子力会受到直接损失。",
        }],
    }, ensure_ascii=False))

    explanation = await explainer.explain_fact_package(package)

    prompt = explainer._chat.await_args.kwargs["prompt"]
    assert package.position.fen not in prompt
    assert f'"threat_id":"{threat_id}"' in prompt
    assert '"source":"python-chess+stockfish"' in prompt
    assert "重要子力会受到直接损失" in explanation


def test_mate_promotion_and_repeated_center_break_are_supported() -> None:
    mate = package_with_routes(
        "7k/8/6QK/8/8/8/8/8 w - - 0 1",
        [(["g6g7"], None, 1)],
    )
    promotion = package_with_routes(
        "7k/P7/8/8/8/8/8/7K w - - 0 1",
        [(["a7a8q"], 900, None)],
    )
    center = package_with_routes(
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1",
        [
            (["g1f3", "d7d5"], 20, None),
            (["b1c3", "d7d5"], 40, None),
        ],
    )
    analyzer = ThreatAnalyzer()

    assert "mate_threat" in {item.type for item in analyzer.detect(mate)}
    assert "promotion_threat" in {item.type for item in analyzer.detect(promotion)}
    assert "center_break" in {item.type for item in analyzer.detect(center)}
