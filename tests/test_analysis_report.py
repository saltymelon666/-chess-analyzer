from __future__ import annotations

import json
from unittest.mock import AsyncMock

import chess
import pytest

from app.analysis_report import (
    AnalysisReportPackage,
    build_analysis_report,
    build_fallback_report,
    validate_report_package,
)
from app.chess_facts import FactCandidateRoute, build_move_fact_package
from app.models import (
    CandidateLine,
    ComplexityFactors,
    EvidenceFact,
    EvaluationSnapshot,
    MoveFacts,
    MoveReview,
    VariationMove,
)
from app.narrative_generator import (
    NarrativeChatResult,
    NarrativeGenerator,
    apply_narrative_draft,
    parse_narrative_draft,
    validate_narrative_draft,
)
from app.position_facts import extract_position_facts
from app.strategic_plans import StrategicPlanFact, StrategicPlanPackage
from app.threat_analysis import (
    ThreatFact,
    ThreatPackage,
    position_id,
)


def _piece_id(piece: chess.Piece | None) -> str:
    if piece is None:
        return "unknown"
    side = "white" if piece.color == chess.WHITE else "black"
    return f"{side}_{chess.piece_name(piece.piece_type)}"


def _line(rank: int, uci_moves: list[str]) -> CandidateLine:
    board = chess.Board()
    moves: list[VariationMove] = []
    for ply, uci in enumerate(uci_moves, start=1):
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        piece = board.piece_at(move.from_square)
        captured = board.piece_at(move.to_square)
        san = board.san(move)
        moves.append(
            VariationMove(
                id=f"line:{rank}:ply:{ply}",
                ply=ply,
                move_number=board.fullmove_number,
                side="white" if board.turn == chess.WHITE else "black",
                san=san,
                uci=uci,
                from_square=chess.square_name(move.from_square),
                to_square=chess.square_name(move.to_square),
                piece=_piece_id(piece),
                capture=board.is_capture(move),
                captured_piece=_piece_id(captured) if captured else None,
                check=board.gives_check(move),
                checkmate=False,
                castling=board.is_castling(move),
                promotion=chess.piece_name(move.promotion) if move.promotion else None,
            )
        )
        board.push(move)
    first = moves[0]
    return CandidateLine(
        id=f"pv_{rank}",
        rank=rank,
        depth=18,
        evaluation=80 - rank * 10,
        firstMove=MoveFacts(
            id=first.id,
            san=first.san,
            uci=first.uci,
            from_square=first.from_square,
            to_square=first.to_square,
            piece=first.piece,
            capture=first.capture,
            captured_piece=first.captured_piece,
            check=first.check,
            checkmate=first.checkmate,
            castling=first.castling,
            promotion=first.promotion,
        ),
        pv=moves,
        resultingFen=board.fen(),
    )


def sample_move_review() -> MoveReview:
    candidate_lines = [
        _line(1, ["e2e4", "e7e5", "g1f3"]),
        _line(2, ["d2d4", "d7d5", "c2c4"]),
        _line(3, ["g1f3", "g8f6", "d2d4"]),
    ]
    actual_line = _line(4, ["a2a3", "e7e5"])
    actual_line.id = "actual_1"
    board_after = chess.Board()
    move = chess.Move.from_uci("a2a3")
    played = MoveFacts(
        id="move:played:1",
        san=board_after.san(move),
        uci=move.uci(),
        from_square="a2",
        to_square="a3",
        piece="white_pawn",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )
    board_after.push(move)
    position_before = extract_position_facts(
        chess.STARTING_FEN,
        candidate_lines=candidate_lines,
        actual_move_line=actual_line,
        tactics=[],
        namespace="report-before",
    )
    position_after = extract_position_facts(
        board_after.fen(),
        candidate_lines=[],
        actual_move_line=None,
        tactics=[],
        namespace="report-after",
    )
    return MoveReview(
        index=1,
        move_number=1,
        notation="1. a3",
        side="white",
        san=played.san,
        uci=played.uci,
        from_square=played.from_square,
        to_square=played.to_square,
        before_fen=chess.STARTING_FEN,
        after_fen=board_after.fen(),
        before=EvaluationSnapshot(evaluation="+0.80", centipawn=80),
        after=EvaluationSnapshot(evaluation="-1.20", centipawn=-120),
        played_move=played,
        best_move=candidate_lines[0].first_move,
        opponent_reply=None,
        centipawn_loss=200,
        best_move_uci=candidate_lines[0].first_move.uci,
        best_move_san=candidate_lines[0].first_move.san,
        best_pv=[item.san for item in candidate_lines[0].moves],
        quality_key="mistake",
        quality_symbol="?",
        quality_label="失误",
        mate_involved=False,
        only_legal_move=False,
        principal_variation=[item.san for item in candidate_lines[0].moves],
        principal_variation_facts=[],
        opponent_variation=[],
        opponent_variation_facts=[],
        complexity="normal",
        complexity_factors=ComplexityFactors(
            legal_move_count=20,
            candidate_gap_cp=10,
            only_reasonable_move=False,
            pv_length=3,
            evaluation_swing_cp=200,
            forcing_line_plies=0,
            engaged_piece_count=4,
        ),
        candidate_lines=candidate_lines,
        actual_move_line=actual_line,
        position_facts=position_before,
        position_facts_after=position_after,
    )


