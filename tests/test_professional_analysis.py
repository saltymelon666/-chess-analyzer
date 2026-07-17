import asyncio
from unittest.mock import AsyncMock

import chess
from fastapi.testclient import TestClient
import pytest

from app import api
from app.game_review import _move_facts, _variation_moves
from app.models import (
    CandidateLine,
    ComplexityFactors,
    EvaluationSnapshot,
    GeneratedProfessionalAnalysis,
    MoveReview,
    ProfessionalAnalysisUsage,
)
from app.position_facts import extract_position_facts
from app.professional_analysis import (
    ChatResult,
    PROFESSIONAL_PROMPT_VERSION,
    PROFESSIONAL_TOKEN_LIMITS,
    ProfessionalAnalysisService,
    build_safe_professional_analysis,
    compute_professional_complexity,
    professional_cache_key,
)
from app.professional_validation import (
    LENGTH_RANGES,
    build_validation_context,
    validate_professional_analysis,
)


def _line(board: chess.Board, rank: int, uci_moves: list[str], line_id: str) -> CandidateLine:
    current = board.copy(stack=False)
    facts = []
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        facts.append(_move_facts(current, move))
        current.push(move)
    moves, resulting_fen = _variation_moves(board, facts)
    for item in moves:
        item.id = f"{line_id}:ply:{item.ply}"
    line = CandidateLine(
        id=line_id,
        rank=rank,
        depth=18,
        evaluation=30 - rank * 10,
        mate=None,
        firstMove=facts[0],
        pv=moves,
        resultingFen=resulting_fen,
    )
    line.resulting_position_facts = extract_position_facts(
        resulting_fen,
        candidate_lines=[],
        actual_move_line=None,
        tactics=[],
        namespace=f"{line_id.replace(':', '-')}-result",
    )
    return line


def professional_review() -> MoveReview:
    board = chess.Board()
    played = _move_facts(board, chess.Move.from_uci("e2e4"))
    played.id = "move:played:1"
    after = board.copy(stack=False)
    after.push_uci("e2e4")
    candidates = [
        _line(board, 1, ["e2e4", "e7e5", "g1f3", "b8c6"], "line:1"),
        _line(board, 2, ["d2d4", "d7d5", "g1f3", "g8f6"], "line:2"),
        _line(board, 3, ["g1f3", "g8f6", "d2d4", "d7d5"], "line:3"),
    ]
    actual = _line(after, 1, ["e7e5", "g1f3", "b8c6", "f1c4"], "line:played")
    before_facts = extract_position_facts(
        board.fen(), candidate_lines=candidates, actual_move_line=actual, tactics=[], namespace="move-1-before"
    )
    after_facts = extract_position_facts(
        after.fen(), candidate_lines=[], actual_move_line=actual, tactics=[], namespace="move-1-after"
    )
    allowed_squares = sorted(
        {piece["square"] for piece in before_facts.pieces}
        | {square for line in [*candidates, actual] for item in line.moves for square in (item.from_square, item.to_square)}
    )
    allowed_moves = sorted(
        {played.san, played.uci}
        | {value for line in [*candidates, actual] for item in line.moves for value in (item.san, item.uci)}
    )
    return MoveReview(
        index=1,
        move_number=1,
        notation="1.e4",
        side="white",
        san=played.san,
        uci=played.uci,
        from_square=played.from_square,
        to_square=played.to_square,
        before_fen=board.fen(),
        after_fen=after.fen(),
        before=EvaluationSnapshot(evaluation="+0.30", centipawn=30),
        after=EvaluationSnapshot(evaluation="+0.10", centipawn=10),
        played_move=played,
        best_move=candidates[0].first_move,
        opponent_reply=actual.first_move,
        centipawn_loss=20,
        best_move_uci="e2e4",
        best_move_san="e4",
        best_pv=[item.san for item in candidates[0].moves],
        quality_key="best",
        quality_symbol="!",
        quality_label="最佳着",
        mate_involved=False,
        only_legal_move=False,
        principal_variation=[item.san for item in candidates[0].moves],
        complexity="normal",
        complexity_factors=ComplexityFactors(
            legal_move_count=20,
            candidate_gap_cp=10,
            only_reasonable_move=False,
            pv_length=4,
            evaluation_swing_cp=20,
            forcing_line_plies=0,
            engaged_piece_count=0,
        ),
        verified_facts=["白方实战走了e4。"],
        allowed_squares=allowed_squares,
        allowed_moves=allowed_moves,
        pieces_before={piece["square"]: f"{piece['side']}_{piece['piece']}" for piece in before_facts.pieces},
        candidate_lines=candidates,
        actual_move_line=actual,
        position_facts=before_facts,
        position_facts_after=after_facts,
    )


