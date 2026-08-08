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
from app.models import EngineResult, MoveResult
from app.threat_analysis import (
    ThreatAnalyzer,
    ThreatFact,
    ThreatPackage,
    assess_initiative,
)


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
    def __init__(
        self,
        centipawns: list[int],
        *,
        top_move_uci: str | None = None,
        top_move_san: str | None = None,
    ) -> None:
        self.centipawns = centipawns
        self.top_move_uci = top_move_uci
        self.top_move_san = top_move_san
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
                top_moves=(
                    [MoveResult(
                        move=self.top_move_uci,
                        san=self.top_move_san or self.top_move_uci,
                        centipawn=centipawn,
                        pv=[],
                        depth=depth,
                    )]
                    if self.top_move_uci else []
                ),
            )
            for centipawn in self.centipawns[:len(fens)]
        ]


class FakeMateProbeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int, float]] = []

    async def analyze_many(
        self,
        fens: list[str],
        *,
        depth: int,
        timeout_seconds: float,
    ) -> list[EngineResult]:
        self.calls.append((fens, depth, timeout_seconds))
        return [EngineResult(
            evaluation="黑方 M3",
            centipawn=None,
            mate_in=-3,
            depth=depth,
            nodes=10,
            time_ms=1,
            top_moves=[MoveResult(
                move="c4f1",
                san="Qf1+",
                centipawn=None,
                mate_in=-3,
                pv=[],
                depth=depth,
            )],
        )]


def test_real_tactical_capture_and_material_win_are_programmatically_detected() -> None:
    package = package_with_routes(
        "7k/8/8/8/3q4/8/3R4/7K b - - 0 1",
        [(["d4d2"], -500, None)],
    )

    threats = ThreatAnalyzer().detect(package)

    assert {threat.type for threat in threats} >= {"tactical_capture", "material_win"}
    tactical = next(threat for threat in threats if threat.type == "tactical_capture")
    assert tactical.scope == "current_direct_threat"
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


@pytest.mark.asyncio
async def test_prepared_threat_requires_ignore_test_before_global_promotion() -> None:
    package = package_with_routes(
        "3r3k/3b4/8/8/8/8/3Q4/7K b - - 0 1",
        [(["d7e6", "h1g1", "d8d2"], -900, None)],
        evaluation_cp=-700,
    )
    analyzer = ThreatAnalyzer(
        evaluation_loss_threshold_pawns=1.5,
        max_ignore_tests=1,
    )
    classified = analyzer.classify(package)
    assert classified.threats == []
    event = next(
        item
        for item in classified.route_events
        if item.type == "tactical_capture"
    )
    assert event.scope == "route_event"
    assert event.preparation_moves == ["Be6"]

    result = await analyzer.analyze(
        package,
        stockfish=FakeIgnoreEngine([-1200, -1100, -1000]),
    )

    assert len(result.prepared_threats) == 1
    prepared = result.prepared_threats[0]
    assert prepared.scope == "prepared_threat"
    assert prepared.ignore_test.performed
    assert prepared.ignore_test.evaluation_loss is not None
    assert prepared.ignore_test.evaluation_loss >= 1.5
    assert prepared in result.threats


@pytest.mark.asyncio
async def test_latent_opponent_double_attack_becomes_prepared_only_after_ignore_test() -> None:
    package = package_with_routes(
        "rnb1kb1r/ppp2ppp/4pn2/q7/2BP4/2N5/PPPB1PPP/R2QK1NR b KQkq - 0 1",
        [
            (["a5b6"], 82, None),
            (["a5d5"], 90, None),
        ],
        evaluation_cp=82,
    )
    analyzer = ThreatAnalyzer(
        evaluation_loss_threshold_pawns=1.5,
        max_ignore_moves=3,
        max_ignore_tests=1,
    )

    result = await analyzer.analyze(
        package,
        stockfish=FakeIgnoreEngine(
            [313, 324, 319],
            top_move_uci="c3d5",
            top_move_san="Nd5",
        ),
    )

    assert len(result.prepared_threats) == 1
    prepared = result.prepared_threats[0]
    assert prepared.type == "prepared_tactic"
    assert prepared.scope == "prepared_threat"
    assert prepared.side == "white"
    assert prepared.supporting_moves == ["Nd5"]
    assert prepared.preparation_moves == ["Nd5"]
    assert prepared.ignore_test.performed
    assert prepared.ignore_test.evaluation_loss is not None
    assert prepared.ignore_test.evaluation_loss >= 1.5
    assert prepared in result.threats


@pytest.mark.asyncio
async def test_latent_tactic_is_rejected_when_one_sampled_ignore_is_safe() -> None:
    package = package_with_routes(
        "rnb1kb1r/ppp2ppp/4pn2/q7/2BP4/2N5/PPPB1PPP/R2QK1NR b KQkq - 0 1",
        [(["a5b6"], 82, None)],
        evaluation_cp=82,
    )
    analyzer = ThreatAnalyzer(
        evaluation_loss_threshold_pawns=1.5,
        max_ignore_moves=3,
        max_ignore_tests=1,
    )

    result = await analyzer.analyze(
        package,
        stockfish=FakeIgnoreEngine(
            [313, 150, 319],
            top_move_uci="c3d5",
            top_move_san="Nd5",
        ),
    )

    assert result.prepared_threats == []