def report_package(
    *,
    include_threat: bool = True,
    include_plan: bool = True,
) -> AnalysisReportPackage:
    move = sample_move_review()
    facts = build_move_fact_package(move)
    threats = [
        ThreatFact(
            threat_id="threat_1",
            type="center_break",
            side="white",
            target="e5",
            supporting_moves=["e4"],
            evidence_route_ids=["pv_1"],
            evidence=["第一条verified路线支持中心突破"],
            confidence="high",
        )
    ] if include_threat else []
    threat_package = ThreatPackage(
        position_id=position_id(facts.position.fen),
        threats=threats,
    )
    plans = [
        StrategicPlanFact(
            plan_id="plan_1",
            side="white",
            type="prepare_center_break",
            goal="准备中心突破",
            supporting_moves=["e4", "d4"],
            evidence_route_ids=["pv_1", "pv_2"],
            structural_evidence=["两条verified路线都支持先争夺中心"],
            confidence="high",
        )
    ] if include_plan else []
    plan_package = StrategicPlanPackage(
        position_id=position_id(facts.position.fen),
        plans=plans,
    )
    facts.threats = threats
    facts.plans = plans
    return build_analysis_report(move, facts, threat_package, plan_package)


def valid_payload(package: AnalysisReportPackage) -> dict[str, object]:
    return {
        "information_insufficient": False,
        "position_summary": "由程序生成",
        "move_explanation": "由程序生成",
        "threat_explanation": [
            {
                "threat_id": item.threat_id,
                "explanation": "程序确认的中心突破会改变中心结构，因此需要优先评估应对顺序。",
            }
            for item in package.threat_section.items
        ],
        "plan_explanation": [
            {
                "plan_id": item.plan_id,
                "explanation": "准备中心突破的计划合理，因为多条已验证路线支持先完成子力协调。",
            }
            for item in package.strategy_section.items
        ],
        "route_explanation": [
            {
                "route_id": item.route_id,
                "explanation": "该路线展示了程序确认的主要延续方式，实际对局仍需逐步核对。",
            }
            for item in package.route_section.routes
        ],
        "final_summary": {
            "text": (
                "本步的主要问题是错过改进机会，还要兼顾中心威胁与长期计划。"
                if package.threat_section.items and package.strategy_section.items
                else "本步的主要问题是错过程序确认的改进机会。"
            ),
            "source_refs": [
                package.move_analysis.move_error_id,
                *package.threat_section.threat_ids[:1],
                *package.strategy_section.plan_ids[:1],
            ],
        },
    }


def chat_result(payload: dict[str, object]) -> NarrativeChatResult:
    return NarrativeChatResult(
        content=json.dumps(payload, ensure_ascii=False),
        prompt_tokens=300,
        completion_tokens=180,
        total_tokens=480,
        elapsed_ms=25,
    )