def test_stable_evidence_ids_and_cache_key_cover_routes_and_prompt_version() -> None:
    move = professional_review()
    context = build_validation_context(move, compute_professional_complexity(move).level)
    assert "line:1" in context.allowed_evidence_ids
    assert "line:1:ply:1" in context.allowed_evidence_ids
    assert any(item.startswith("piece:move-1-before:white-knight-") for item in context.allowed_evidence_ids)
    assert any(item.startswith("fact:move-1-before:king:") for item in context.allowed_evidence_ids)
    first = professional_cache_key(move, stockfish_version="Stockfish 18", stockfish_depth=18)
    second = professional_cache_key(move, stockfish_version="Stockfish 18", stockfish_depth=18)
    assert first == second
    assert PROFESSIONAL_PROMPT_VERSION
    move.candidate_lines[0].moves[0].uci = "a2a3"
    assert professional_cache_key(move, stockfish_version="Stockfish 18", stockfish_depth=18) != first


def test_validator_rejects_hallucinated_evidence_square_piece_and_route() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    payload = build_safe_professional_analysis(move, complexity).model_dump(by_alias=True)
    payload["mainDanger"]["description"] = "黑后在h9攻击白王，白方会立即受损。"
    payload["mainDanger"]["sideInDanger"] = "white"
    payload["mainDanger"]["evidenceRefs"] = ["fact:not-real"]
    payload["keyPieces"][0].update({"side": "black", "piece": "queen", "square": "a1"})
    payload["playedMoveAnalysis"]["move"] = "Qh5"
    payload["playedMoveAnalysis"]["positiveEffects"] = ["实战走法吃掉了黑后。"]
    payload["candidateLines"][0]["firstMove"] = "Qh5"
    payload["candidateLines"][0]["continuationPhases"][0]["moves"] = ["d4"]
    from app.models import ProfessionalAnalysis
    analysis = ProfessionalAnalysis.model_validate(payload)
    errors = validate_professional_analysis(analysis, context, enforce_length=False)
    assert any("evidenceRefs" in error for error in errors)
    assert any("棋盘范围外" in error for error in errors)
    assert any("关键棋子" in error for error in errors)
    assert any("实际走法" in error for error in errors)
    assert any("未验证的吃子" in error for error in errors)
    assert any("firstMove" in error for error in errors)
    assert any("候选路线1引用了" in error for error in errors)


def test_validator_prevents_fourth_line_and_empty_vague_claim() -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    context = build_validation_context(move, complexity.level)
    payload = build_safe_professional_analysis(move, complexity).model_dump(by_alias=True)
    fourth = dict(payload["candidateLines"][0])
    fourth["rank"] = 3
    payload["candidateLines"].append(fourth)
    from app.professional_validation import parse_professional_analysis
    parsed, parse_errors = parse_professional_analysis(__import__("json").dumps(payload, ensure_ascii=False))
    assert parsed is None
    assert parse_errors

    payload["candidateLines"].pop()
    payload["plans"]["white"][0]["description"] = "白方准备进攻。"
    from app.models import ProfessionalAnalysis
    analysis = ProfessionalAnalysis.model_validate(payload)
    errors = validate_professional_analysis(analysis, context, enforce_length=False)
    assert any("准备进攻" in error for error in errors)


@pytest.mark.asyncio
async def test_two_invalid_deepseek_results_retry_once_then_use_safe_analysis() -> None:
    move = professional_review()
    service = ProfessionalAnalysisService(
        api_key="test", base_url="https://example.invalid", model="test", timeout_seconds=1
    )
    service._chat = AsyncMock(
        return_value=ChatResult(
            content='{"not":"the schema"}',
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            elapsed_ms=20,
        )
    )
    result = await service.analyze(move)
    context = build_validation_context(move, result.analysis.complexity)
    assert validate_professional_analysis(result.analysis, context, enforce_length=False) == []
    assert service._chat.await_count == 2
    assert result.usage.attempts == 2
    assert result.usage.total_tokens == 30
    assert result.validation_warnings


def test_complexity_profiles_have_distinct_lengths_and_token_limits() -> None:
    assert LENGTH_RANGES["simple"][1] < LENGTH_RANGES["normal"][0]
    assert LENGTH_RANGES["normal"][1] < LENGTH_RANGES["complex"][0]
    assert PROFESSIONAL_TOKEN_LIMITS["simple"] < PROFESSIONAL_TOKEN_LIMITS["normal"] < PROFESSIONAL_TOKEN_LIMITS["complex"]


def test_professional_api_cache_uses_fact_hash_without_second_service_call(monkeypatch) -> None:
    move = professional_review()
    complexity = compute_professional_complexity(move)
    generated = GeneratedProfessionalAnalysis(
        analysis=build_safe_professional_analysis(move, complexity),
        complexity_reasons=complexity.reasons,
        usage=ProfessionalAnalysisUsage(elapsed_ms=10, attempts=1),
    )

    class FakeProfessionalService:
        def __init__(self):
            self.calls = 0

        async def analyze(self, selected):
            self.calls += 1
            await asyncio.sleep(0)
            return generated

    fake = FakeProfessionalService()
    monkeypatch.setattr(api, "professional_service", fake)
    api.game_cache.clear()
    api.professional_cache.clear()
    api.professional_tasks.clear()
    analysis_id = "professional-cache-test"
    api.game_cache[analysis_id] = [move]
    client = TestClient(api.app)
    first = client.post("/api/professional-analysis", json={"analysis_id": analysis_id, "move_index": 1})
    second = client.post("/api/professional-analysis", json={"analysis_id": analysis_id, "move_index": 1})
    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert fake.calls == 1