@pytest.mark.asyncio
async def test_multiple_checks_trigger_bounded_deeper_mate_probe() -> None:
    package = package_with_routes(
        "rn3rk1/p5pp/2p5/3Ppb2/2q5/6Q1/PPPB2PP/R3K1NR b KQ - 1 15",
        [(["c4c2"], -247, None)],
        evaluation_cp=-247,
    )
    engine = FakeMateProbeEngine()

    result = await ThreatAnalyzer(
        max_ignore_tests=0,
        mate_probe_depth=13,
    ).analyze(package, stockfish=engine)

    mate = next(item for item in result.threats if item.type == "mate_threat")
    assert mate.scope == "current_direct_threat"
    assert mate.side == "black"
    assert mate.supporting_moves == ["Qf1+"]
    assert mate.evidence_route_ids == ["depth_mate_probe"]
    assert mate.source == "python-chess+stockfish+depth-escalation"
    assert package.candidate_routes[-1].route_id == "depth_mate_probe"
    assert engine.calls[0][1] == 13


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


def test_mate_and_promotion_are_current_but_pv_center_break_is_route_only() -> None:
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
    assert "center_break" not in {item.type for item in analyzer.detect(center)}
    classified = analyzer.classify(center)
    assert "center_break" in {item.type for item in classified.route_events}
    assert all(item.scope == "route_event" for item in classified.route_events)


@pytest.mark.parametrize(
    ("position_id", "fen", "routes", "forbidden_moves"),
    [
        (
            "CC-035",
            "3r1n1k/pbq1rpp1/1p2p2p/2p1P2Q/2BPN1R1/2PR4/P4PPP/6K1 w - - 0 1",
            [
                (["g4g7", "h8g7", "d3h3", "f7f5", "e5f6", "g7g8", "h5g4", "g8f7"], 502, None),
                (["e4f6", "f8g6", "g4g6", "f7g6", "h5g6", "b7e4", "f6e4", "c5d4"], 342, None),
                (["e4d6", "c5d4", "g4g7", "h8g7", "d3g3", "g7h7", "c4d3", "f8g6", "d3g6", "f7g6"], 211, None),
            ],
            {"fxg6", "Nxe4", "Kxg7"},
        ),
        (
            "CC-036",
            "r2q1rk1/1b3ppp/pnn1p3/1pppP3/3P1P2/2PB1N2/PP1N2PP/R2Q1RK1 w - - 0 1",
            [
                (["d3h7", "g8h8", "f3g5", "g7g6", "d1g4", "c5d4", "g4h3", "h8g7", "h7g6", "f8h8"], 386, None),
                (["d2b3", "h7h6", "b3c5", "b7c8", "b2b4", "c8d7", "d1e2"], 299, None),
                (["d4c5", "b6c4", "d2c4", "b5c4", "d3h7", "g8h7", "f3g5", "h7g8"], 233, None),
            ],
            {"bxc4", "Kxh7"},
        ),
        (
            "GEL-03",
            "1r3bk1/1p1n1ppp/pPp1rn2/N1Pppb2/1P1P4/2N1P3/4BPPP/R1B2RK1 w - - 1 18",
            [
                (["f2f3", "e5d4", "e3d4", "d7b6", "g2g4", "f5g6", "c1f4", "b8e8", "e2a6", "b7a6"], 187, None),
                (["h2h3", "h7h5", "g1h2", "g7g5", "f2f3", "f8g7"], 157, None),
                (["h2h4", "e5d4", "e3d4", "d7b6", "c1f4", "b8e8", "e2a6", "b7a6", "c5b6", "f8b4"], 152, None),
            ],
            {"cxb6"},
        ),
    ],
)
def test_phase5d_pv_captures_are_not_promoted_to_current_threats(
    position_id: str,
    fen: str,
    routes: list[tuple[list[str], int | None, int | None]],
    forbidden_moves: set[str],
) -> None:
    del position_id
    package = package_with_routes(fen, routes)

    classified = ThreatAnalyzer().classify(package)

    current_moves = {
        move
        for threat in classified.threats
        for move in threat.supporting_moves
    }
    route_moves = {
        move
        for event in classified.route_events
        for move in event.supporting_moves
    }
    assert current_moves.isdisjoint(forbidden_moves)
    assert forbidden_moves <= route_moves


def test_stockfish_score_alone_never_grants_initiative() -> None:
    package = package_with_routes(chess.STARTING_FEN, [], evaluation_cp=240)
    threats = ThreatPackage(position_id="position:test")

    initiative = assess_initiative(package, threats)

    assert initiative.side == "unknown"
    assert not initiative.dynamic_source


def test_initiative_requires_dynamic_threats_and_two_forcing_routes() -> None:
    package = package_with_routes(
        "7k/8/6QK/8/8/8/8/8 w - - 0 1",
        [
            (["g6g7"], 900, None),
            (["g6e8"], 900, None),
        ],
    )
    threat = ThreatFact(
        threat_id="threat_1",
        type="mate_threat",
        side="white",
        target="h8",
        supporting_moves=["Qg7+", "Qe8+"],
        evidence_route_ids=["pv_1", "pv_2"],
        evidence=["two verified forcing routes"],
        confidence="high",
    )
    package_threats = ThreatPackage(
        position_id="position:test",
        threats=[threat],
    )

    initiative = assess_initiative(package, package_threats)

    assert initiative.side == "white"
    assert initiative.dynamic_source
    assert initiative.forcing_route_ids == ["pv_1", "pv_2"]