@pytest.mark.asyncio
async def test_complete_report_generation_uses_one_narrative_call() -> None:
    package = report_package()
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(valid_payload(package)))

    generated = await generator.generate(package)

    assert generator._chat.await_count == 1
    assert generated.usage.attempts == 1
    assert generated.usage.used_fallback is False
    assert generated.report.position_overview.text
    assert generated.report.move_analysis.text
    assert generated.report.threat_section.items[0].explanation
    assert generated.report.strategy_section.items[0].explanation
    assert all(item.moves_san for item in generated.report.route_section.routes)
    assert validate_report_package(generated.report) == []


@pytest.mark.asyncio
async def test_position_without_threat_cannot_generate_threat_section() -> None:
    package = report_package(include_threat=False)
    payload = valid_payload(package)
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(payload))

    generated = await generator.generate(package)

    assert generated.usage.used_fallback is False
    assert generated.report.threat_section.threat_ids == []
    assert generated.report.threat_section.items == []


@pytest.mark.asyncio
async def test_empty_threat_package_rejects_invented_threat() -> None:
    package = report_package(include_threat=False)
    payload = valid_payload(package)
    payload["threat_explanation"] = [
        {"threat_id": "threat_invented", "explanation": "这里存在新的将杀威胁。"}
    ]
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(payload))

    generated = await generator.generate(package)

    assert generated.usage.used_fallback is True
    assert generated.report.threat_section.items == []


@pytest.mark.asyncio
async def test_position_without_plan_cannot_generate_plan_section() -> None:
    package = report_package(include_plan=False)
    payload = valid_payload(package)
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(payload))

    generated = await generator.generate(package)

    assert generated.usage.used_fallback is False
    assert generated.report.strategy_section.plan_ids == []
    assert generated.report.strategy_section.items == []


@pytest.mark.asyncio
async def test_model_cannot_change_confirmed_plan_type() -> None:
    package = report_package()
    payload = valid_payload(package)
    payload["plan_explanation"][0]["explanation"] = "这个简化残局计划可以减少复杂变化。"
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(payload))

    generated = await generator.generate(package)

    assert generated.usage.used_fallback is True
    assert any("修改了战略类型" in warning for warning in generated.validation_warnings)


@pytest.mark.asyncio
async def test_unknown_id_fails_validation_and_falls_back_without_retry() -> None:
    package = report_package()
    payload = valid_payload(package)
    payload["threat_explanation"][0]["threat_id"] = "threat_missing"
    draft, parse_errors = parse_narrative_draft(json.dumps(payload, ensure_ascii=False))
    assert draft is not None
    assert parse_errors == []
    assert any("不存在的threat_id" in error for error in validate_narrative_draft(draft, package))
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(payload))

    generated = await generator.generate(package)

    assert generator._chat.await_count == 1
    assert generated.usage.used_fallback is True
    assert validate_report_package(generated.report) == []


@pytest.mark.asyncio
async def test_model_generated_move_is_rejected() -> None:
    package = report_package()
    payload = valid_payload(package)
    payload["move_explanation"] = "这里应该改走e4，才能争取主动。"
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(payload))

    generated = await generator.generate(package)

    assert generated.usage.used_fallback is True
    assert any("SAN棋步" in warning for warning in generated.validation_warnings)


@pytest.mark.asyncio
async def test_wrong_evaluation_direction_is_rejected() -> None:
    package = report_package()
    payload = valid_payload(package)
    payload["position_summary"] = "程序评价显示黑方占优，白方需要改变局面。"
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(payload))

    generated = await generator.generate(package)

    assert generated.usage.used_fallback is True
    assert any("黑方优势" in warning for warning in generated.validation_warnings)
    assert "白方占优" in generated.report.position_overview.text


@pytest.mark.asyncio
async def test_summary_without_source_refs_falls_back() -> None:
    package = report_package()
    payload = valid_payload(package)
    payload["final_summary"]["source_refs"] = []
    generator = NarrativeGenerator(
        api_key="test",
        base_url="https://example.invalid",
        model="test",
        timeout_seconds=1,
    )
    generator._chat = AsyncMock(return_value=chat_result(payload))

    generated = await generator.generate(package)

    assert generated.usage.used_fallback is True
    assert generated.report.summary_section.source_refs
    assert validate_report_package(generated.report) == []


