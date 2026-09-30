from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import chess
import pytest

from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.professional_analysis import (
    _fit_resolved_analysis_length,
    apply_hard_fact_guard,
    build_professional_payload,
    build_safe_professional_analysis,
    compute_professional_complexity,
    professional_user_prompt,
)
from app.narrative_claims import (
    NarrativeClaimPackage,
    _verified_alternative_pawn_trade,
    _verified_delayed_center_contact,
    _verified_double_attack_reply,
    _verified_fianchetto_center_order,
    _verified_opening_bishop_pressure,
    _verified_queenless_king_activity,
    compose_verified_core_paragraph,
    resolve_narrative_claims,
)
from app.professional_validation import build_validation_context, validate_professional_analysis
from scripts.run_professional_quality_suite import thought_path_summary


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "professional_validation_positions.json"


def load_positions() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_validation_set_has_required_categories_and_metadata() -> None:
    positions = load_positions()
    assert len(positions) == 15
    assert Counter(item["category"] for item in positions) == {
        "simple_opening": 3,
        "direct_tactics": 3,
        "king_attack": 3,
        "center_counter": 2,
        "closed_center_wing_attack": 2,
        "simplification_endgame": 2,
    }
    for item in positions:
        assert item["source"]["description"]
        assert item["expected"]["mainDanger"]
        assert item["expected"]["strategy"]["white"]
        assert item["expected"]["strategy"]["black"]
        assert item["expected"]["forbiddenConclusions"]


@pytest.mark.parametrize("position", load_positions(), ids=lambda item: item["id"])
def test_validation_set_routes_are_legal_and_from_the_saved_position(position: dict) -> None:
    board = chess.Board(position["fen"])
    assert board.is_valid()
    assert position["sideToMove"] == ("white" if board.turn else "black")

    played = chess.Move.from_uci(position["playedMove"]["uci"])
    assert played in board.legal_moves
    assert board.san(played) == position["playedMove"]["san"]

    lines = position["stockfishLines"]
    assert [line["rank"] for line in lines] == [1, 2, 3]
    for line in lines:
        route_board = board.copy(stack=False)
        assert 1 <= len(line["plies"]) <= 10
        for index, ply in enumerate(line["plies"], 1):
            move = chess.Move.from_uci(ply["uci"])
            assert move in route_board.legal_moves
            assert route_board.san(move) == ply["san"]
            assert ply["id"] == f"line:{line['rank']}:ply:{index}"
            route_board.push(move)