def test_prompt_payload_excludes_fen_moves_and_unverified_routes() -> None:
    move = sample_move_review()
    facts = build_move_fact_package(move)
    facts.candidate_routes.append(
        FactCandidateRoute(
            route_id="pv_unverified",
            verified=False,
            error="illegal_move",
        )
    )
    threat_package = ThreatPackage(
        position_id=position_id(facts.position.fen),
        threats=[],
    )
    plan_package = StrategicPlanPackage(
        position_id=position_id(facts.position.fen),
        plans=[],
    )
    package = build_analysis_report(move, facts, threat_package, plan_package)
    payload = package.prompt_payload()
    encoded = json.dumps(payload, ensure_ascii=False)

    assert move.before_fen not in encoded
    assert move.played_move.uci not in encoded
    assert "moves_san" not in encoded
    assert "moves_uci" not in encoded
    assert "pv_unverified" not in encoded
    assert all(set(item) == {"route_id"} for item in payload["routes"])


def test_report_builder_rejects_package_from_another_position() -> None:
    move = sample_move_review()
    facts = build_move_fact_package(move)
    threats = ThreatPackage(position_id="wrong-position", threats=[])
    plans = StrategicPlanPackage(
        position_id=position_id(facts.position.fen),
        plans=[],
    )

    with pytest.raises(ValueError, match="ThreatPackage position"):
        build_analysis_report(move, facts, threats, plans)


def test_program_fallback_is_complete_and_nonblank() -> None:
    package = report_package()

    fallback = build_fallback_report(package)

    assert fallback.position_overview.evaluation.evaluation_cp == 80
    assert fallback.position_overview.advantage_side == "white"
    assert fallback.position_overview.material.fact_id
    assert fallback.position_overview.position_fact_ids
    assert fallback.summary_section.source_refs
    assert validate_report_package(fallback) == []


def test_report_surfaces_interpretation_themes_without_losing_route_scope() -> None:
    move = sample_move_review()
    move.position_facts.threats.extend([
        EvidenceFact(
            id="fact:current-double-attack",
            category="double_attack",
            side="white",
            description="a3后同时攻击两个目标。",
            evidence=["python-chess验证走法a2a3"],
            squares=["a3"],
        ),
        EvidenceFact(
            id="fact:route-pin",
            category="pin",
            side="black",
            description="e5后候选路线内形成牵制。",
            evidence=["python-chess验证走法e7e5"],
            squares=["e5"],
        ),
    ])
    facts = build_move_fact_package(move)
    threats = ThreatPackage(position_id=position_id(facts.position.fen), threats=[])
    plans = StrategicPlanPackage(position_id=position_id(facts.position.fen), plans=[])

    report = build_fallback_report(build_analysis_report(move, facts, threats, plans))

    assert "当前局面主题（双重攻击）" in report.position_overview.text
    assert "候选路线内部主题（牵制）" in report.position_overview.text


def test_narrative_cannot_overwrite_program_owned_position_and_move_facts() -> None:
    package = report_package()
    payload = valid_payload(package)
    payload["position_summary"] = "白方多一兵，黑王准备易位。"
    payload["move_explanation"] = "实战着与Stockfish首选一致，是最佳着。"
    draft, errors = parse_narrative_draft(json.dumps(payload, ensure_ascii=False))
    assert draft is not None
    assert errors == []

    report = apply_narrative_draft(package, draft)

    assert "白方多一兵" not in report.position_overview.text
    assert "准备易位" not in report.position_overview.text
    assert "与程序首选一致" not in report.move_analysis.text
    assert package.move_analysis.same_as_best is False
    assert "程序给出的主要改进是" in report.move_analysis.text


def test_unknown_initiative_rejects_narrative_claim() -> None:
    package = report_package()
    assert package.initiative.side == "unknown"
    payload = valid_payload(package)
    payload["final_summary"]["text"] = "白方已经掌握主动权。"
    draft, errors = parse_narrative_draft(json.dumps(payload, ensure_ascii=False))
    assert draft is not None
    assert errors == []

    validation = validate_narrative_draft(draft, package)

    assert any("主动权证据门禁" in error for error in validation)


def test_prompt_payload_marks_unknown_initiative() -> None:
    package = report_package()

    payload = package.prompt_payload()

    assert payload["initiative"]["side"] == "unknown"
    assert payload["initiative"]["dynamic_source"] is False