@pytest.mark.asyncio
async def test_all_validation_positions_build_compact_strict_fact_packages() -> None:
    settings = load_settings()
    engine = StockfishService(
        settings.stockfish_path,
        depth=10,
        threads=1,
        hash_mb=32,
        multipv=3,
        timeout_seconds=60,
    )
    for position in load_positions():
        review = await analyze_pgn(
            pgn=position["pgn"],
            stockfish=engine,
            analysis_id=f"test-{position['id']}",
            depth=10,
            timeout_seconds=90,
            max_plies=2,
        )
        move = review.moves[0]
        original_move = move.model_dump()
        complexity = compute_professional_complexity(move)
        context = build_validation_context(move, complexity.level)
        payload = build_professional_payload(move, complexity, context.allowed_evidence_ids)
        prompt = professional_user_prompt(payload, complexity.level)
        safe = build_safe_professional_analysis(move, complexity)
        claims = NarrativeClaimPackage.model_validate(payload["narrativeClaims"])
        guarded = apply_hard_fact_guard(safe, move, narrative_claims=claims)

        assert len(move.candidate_lines) == 3
        assert all(len(line.moves) <= 10 for line in move.candidate_lines)
        assert len(prompt) < 60_000
        assert '"bookEvaluationMethod"' in prompt
        assert '"decisionPriority"' in prompt
        if position["id"] in {"tactic-2", "king-attack-2", "simplify-1"}:
            assert payload["positionInterpretation"]["objective"]["kind"] == "forcing_tactics"
        assert validate_professional_analysis(safe, context) == []
        assert guarded.played_move_analysis.intention == compose_verified_core_paragraph(
            claims, guarded.played_move_analysis.claim_refs,
        )
        assert thought_path_summary(move, guarded, claims)["complete"], position["id"]
        selected = resolve_narrative_claims(claims, guarded.played_move_analysis.claim_refs)
        assert any(
            item.kind in {"verified_choice", "position_cause", "verified_plan", "verified_consequence"}
            or item.claim_id.endswith("position:plan-conflict")
            for item in selected
        ), position["id"]
        assert all(set(item.evidence_refs) <= context.allowed_evidence_ids for item in selected)
        core = guarded.played_move_analysis.intention
        assert not any(item.kind == "evaluation_comparison" for item in selected)
        assert not any(marker in core for marker in (
            "轮到白方落子时", "轮到黑方落子时", "并非退而求其次",
            "原有的优势在落子前", "分歧从这里出现", "已验证路线",
            "后评价", " cp", "定级", "首选是", "的评价接近",
        )), position["id"]
        assert move.model_dump() == original_move, "Removing score prose must not mutate engine data"
        if position["id"] == "opening-1":
            assert "实战路线稍后才走c4" in core
            assert "dxc4将这一接触转成兵的交换" in core
            tampered = move.model_copy(deep=True)
            assert tampered.actual_move_line is not None
            tampered.actual_move_line.moves[8].uci = "d5c5"
            assert _verified_delayed_center_contact(tampered) is None
        if position["id"] == "opening-2":
            assert "实战Bg2完成出子后，黑方以c5控制d4" in core
            assert "白方随后才走d4" in core
            tampered = move.model_copy(deep=True)
            assert tampered.actual_move_line is not None
            tampered.actual_move_line.moves[4].uci = "c7c6"
            assert _verified_fianchetto_center_order(tampered) is None
        if position["id"] == "opening-3":
            assert "保护e5兵的c6马施压" in core
            assert "黑马Nxe4吃掉e4兵" in core
            assert "黑马Nd6退开时又攻击b5象" in core
            assert "Bb5把原在f1的白象投入行动" not in core
            tampered = move.model_copy(deep=True)
            assert tampered.actual_move_line is not None
            tampered.actual_move_line.moves[4].uci = "e4f6"
            assert _verified_opening_bishop_pressure(tampered) is None
        if position["id"] == "king-attack-2":
            assert "同时攻击e6的黑后和e4的黑象" in core
            assert "以Qc4把后移出马的攻击范围" in core
            tampered = move.model_copy(deep=True)
            assert tampered.actual_move_line is not None
            tampered.actual_move_line.moves[0].uci = "e6e6"
            assert _verified_double_attack_reply(tampered) is None
        if position["id"] == "closed-1":
            assert "在封闭中心旁保护e5的本方兵" in core
            assert "以王翼兵推进争取空间" in core
        if position["id"] == "center-2":
            assert "随后马、象继续交换" in core
            assert "原在b6的兵来到c5" in core
            assert "实战换掉的则是原在e6的兵" in core
            assert "不能把评价损失简单说成亏子" not in core
            tampered = move.model_copy(deep=True)
            tampered.candidate_lines[0].moves[1].uci = "b6b5"
            assert move.actual_move_line is not None
            assert _verified_alternative_pawn_trade(
                tampered, chess.Move.from_uci(move.actual_move_line.moves[0].uci),
            ) is None
        if position["id"] == "simplify-1":
            assert "双后立即离盘" in core
            assert "直接攻击f8的黑车" not in core
        if position["id"] == "simplify-2":
            assert "让同一匹马来到e4时双后已经离盘" in core
            assert "h7—g6—f5" in core
            assert "白王还留在g1" in core
            assert "不能把分差解释成直接丢后" not in core
            tampered = move.model_copy(deep=True)
            assert tampered.actual_move_line is not None
            tampered.actual_move_line.moves[4].uci = "h7h6"
            assert _verified_queenless_king_activity(tampered) is None
        fitted = _fit_resolved_analysis_length(guarded, move, complexity.level)
        assert validate_professional_analysis(fitted, context) == [], position["id"]
